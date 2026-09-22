"""Read-only discovery of installed Hermes profiles.

The Hermes profile field in the UI must name a profile that actually exists:
``hermes -p <name>`` fails hard (exit 1) for an unknown profile, and silently
guessing a name would turn a configuration typo into a failed dispatch.
Discovery is therefore a **guard** as well as a convenience.

Two read-only mechanisms, in order:

1. ``hermes profile list`` — the CLI's own listing (authoritative; also reports
   the ``default`` profile).  Parsed, never re-implemented.
2. A directory scan of the documented profile roots, used only when the CLI
   cannot be run.  It applies Hermes' own "a directory is a profile only when it
   carries an identity marker" rule instead of listing raw directories.

Nothing here reads a secret: profile *names* come from a table and a directory
listing.  No ``.env``, no config file and no credential store is ever opened, and
no profile is created, modified or deleted.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..drivers.hermes import HermesDriver
from ..drivers.process import ProcessRunner, ProcessSpec
from ..drivers.hermes_cli import child_environment

__all__ = [
    "IDENTITY_MARKERS",
    "PROFILE_NAME_RE",
    "ProfileDiscoveryResult",
    "discover_profiles",
    "parse_profile_list",
    "profile_roots",
]

#: A Hermes profile name (also the rule the CLI itself applies).
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Files that mark a directory as a real profile (mirrors the predicate the
#: Hermes CLI uses before it will list, serve or resolve a profile directory).
IDENTITY_MARKERS: tuple[str, ...] = (
    "config.yaml",
    ".env",
    "SOUL.md",
    "profile.yaml",
    "auth.json",
    "state.db",
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_BOX_CHARS = set("─━═-│┃ |+")
_LEAD_MARKERS = "◆●*•○◉○ "


@dataclass(slots=True)
class ProfileDiscoveryResult:
    """Outcome of a discovery attempt — never raises for a missing CLI."""

    ok: bool
    profiles: tuple[str, ...] = ()
    method: str = "none"
    detail: str = ""
    error: str | None = None
    candidates: tuple[str, ...] = field(default=())

    def contains(self, name: str) -> bool:
        return name in self.profiles


def parse_profile_list(output: str) -> list[str]:
    """Extract profile names from ``hermes profile list`` output.

    The listing is a human table: a decorated header, a box-drawing separator,
    an active-profile marker glued to the active row, and warning lines.  Names
    are read **only after the separator**, so prose that merely mentions a
    profile cannot be mistaken for a row, and each candidate is validated against
    :data:`PROFILE_NAME_RE`.  Anything else is discarded rather than guessed at.
    """
    names: list[str] = []
    in_table = False
    for raw_line in _ANSI_RE.sub("", str(output or "")).splitlines():
        line = raw_line.replace("\r", "").strip()
        if not line:
            continue
        if not in_table:
            # The separator row (box-drawing / dashes only) starts the table.
            if len(line) >= 3 and set(line) <= _BOX_CHARS:
                in_table = True
            continue
        if set(line) <= _BOX_CHARS:
            continue
        lowered = line.lower()
        if "profile" in lowered and "model" in lowered and "gateway" in lowered:
            continue  # a repeated header (paged output)
        if line.startswith(("⚠", "!", "Warning", "Note")):
            continue
        token = line.split()[0]
        token = token.lstrip(_LEAD_MARKERS)
        if PROFILE_NAME_RE.match(token):
            names.append(token)
    # De-duplicate, preserving order.
    return list(dict.fromkeys(names))


def profile_roots(environ: dict[str, str] | None = None, extra: list[str] | None = None) -> list[Path]:
    """Candidate Hermes roots, most specific first (read-only).

    Derived from the environment the CLI would itself read, plus the platform
    default.  A root is only *used* when it looks like a Hermes home.
    """
    env = os.environ if environ is None else environ
    roots: list[Path] = []

    home = str(env.get("HERMES_HOME", "")).strip()
    if home:
        candidate = Path(home).expanduser()
        roots.append(candidate.parent.parent if candidate.parent.name == "profiles" else candidate)

    local_appdata = str(env.get("LOCALAPPDATA", "")).strip()
    if local_appdata:
        roots.append(Path(local_appdata) / "hermes")

    roots.append(Path.home() / ".hermes")
    if os.name == "nt":
        roots.append(Path.home() / "AppData" / "Local" / "hermes")

    for value in extra or []:
        roots.append(Path(value).expanduser())

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _scan_root(root: Path) -> list[str]:
    """Profile names in ``root`` per the identity-marker rule (read-only)."""
    names: list[str] = []
    if (root / "config.yaml").is_file() or (root / ".env").is_file():
        names.append("default")
    profiles_dir = root / "profiles"
    if not profiles_dir.is_dir():
        return names
    for entry in sorted(profiles_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue  # staging/hidden dirs are never profiles
        if not PROFILE_NAME_RE.match(entry.name):
            continue
        if any((entry / marker).is_file() for marker in IDENTITY_MARKERS):
            names.append(entry.name)
    return names


def discover_profiles(
    *,
    runner: ProcessRunner | None = None,
    executable: str | None = None,
    timeout_s: float = 60.0,
    roots: list[str] | None = None,
    extra_roots: list[str] | None = None,
    environ: dict[str, str] | None = None,
) -> ProfileDiscoveryResult:
    """Discover installed Hermes profiles, read-only.

    The CLI listing is tried first; when it cannot be run or cannot be parsed,
    the directory scan of :func:`profile_roots` provides names.  A failure of
    both is reported as ``ok=False`` with the reason — callers must treat that
    as "unknown", not as "no profiles".

    ``roots`` replaces the derived candidate roots (used by tests and by callers
    that already know the Hermes home); ``extra_roots`` is appended to them.
    """
    exe = executable or HermesDriver.resolve_executable()
    errors: list[str] = []

    if exe and runner is not None:
        try:
            process = runner.run(
                ProcessSpec(
                    argv=[exe, "profile", "list"],
                    env=child_environment(environ),
                    timeout_s=timeout_s,
                    no_window=True,
                )
            )
            if process.ok:
                names = parse_profile_list(process.stdout)
                if names:
                    return ProfileDiscoveryResult(
                        ok=True,
                        profiles=tuple(names),
                        method="cli",
                        detail=f"hermes profile list ({len(names)} profiles)",
                    )
                errors.append("hermes profile list returned no parseable rows")
            else:
                errors.append(
                    f"hermes profile list exited with {process.exit_code}: "
                    f"{process.stderr.strip()[:400]}"
                )
        except Exception as exc:  # noqa: BLE001 - discovery must never raise
            errors.append(f"hermes profile list failed: {type(exc).__name__}: {exc}")

    scanned: list[str] = []
    used_roots: list[str] = []
    candidates = [Path(value).expanduser() for value in roots] if roots is not None else profile_roots(environ, extra_roots)
    for root in candidates:
        try:
            if not root.is_dir():
                continue
            found = _scan_root(root)
        except OSError as exc:
            errors.append(f"{root}: {exc}")
            continue
        if found:
            used_roots.append(str(root))
            scanned.extend(found)

    scanned = list(dict.fromkeys(scanned))
    if scanned:
        return ProfileDiscoveryResult(
            ok=True,
            profiles=tuple(scanned),
            method="directory-scan",
            detail="scanned profile roots: " + ", ".join(used_roots),
            error="; ".join(errors) or None,
            candidates=tuple(used_roots),
        )

    return ProfileDiscoveryResult(
        ok=False,
        profiles=(),
        method="none",
        detail="no Hermes profiles could be discovered",
        error="; ".join(errors) or "no Hermes installation found",
    )


def find_executable() -> str | None:
    """Absolute path of the Hermes launcher on PATH (read-only)."""
    for name in ("hermes", "hermes.exe", "hermes.cmd", "hermes.bat"):
        found = shutil.which(name)
        if found:
            return found
    return None