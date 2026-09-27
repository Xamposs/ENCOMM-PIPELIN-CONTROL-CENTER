"""READ-ONLY discovery of existing Hermes sessions, scoped to one profile.

Session 009 brief (§12–§15): Hermes had profile discovery and resume support
but no external session selector.  This module implements the generic
:class:`~encomm_pcc.drivers.session_discovery.SessionDiscoverer` contract for
the installed Hermes CLI with the same discipline as the Codex discoverer:

* **Priority A — the official CLI listing.**  The installed CLI exposes
  ``hermes [-p <profile>] sessions list [--limit N] [--workspace NEEDLE]``.
  Its human table is parsed, never re-implemented: the session id is the LAST
  whitespace token of every data row (verified against the installed build's
  four column layouts); header/rule/``No sessions found.``/truncation lines
  never carry a valid id token, so they are discarded structurally — not by
  matching their prose.
* **Priority B — read-only local session store.**  When the CLI cannot be run
  (missing, timeout, non-zero exit), discovery reads the profile's own
  ``state.db`` SQLite session store — ``mode=ro`` so it can never create or
  alter engine state.  Only session metadata columns are selected (id, title,
  cwd, timestamps, source); message bodies, prompts and any credential file
  (``auth.json``, ``.env``) are never read.
* **Profile scoping is structural.**  Priority A passes ``-p <profile>`` so
  the CLI itself lists only that profile's sessions; Priority B resolves the
  profile's own home directory.  A session from another profile cannot appear
  in the result by construction.
* **Fail soft, bounded, newest first, never fabricated.**  Titles come from
  the engine's own records or stay ``None``; a locked/malformed store yields
  an empty (or partial) result with an actionable message, never an exception
  through the UI.
"""

from __future__ import annotations

import dataclasses
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .hermes import HermesDriver
from .hermes_cli import child_environment
from .process import ProcessRunner, ProcessSpec
from .session_discovery import (
    DEFAULT_DISCOVERY_LIMIT,
    ExternalSessionDescriptor,
    SessionDiscoveryResult,
    normalise_workspace_key,
)

__all__ = [
    "HermesSessionDiscovery",
    "parse_sessions_list",
    "profile_session_db_path",
]

_DRIVER_ID = "hermes"
_MECHANISM_CLI = "hermes-sessions-list"
_MECHANISM_DB = "hermes-state-db"

#: Bounded CLI wait; a listing must never hang the UI.
_CLI_TIMEOUT_S = 60.0

#: Session-id shape accepted from the CLI table (the CLI's own ids are
#: ``YYYYMMDD_HHMMSS_hexhex``; the regex stays permissive but excludes prose).
#: The CLI's own id shape (timestamp + hex suffix). Strict on purpose:
#: prose words like "profiles" must never pass as a session id.
_SESSION_ID_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-fA-F]+$")

#: Lines that are table decoration, never data.
_RULE_CHARS = set("─━═-=— ")


@dataclass(slots=True)
class _CliListing:
    rows: list[dict]
    has_more: bool


def parse_sessions_list(output: str) -> _CliListing:
    """Parse ``hermes sessions list`` output into bounded row dicts.

    Structural rules (verified against the installed build's four layouts):
    the session id is the LAST token of a data row; the header/rule lines and
    the ``No sessions found.`` / ``use --limit N to see more`` notices never
    end in a valid id token.  Order is preserved (the CLI lists newest first).
    ``has_more`` is set from the CLI's own truncation notice.
    """
    rows: list[dict] = []
    has_more = False
    for raw_line in str(output or "").splitlines():
        line = raw_line.replace("\r", "").strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("use --limit") and "to see more" in lowered:
            has_more = True
            continue
        if set(line) <= _RULE_CHARS:
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue
        candidate = tokens[-1]
        if not _SESSION_ID_RE.match(candidate):
            continue  # header ('ID'), 'No sessions found.', prose
        rows.append({"session_id": candidate, "raw": line})
    return _CliListing(rows=rows, has_more=has_more)


def profile_home(
    profile: str,
    *,
    environ: dict[str, str] | None = None,
) -> Path | None:
    """Resolve a profile's home directory the way the CLI does (read-only).

    ``default`` is the Hermes root itself; any other profile lives under
    ``<root>/profiles/<name>``.  The candidate roots mirror
    :func:`encomm_pcc.core.hermes_profiles.profile_roots`.  Returns ``None``
    when no candidate exists.
    """
    from ..core.hermes_profiles import profile_roots

    name = str(profile or "default").strip() or "default"
    env = os.environ if environ is None else environ
    env_dict = dict(env)
    for root in profile_roots(env_dict):
        candidate = root if name == "default" else root / "profiles" / name
        if candidate.is_dir():
            return candidate
    return None


def profile_session_db_path(
    profile: str,
    *,
    environ: dict[str, str] | None = None,
) -> Path | None:
    """The profile's own session store, or ``None`` when it does not exist."""
    home = profile_home(profile, environ=environ)
    if home is None:
        return None
    db_path = home / "state.db"
    return db_path if db_path.is_file() else None


