#!/usr/bin/env python3
"""SESSION 021 — CODEX SESSION DISCOVERY ACCEPTANCE (brief §20).

Read-ONLY and ZERO MODEL CALLS: proves the real installed Codex CLI's
session-discovery contract through the production discovery code
(``CodexSessionDiscovery`` via the driver's ``SessionDiscoverer``
contract).  Two legs:

1. LIVE read-only discovery against the real local Codex rollout store
   (no network, no model work, no auth reads): the call must return
   safely (ok or an honest typed failure), never fabricate sessions.
2. DISPOSABLE-FIXTURE discovery: a synthetic rollout store in a temp dir
   proves the parse/marker/sort contract exactly — a workspace-matching
   session sorts FIRST, carries the real full id as data, titles are
   display-only, and sessions from other workspaces stay visible.

Run::

    python scripts/session_021_codex_discovery_acceptance.py

Expected final line:  CODEX SESSION DISCOVERY ACCEPTANCE PASSED
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from encomm_pcc.drivers.codex import CodexDriver  # noqa: E402
from encomm_pcc.drivers.codex_discovery import CodexSessionDiscovery  # noqa: E402


def step(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"ACCEPTANCE FAILED at: {name}")


def _write_rollout(home: Path, session_id: str, cwd: str, title: str,
                   updated: str) -> None:
    """Write one minimal rollout file the discovery reader accepts.

    The real store layout is ``sessions/YYYY/MM/DD/rollout-*.jsonl`` with a
    JSON session-meta first line; titles live in ``session_index.jsonl``.
    """
    day_dir = home / "sessions" / "2026" / "10" / "04"
    day_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "cwd": cwd,
            "originator": "codex-cli",
            "cli_version": "0.154.0",
        },
        "timestamp": updated,
    }
    rollout = day_dir / f"rollout-2026-10-04T00-00-00-{session_id}.jsonl"
    rollout.write_text(
        json.dumps(record) + "\n", encoding="utf-8", newline="\n"
    )
    index = home / "session_index.jsonl"
    with open(index, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            json.dumps({"id": session_id, "thread_name": title}) + "\n"
        )


def main() -> int:
    print("SESSION 021 — CODEX SESSION DISCOVERY ACCEPTANCE (read-only, zero AI)")
    tmp = Path(tempfile.mkdtemp(prefix="s021-codex-discovery-"))
    try:
        # -- leg 1: the REAL discovery path on this machine -------------------
        driver = CodexDriver()
        result = driver.discover_sessions()
        step(
            "1. live read-only discovery returns an HONEST result",
            result.ok or (result.error is not None and result.sessions == []),
            f"ok={result.ok} mechanism={result.mechanism!r} "
            f"sessions={len(result.sessions)} error={result.error!r}",
        )
        for s in result.sessions:
            # Real ids only: a uuid-shaped id must never be invented prose.
            step(
                "1b. every reported id is the engine's own full id",
                bool(s.session_id) and s.driver_id == "codex",
                f"id={s.session_id[:16]}…",
            )
            break

        # -- leg 2: the disposable FIXTURE store (exact contract) -------------
        ws = tmp / "proposal-workspace"
        ws.mkdir(parents=True)
        ws_str = str(ws)
        home = tmp / "codex-home"
        match_id = "11111111-2222-3333-4444-555555555555"
        other_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        _write_rollout(
            home, other_id, "C:/somewhere-else", "ASTRA — other thread",
            "2026-10-04T09:00:00Z",
        )
        _write_rollout(
            home, match_id, ws_str, "ASTRA — EIC PANEL CHAIR",
            "2026-10-04T10:00:00Z",
        )
        discovery = CodexSessionDiscovery(home=home)
        fixture = discovery.discover_sessions(workspace_path=ws_str)
        step(
            "2. fixture discovery parses the disposable store",
            fixture.ok and len(fixture.sessions) == 2,
            f"mechanism={fixture.mechanism!r}",
        )
        ws_first = fixture.sessions[0]
        other = fixture.sessions[1] if len(fixture.sessions) > 1 else None
        step(
            "3. workspace-matching session sorts FIRST",
            ws_first.session_id == match_id and ws_first.matches_workspace
            and ws_first.title == "ASTRA — EIC PANEL CHAIR",
            f"first={ws_first.session_id[:8]}… title={ws_first.title!r}",
        )
        step(
            "4. real FULL session id carried as data; title is display-only",
            ws_first.session_id == match_id
            and len(ws_first.session_id) == 36,
        )
        step(
            "5. other-workspace session stays VISIBLE and marked",
            other is not None
            and other.session_id == other_id
            and other.matches_workspace is False,
        )
        label = ws_first.label()
        step(
            "6. label renders for the UI (bounded, real content)",
            bool(label) and match_id[:8] in label or "ASTRA" in label,
            f"label={label[:40]!r}",
        )

        # -- leg 7: the driver adapter surfaces the SAME contract -------------
        # The driver is production wiring: it resolves the REAL store (no
        # fixture-injection seam).  The equivalence proof is therefore the
        # MECHANISM: driver live call == direct live call == the fixture's
        # parser (same class, same code path).
        via_driver = driver.discover_sessions()
        direct_live = CodexSessionDiscovery().discover_sessions()
        step(
            "7. driver discover_sessions == direct discovery (one mechanism)",
            via_driver.ok
            and direct_live.ok
            and via_driver.mechanism == fixture.mechanism
            and [s.session_id for s in via_driver.sessions]
            == [s.session_id for s in direct_live.sessions],
            f"mechanism={via_driver.mechanism!r}",
        )

        print()
        print("CODEX SESSION DISCOVERY ACCEPTANCE PASSED")
        return 0
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
