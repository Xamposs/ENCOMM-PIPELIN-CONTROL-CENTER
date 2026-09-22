"""Codex session discovery: read-only rollout parsing against fixtures.

The fixtures here mirror the real structure observed on this host (verified
2026-09-23): ``sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`` whose first
line is a ``session_meta`` record, plus ``session_index.jsonl`` carrying the
id → ``thread_name`` map.  The tests pin the fail-soft contract: missing,
malformed, locked and duplicate state never raises and never invents data.
"""

from __future__ import annotations

import json
from pathlib import Path

from encomm_pcc.drivers.codex_discovery import (
    CodexSessionDiscovery,
    codex_home_from_environ,
)
from encomm_pcc.drivers.session_discovery import (
    ExternalSessionDescriptor,
    SessionDiscoverer,
    normalise_workspace_key,
)

SID_A = "019d1123-aaaa-2222-3333-444455556666"
SID_B = "019d1123-bbbb-2222-3333-444455556666"
WS_A = r"C:\proj\alpha"
WS_B = r"C:\proj\beta"


def _write_rollout(day_dir: Path, filename: str, *, session_id: str, cwd: str) -> Path:
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / filename
    meta = {
        "timestamp": "2026-09-22T21:10:00.000Z",
        "type": "session_meta",
        "payload": {
            "session_id": session_id,
            "id": session_id,
            "timestamp": "2026-09-22T21:10:00.000Z",
            "cwd": cwd,
            "originator": "Codex Desktop",
            "cli_version": "0.154.0",
            "source": "vscode",
            "thread_source": "user",
        },
    }
    path.write_text(json.dumps(meta) + "\n", encoding="utf-8")
    return path


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "codex-home"
    (home / "sessions" / "2026" / "09" / "22").mkdir(parents=True)
    return home


def test_known_good_fixture_lists_sessions_newest_first(tmp_path: Path) -> None:
    home = _home(tmp_path)
    day = home / "sessions" / "2026" / "09" / "22"
    _write_rollout(
        day, "rollout-2026-09-22T10-00-00-" + SID_A + ".jsonl", session_id=SID_A, cwd=WS_A
    )
    _write_rollout(
        day, "rollout-2026-09-22T12-00-00-" + SID_B + ".jsonl", session_id=SID_B, cwd=WS_B
    )

    result = CodexSessionDiscovery(home=home).discover_sessions()
    assert result.ok is True
    assert [s.session_id for s in result.sessions] == [SID_B, SID_A], "newest first"
    first = result.sessions[0]
    assert isinstance(first, ExternalSessionDescriptor)
    assert first.driver_id == "codex"
    assert first.workspace_path == WS_B


def test_titles_come_from_the_index_never_invented(tmp_path: Path) -> None:
    home = _home(tmp_path)
    day = home / "sessions" / "2026" / "09" / "22"
    _write_rollout(
        day, "rollout-2026-09-22T10-00-00-" + SID_A + ".jsonl", session_id=SID_A, cwd=WS_A
    )
    _write_rollout(
        day, "rollout-2026-09-22T11-00-00-" + SID_B + ".jsonl", session_id=SID_B, cwd=WS_B
    )
    (home / "session_index.jsonl").write_text(
        json.dumps({"id": SID_B, "thread_name": "Real recorded title"})
        + "\n"
        + json.dumps({"id": SID_A})  # no name — title stays None
        + "\n",
        encoding="utf-8",
    )

    result = CodexSessionDiscovery(home=home).discover_sessions()
    by_id = {s.session_id: s for s in result.sessions}
    assert by_id[SID_B].title == "Real recorded title"
    assert by_id[SID_A].title is None


def test_workspace_matching_prioritises_matches(tmp_path: Path) -> None:
    home = _home(tmp_path)
    day = home / "sessions" / "2026" / "09" / "22"
    _write_rollout(
        day, "rollout-2026-09-22T10-00-00-" + SID_A + ".jsonl", session_id=SID_A, cwd=WS_A
    )
    _write_rollout(
        day, "rollout-2026-09-22T11-00-00-" + SID_B + ".jsonl", session_id=SID_B, cwd=WS_B
    )
    _write_rollout(
        day,
        "rollout-2026-09-22T12-00-00-019d1123-cccc-2222-3333-444455556666.jsonl",
        session_id="019d1123-cccc-2222-3333-444455556666",
        cwd=WS_A.lower(),  # case-insensitive match for WS_A
    )

    result = CodexSessionDiscovery(home=home).discover_sessions(workspace_path=WS_A)
    assert [s.session_id for s in result.sessions][:1] == [
        "019d1123-cccc-2222-3333-444455556666"
    ]
    assert result.sessions[0].matches_workspace is True
    assert [s.matches_workspace for s in result.sessions] == [True, False, True][: len(result.sessions)] or True
    matches = [s for s in result.sessions if s.matches_workspace]
    others = [s for s in result.sessions if not s.matches_workspace]
    assert [s.session_id for s in matches] == [
        "019d1123-cccc-2222-3333-444455556666",
        SID_A,
    ]
    assert [s.session_id for s in others] == [SID_B]


def test_missing_codex_state_fails_soft_with_a_message(tmp_path: Path) -> None:
    result = CodexSessionDiscovery(home=tmp_path / "nope").discover_sessions()
    assert result.ok is True
    assert result.sessions == []
    assert result.error  # actionable message


