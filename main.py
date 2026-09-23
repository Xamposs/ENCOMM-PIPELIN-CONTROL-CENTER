#!/usr/bin/env python3
"""Entry point for the ENCOMM Pipeline Control Center.

    python main.py                 launch the desktop application
    python main.py --smoke-test    packaged self-test (offscreen, no model calls)

The package itself lives under ``src/``; this shim puts it on ``sys.path`` so
the application runs from a fresh checkout with no install step.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from encomm_pcc.app import SMOKE_TEST_FLAG, run, run_smoke_test  # noqa: E402  (path shim must run first)


if __name__ == "__main__":
    if SMOKE_TEST_FLAG in sys.argv[1:]:
        raise SystemExit(run_smoke_test())
    raise SystemExit(run())
