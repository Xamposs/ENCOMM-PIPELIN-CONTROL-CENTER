#!/usr/bin/env python3
"""Session 008 — ONE real Generic CLI call against a real third-party AI CLI.

Proves the full PCC driver abstraction end-to-end:

    GenericCliConfig → GenericCliDriver → ProcessSpec → SubprocessRunner → opencode

Rules honoured (brief §16/§17):
- uses the ALREADY INSTALLED third-party CLI (opencode) — nothing is installed
- exactly ONE minimal non-interactive call; the prompt asks for a deterministic
  marker line, nothing else
- scratch workspace only; no production repo is touched
- no credentials are read, printed or captured

Usage:
    python scripts/session_008_generic_cli_proof.py [--keep-scratch]
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from encomm_pcc.domain import AgentRole  # noqa: E402
from encomm_pcc.drivers import GenericCliDriver, SessionRequest, SubprocessRunner  # noqa: E402

MARKER = "ENCOMM_PCC_GENERIC_CLI_REAL_OK"


def main() -> int:
    keep_scratch = "--keep-scratch" in sys.argv[1:]

    if not shutil.which("opencode"):
        print("GENERIC_CLI_PROOF SKIPPED: opencode is not on PATH.")
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="encomm-pcc-s008-genericcli-"))
    config = {
        "executable": "opencode",
        "args": [
            "run",
            "Follow the instruction contained in the attached file exactly.",
            "--file",
            "{prompt_file}",
        ],
        "model_args": ["-m", "{model}"],
        "prompt_transport": "temporary_file",
        "result_mode": "stdout_text",
        "timeout_s": 300,
    }
    print(f"scratch dir  : {scratch}")
    print(f"config       : {json.dumps(config)}")

    driver = GenericCliDriver(runner=SubprocessRunner())
    request = SessionRequest(
        role=AgentRole.ORCHESTRATOR,
        workspace_path=str(scratch),
        model="openrouter/~z-ai/glm-flash-latest",
        extra={"generic_cli": config},
    )

    started = time.monotonic()
    session = driver.start_session(request)
    handle = driver.send_prompt(
        session,
        "Reply with exactly one line and nothing else — no markdown, no "
        f"explanation:\n{MARKER}",
    )
    result = driver.wait_for_completion(handle, timeout_s=300)
    duration = time.monotonic() - started

    evidence = {
        "executable": "opencode (version 1.17.13, probed separately)",
        "argv_shape": ["opencode", "run", "<instruction>", "--file", "{prompt_file}", "-m", "{model}"],
        "exit_code": result.exit_code,
        "ok": result.ok,
        "duration_s": round(duration, 2),
        "session_id": result.session_id,  # None expected: stateless driver
        "marker_found": MARKER in (result.text or ""),
        "text": (result.text or "")[:500],
        "stderr_excerpt": (result.metadata or {}).get("stderr_excerpt", ""),
    }
    print(json.dumps(evidence, indent=2))

    if keep_scratch:
        print(f"scratch kept : {scratch}")
    else:
        shutil.rmtree(scratch, ignore_errors=True)
        print("scratch removed.")

    if evidence["ok"] and evidence["marker_found"]:
        print("GENERIC_CLI_PROOF PASSED")
        return 0
    print("GENERIC_CLI_PROOF FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