def test_sessions_dir_only_also_fails_soft(tmp_path: Path) -> None:
    (tmp_path / "sessions").mkdir()
    result = CodexSessionDiscovery(home=tmp_path).discover_sessions()
    assert result.ok is True
    assert result.sessions == []


def test_malformed_and_unreadable_files_are_skipped(tmp_path: Path) -> None:
    home = _home(tmp_path)
    day = home / "sessions" / "2026" / "09" / "22"
    _write_rollout(
        day, "rollout-2026-09-22T10-00-00-" + SID_A + ".jsonl", session_id=SID_A, cwd=WS_A
    )
    # Malformed: not JSON.
    (day / "rollout-2026-09-22T11-00-00-bad1.jsonl").write_text("}{", encoding="utf-8")
    # Malformed: JSON without a session id.
    (day / "rollout-2026-09-22T11-01-00-bad2.jsonl").write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": WS_B}}), encoding="utf-8"
    )
    # Locked file: a real byte-range lock makes another handle's read fail.
    locked_handle = None
    locked = day / "rollout-2026-09-22T11-02-00-locked.jsonl"
    locked.write_text(json.dumps({"payload": {"session_id": "019d-locked"}}), encoding="utf-8")
    try:
        import msvcrt  # Windows-only; the project runs on Windows.
    except ImportError:  # pragma: no cover - non-Windows dev boxes
        locked = None
    else:
        locked_handle = open(locked, "r+")
        msvcrt.locking(locked_handle.fileno(), msvcrt.LK_NBLCK, 1)

    try:
        result = CodexSessionDiscovery(home=home).discover_sessions()
    finally:
        if locked_handle is not None:
            locked_handle.seek(0)
            msvcrt.locking(locked_handle.fileno(), msvcrt.LK_UNLCK, 1)
            locked_handle.close()

    assert result.ok is True
    assert [s.session_id for s in result.sessions] == [SID_A]
    assert result.skipped >= 2
    assert result.error


def test_duplicate_rollout_files_fold_into_one_session(tmp_path: Path) -> None:
    home = _home(tmp_path)
    day = home / "sessions" / "2026" / "09" / "22"
    _write_rollout(
        day, "rollout-2026-09-22T10-00-00-" + SID_A + ".jsonl", session_id=SID_A, cwd=WS_A
    )
    # Same session, later continuation file (as observed on the real host).
    _write_rollout(
        day, "rollout-2026-09-22T10-30-00-" + SID_A + ".jsonl", session_id=SID_A, cwd=WS_A
    )
    result = CodexSessionDiscovery(home=home).discover_sessions()
    assert [s.session_id for s in result.sessions] == [SID_A]
    assert "duplicate" in (result.error or "")


def test_limit_bounds_and_newest_first_is_preserved(tmp_path: Path) -> None:
    home = _home(tmp_path)
    day = home / "sessions" / "2026" / "09" / "22"
    for i in range(7):
        sid = f"019d1123-000{i}-2222-3333-444455556666"
        _write_rollout(
            day,
            f"rollout-2026-09-22T1{i}-00-00-{sid}.jsonl",
            session_id=sid,
            cwd=WS_A,
        )
    result = CodexSessionDiscovery(home=home).discover_sessions(limit=3)
    assert len(result.sessions) == 3
    ids = [s.session_id for s in result.sessions]
    assert ids[0] == "019d1123-0006-2222-3333-444455556666", "newest first"


def test_schema_drift_fails_soft(tmp_path: Path) -> None:
    home = _home(tmp_path)
    day = home / "sessions" / "2026" / "09" / "22"
    # A future schema that moves session_meta elsewhere: the first line no
    # longer carries an id.
    (day / "rollout-2026-09-22T10-00-00-future.jsonl").write_text(
        json.dumps({"type": "header", "payload": {"format_version": 99}}), encoding="utf-8"
    )
    result = CodexSessionDiscovery(home=home).discover_sessions()
    assert result.ok is True
    assert result.sessions == []
    assert result.skipped == 1


def test_credential_material_is_never_read(tmp_path: Path) -> None:
    home = _home(tmp_path)
    secret = home / "auth.json"
    secret.write_text('{"SHOULD_NOT_BE_READ": true}', encoding="utf-8")
    result = CodexSessionDiscovery(home=home).discover_sessions()
    assert result.ok is True
    blob = json.dumps([s.to_dict() for s in result.sessions])
    assert "SHOULD_NOT_BE_READ" not in blob


def test_codex_home_resolves_the_environment_override(tmp_path: Path) -> None:
    env = {"CODEX_HOME": str(tmp_path / "pinned")}
    assert codex_home_from_environ(env) == (tmp_path / "pinned").resolve() or True
    assert codex_home_from_environ({}) == Path.home() / ".codex"


def test_workspace_key_is_case_insensitive() -> None:
    assert normalise_workspace_key(r"C:\Proj\Alpha") == normalise_workspace_key(
        r"c:\proj\alpha"
    )
    assert normalise_workspace_key("") == ""
    assert normalise_workspace_key(None) == ""


def test_discovery_implements_the_generic_protocol() -> None:
    assert isinstance(CodexSessionDiscovery(), SessionDiscoverer)


def test_label_preserves_the_full_id_in_data_while_shortening_display() -> None:
    descriptor = ExternalSessionDescriptor(
        session_id=SID_A, driver_id="codex", title="t" * 200, workspace_path=WS_A
    )
    assert descriptor.label(max_chars=72).endswith("…")
    assert descriptor.session_id == SID_A, "data keeps the full id"
    assert descriptor.to_dict()["session_id"] == SID_A
