"""READ-ONLY discovery of existing Codex sessions on this machine.

No machine-readable ``codex exec list`` exists in the installed CLI
(``codex exec --help``: ``resume``, ``fork``, ``review``, ``help`` — no
listing subcommand), so discovery reads the **local rollout state** the CLI
itself writes, in the read-only discipline the brief mandates (§11):

* reads only ``$CODEX_HOME`` (default ``~/.codex``), honouring the
  ``CODEX_HOME`` environment variable exactly as the CLI does;
* scans ``sessions/YYYY/MM/DD/rollout-*.jsonl`` — each file's **first JSON
  line** is a ``session_meta`` record carrying the real session id, cwd,
  originator, CLI version and source; nothing else in the file is parsed;
* titles come from ``session_index.jsonl`` (the CLI's own
  id→``thread_name`` map) when present — titles are never invented;
* never reads ``auth.json``, ``config.toml`` or any credential material;
* tolerates missing files, unreadable (locked) files, malformed lines and
  schema drift: bad entries are counted in ``skipped`` and the rest is
  returned;
* returns at most ``limit`` descriptors, newest file first (filename
  timestamps sort chronologically);
* never writes, moves, locks or deletes anything.

The Codex-internal filename/schema knowledge is isolated in this module; the
rest of PCC sees only :class:`ExternalSessionDescriptor` rows.  A schema that
changes shape fails soft (empty/partial result), never raises through the UI.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .session_discovery import (
    DEFAULT_DISCOVERY_LIMIT,
    ExternalSessionDescriptor,
    SessionDiscoveryResult,
    normalise_workspace_key,
)

__all__ = ["CodexSessionDiscovery", "codex_home_from_environ"]

_DRIVER_ID = "codex"
_MECHANISM = "codex-rollouts"

#: Upper bound of files scanned per discovery pass (defence against a huge
#: state directory; the newest files win because the walk is newest-first).
_MAX_FILES_SCANNED = 400

#: Hard per-line cap for the meta/index JSON reads.  A first line longer than
#: this is treated as malformed rather than trusted into memory.
_MAX_META_LINE_CHARS = 400_000


@dataclass(slots=True)
class _MetaInfo:
    session_id: str
    cwd: str | None
    title: str | None
    updated_at: str | None
    source: str | None
    originator: str | None


def codex_home_from_environ(environ: dict[str, str] | None = None) -> Path:
    """Resolve the Codex home the same way the CLI does (default ``~/.codex``)."""
    source = environ if environ is not None else os.environ
    override = source.get("CODEX_HOME")
    if override and str(override).strip():
        return Path(str(override).strip()).expanduser()
    return Path.home() / ".codex"


class CodexSessionDiscovery:
    """Read-only Codex session listing behind the generic discovery contract."""

    driver_id = _DRIVER_ID

    def __init__(self, *, home: Path | None = None) -> None:
        # An explicit home is for tests (fixtures); production resolves the
        # environment per call so a CODEX_HOME set after construction wins.
        self._home_override = home

    # -- generic contract -------------------------------------------------
    def discover_sessions(
        self,
        *,
        workspace_path: str | None = None,
        limit: int = DEFAULT_DISCOVERY_LIMIT,
    ) -> SessionDiscoveryResult:
        home = self._resolve_home()
        if home is None or not home.is_dir():
            return SessionDiscoveryResult(
                ok=True,
                driver_id=_DRIVER_ID,
                mechanism=_MECHANISM,
                error="Codex state directory not found (no sessions discovered).",
            )
        sessions_dir = home / "sessions"
        if not sessions_dir.is_dir():
            return SessionDiscoveryResult(
                ok=True,
                driver_id=_DRIVER_ID,
                mechanism=_MECHANISM,
                error="Codex 'sessions' directory not found (no sessions discovered).",
            )

        titles = self._load_titles(home / "session_index.jsonl")
        wanted_key = normalise_workspace_key(workspace_path)

        rollout_files: list[Path] = []
        for day_dir in _walk_day_dirs_newest_first(sessions_dir):
            try:
                rollout_files.extend(
                    p for p in day_dir.iterdir() if p.is_file() and p.name.startswith("rollout-")
                )
            except OSError:
                continue
        # Filenames embed a timestamp: lexical sort == chronological.
        rollout_files.sort(key=lambda p: p.name, reverse=True)

        descriptors: list[ExternalSessionDescriptor] = []
        skipped = 0
        duplicates = 0
        scanned = 0
        note: str | None = None
        seen_ids: set[str] = set()
        for path in rollout_files:
            if len(descriptors) >= max(1, int(limit)):
                break
            if scanned >= _MAX_FILES_SCANNED:
                note = f"Stopped after scanning {scanned} rollout files."
                break
            scanned += 1
            meta = self._read_meta(path)
            if meta is None or not meta.session_id:
                skipped += 1
                continue
            # One real session can own several rollout files (continuations,
            # compactions).  Files are newest-first, so the first file seen for
            # an id carries the newest state; the rest are duplicates.
            if meta.session_id in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(meta.session_id)
            matches = bool(
                wanted_key
                and meta.cwd
                and normalise_workspace_key(meta.cwd) == wanted_key
            )
            meta.title = titles.get(meta.session_id)
            descriptors.append(
                ExternalSessionDescriptor(
                    session_id=meta.session_id,
                    driver_id=_DRIVER_ID,
                    title=meta.title,
                    workspace_path=meta.cwd,
                    updated_at=meta.updated_at,
                    source=meta.source or meta.originator,
                    matches_workspace=matches,
                    metadata={"rollout_file": path.name},
                )
            )
        if note is None and (skipped or duplicates):
            note = (
                f"{skipped} rollout file(s) skipped (unreadable or malformed); "
                f"{duplicates} duplicate rollout file(s) folded into their session."
            )

        # Workspace matches first (still newest-first within each group).
        if wanted_key:
            descriptors.sort(key=lambda d: not d.matches_workspace)

        return SessionDiscoveryResult(
            ok=True,
            driver_id=_DRIVER_ID,
            sessions=descriptors,
            mechanism=_MECHANISM,
            error=note,
            skipped=skipped,
        )

    # -- internals ----------------------------------------------------------
    def _resolve_home(self) -> Path | None:
        if self._home_override is not None:
            return self._home_override
        try:
            return codex_home_from_environ()
        except Exception:  # pragma: no cover - defensive
            return None

    def _load_titles(self, index_path: Path) -> dict[str, str]:
        """Read ``session_index.jsonl`` (id → thread_name). Missing = no titles."""
        titles: dict[str, str] = {}
        if not index_path.is_file():
            return titles
        try:
            with index_path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    session_id = str(record.get("id") or "").strip()
                    name = record.get("thread_name")
                    if session_id and isinstance(name, str) and name.strip():
                        titles[session_id] = name.strip()
        except OSError:
            return {}
        return titles

    def _read_meta(self, path: Path) -> _MetaInfo | None:
        """Read ONLY the first JSON line of a rollout file.

        Locked/unreadable files and non-JSON first lines are reported as
        ``None`` (counted as skipped by the caller) — never a crash.
        """
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                first = fh.readline(_MAX_META_LINE_CHARS)
        except OSError:
            return None
        line = (first or "").strip()
        if not line or len(line) >= _MAX_META_LINE_CHARS:
            return None
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            return None
        if not isinstance(record, dict):
            return None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            payload = record
        session_id = str(payload.get("session_id") or payload.get("id") or "").strip()
        if not session_id:
            return None
        cwd = payload.get("cwd")
        timestamp = payload.get("timestamp") or record.get("timestamp")
        source = payload.get("source")
        originator = payload.get("originator")
        return _MetaInfo(
            session_id=session_id,
            cwd=str(cwd) if isinstance(cwd, str) and cwd.strip() else None,
            title=None,  # filled by the caller from the index
            updated_at=str(timestamp) if isinstance(timestamp, str) else None,
            source=str(source) if isinstance(source, str) and source.strip() else None,
            originator=(
                str(originator)
                if isinstance(originator, str) and originator.strip()
                else None
            ),
        )


def _walk_day_dirs_newest_first(sessions_dir: Path) -> list[Path]:
    """Yield ``YYYY/MM/DD`` leaf directories newest-first (name sort)."""
    day_dirs: list[Path] = []
    try:
        for year in sessions_dir.iterdir():
            if not year.is_dir():
                continue
            for month in year.iterdir():
                if not month.is_dir():
                    continue
                for day in month.iterdir():
                    if day.is_dir():
                        day_dirs.append(day)
    except OSError:
        return []
    day_dirs.sort(key=lambda p: str(p), reverse=True)
    return day_dirs