class HermesSessionDiscovery:
    """Profile-scoped Hermes session listing behind the generic contract."""

    driver_id = _DRIVER_ID

    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        executable: str | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        # ``runner=None`` means the driver's injected runner (production) —
        # constructed lazily so a plain ``HermesSessionDiscovery()`` never
        # silently loses the real runner.
        self._runner = runner
        self._executable = executable
        self._environ = environ

    # -- generic contract --------------------------------------------------
    def discover_sessions(
        self,
        *,
        profile: str | None = None,
        workspace_path: str | None = None,
        limit: int = DEFAULT_DISCOVERY_LIMIT,
    ) -> SessionDiscoveryResult:
        """List one profile's sessions (newest first, bounded, read-only).

        ``profile`` is REQUIRED for meaningful results: without it the CLI
        path lists the default profile only.  Callers pass the role's
        configured ``project_profile`` so the dropdown can never show another
        profile's sessions (Session 009 brief §15).
        """
        wanted_key = normalise_workspace_key(workspace_path)
        profile_name = str(profile or "").strip()

        cli_result = self._discover_via_cli(
            profile=profile_name, workspace_path=workspace_path, limit=limit
        )
        if cli_result is not None:
            return self._mark_matches(cli_result, wanted_key)

        db_result = self._discover_via_store(
            profile=profile_name, wanted_key=wanted_key, limit=limit
        )
        return self._mark_matches(db_result, wanted_key)

    # -- priority A: the official CLI listing ------------------------------
    def _discover_via_cli(
        self, *, profile: str, workspace_path: str | None, limit: int
    ) -> SessionDiscoveryResult | None:
        executable = self._executable or HermesDriver.resolve_executable()
        runner = self._resolve_runner()
        if not executable or runner is None:
            return None
        argv = [executable]
        if profile:
            argv += ["-p", profile]
        argv += ["sessions", "list", "--limit", str(max(1, int(limit)))]
        if workspace_path:
            argv += ["--workspace", str(workspace_path)]
        try:
            process = runner.run(
                ProcessSpec(
                    argv=argv,
                    env=child_environment(self._environ),
                    timeout_s=_CLI_TIMEOUT_S,
                    no_window=True,
                )
            )
        except Exception:  # noqa: BLE001 - discovery must never raise
            return None
        if process.timed_out or process.exit_code != 0:
            return None
        listing = parse_sessions_list(process.stdout)
        sessions = [
            ExternalSessionDescriptor(
                session_id=row["session_id"],
                driver_id=_DRIVER_ID,
                title=None,  # the CLI table's title column is not machine-safe; never invented
                workspace_path=None,
                updated_at=None,
                source="cli",
                metadata={"profile": profile or "default"},
            )
            for row in listing.rows[: max(1, int(limit))]
        ]
        return SessionDiscoveryResult(
            ok=True,
            driver_id=_DRIVER_ID,
            sessions=sessions,
            mechanism=_MECHANISM_CLI,
            error=(
                "listing may be truncated (the CLI reported more sessions)"
                if listing.has_more
                else None
            )
            if listing.has_more
            else None,
        )

    # -- priority B: the profile's own session store ------------------------
    def _discover_via_store(
        self, *, profile: str, wanted_key: str, limit: int
    ) -> SessionDiscoveryResult:
        db_path = profile_session_db_path(profile or "default", environ=self._environ)
        if db_path is None:
            return SessionDiscoveryResult(
                ok=False,
                driver_id=_DRIVER_ID,
                mechanism=_MECHANISM_DB,
                error=(
                    "No session store found for profile "
                    f"'{profile or 'default'}' (no sessions discovered)."
                ),
            )
        # mode=ro: the store can never be created or altered from here.
        uri = "file:" + str(db_path).replace("\\", "/").replace("?", "%3f").replace("#", "%23")
        uri += "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
            try:
                cursor = connection.execute(
                    "SELECT id, title, cwd, last_activity_at, source "
                    "FROM sessions WHERE archived = 0 AND hidden = 0 "
                    "ORDER BY COALESCE(last_activity_at, started_at) DESC "
                    "LIMIT ?",
                    (max(1, int(limit)),),
                )
                rows = cursor.fetchall()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            return SessionDiscoveryResult(
                ok=False,
                driver_id=_DRIVER_ID,
                mechanism=_MECHANISM_DB,
                error=f"Session store unreadable (fail-soft): {exc}",
            )
        sessions = [
            ExternalSessionDescriptor(
                session_id=str(row[0]),
                driver_id=_DRIVER_ID,
                title=(str(row[1]) if row[1] else None),
                workspace_path=(str(row[2]) if row[2] else None),
                updated_at=(str(row[3]) if row[3] else None),
                source=(str(row[4]) if row[4] else None),
                metadata={"profile": profile or "default", "store": str(db_path)},
            )
            for row in rows
        ]
        return SessionDiscoveryResult(
            ok=True,
            driver_id=_DRIVER_ID,
            sessions=sessions,
            mechanism=_MECHANISM_DB,
        )

    # -- helpers -------------------------------------------------------------
    def _resolve_runner(self) -> ProcessRunner | None:
        if self._runner is not None:
            return self._runner
        try:
            return HermesDriver()._runner  # noqa: SLF001 - the driver's own injected runner
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _mark_matches(
        result: SessionDiscoveryResult, wanted_key: str
    ) -> SessionDiscoveryResult:
        if wanted_key:
            for descriptor in result.sessions:
                matches = (
                    normalise_workspace_key(descriptor.workspace_path) == wanted_key
                )
                object.__setattr__(descriptor, "matches_workspace", matches)
        return result


def _json_or_none(text: str):  # pragma: no cover - reserved for future index reads
    try:
        import json

        return json.loads(text)
    except (ValueError, TypeError):
        return None
