"""Session 015 — isolation, provider-independence and persistence discipline.

Covers the mandated ISOLATION matrix sections (brief §18, items 51–55):

* the PURE proposal package still imports NOTHING from core/domain/drivers/
  persistence/ui/proposal_runtime (subprocess-proven);
* the runtime modules import no UI/persistence/PySide6 (subprocess-proven);
* Coding Mode production files are untouched (structural scan) and never
  import proposal packages;
* no provider/engine/model name is hardcoded anywhere in the new
  integration machinery;
* no full transcript / raw envelope is persisted by the integration
  writers (structural scan over the writing modules).

All tests are OFFLINE; the subprocess probes import repo modules with
``src/`` on ``sys.path`` and assert on the imported-module sets.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
PYTHON = sys.executable

RUNTIME_INTEGRATION_MODULES = (
    "encomm_pcc.proposal_runtime.integration_executor",
    "encomm_pcc.proposal_runtime.integration_artifacts",
    "encomm_pcc.proposal_runtime.master_writer",
    "encomm_pcc.proposal_runtime.version_freeze_post",
    "encomm_pcc.proposal_runtime.review_freshness",
    "encomm_pcc.proposal_runtime.revision_handoff",
    "encomm_pcc.proposal_runtime.proposal_iteration",
)

PURE_INTEGRATION_MODULES = (
    "encomm_pcc.proposal.integration_packet",
    "encomm_pcc.proposal.integration_models",
    "encomm_pcc.proposal.integration_parser",
)


def _run_and_report(code: str) -> list[str]:
    proc = subprocess.run(
        [PYTHON, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        raise AssertionError(f"probe failed: {proc.stderr}")
    return proc.stdout.strip().splitlines()


class TestIsolation:
    def test_51_pure_proposal_package_still_import_clean(self) -> None:
        code = (
            "import sys; sys.path.insert(0, r'%s');\n"
            "import encomm_pcc.proposal as pp;\n"
            "banned = ('PySide6', 'encomm_pcc.core', 'encomm_pcc.domain',\n"
            " 'encomm_pcc.drivers', 'encomm_pcc.persistence',\n"
            " 'encomm_pcc.proposal_runtime', 'encomm_pcc.ui')\n"
            "hits = [m for m in sys.modules for b in banned\n"
            "        if m == b or m.startswith(b + '.')]\n"
            "print('\\n'.join(hits))\n" % str(SRC_ROOT)
        )
        assert _run_and_report(code) == []

    def test_51b_pure_integration_modules_import_clean(self) -> None:
        mods = ", ".join(repr(m) for m in PURE_INTEGRATION_MODULES)
        code = (
            "import sys; sys.path.insert(0, r'%s');\n"
            "for m in (%s,): __import__(m)\n"
            "banned = ('PySide6', 'encomm_pcc.core', 'encomm_pcc.domain',\n"
            " 'encomm_pcc.drivers', 'encomm_pcc.persistence',\n"
            " 'encomm_pcc.proposal_runtime', 'encomm_pcc.ui')\n"
            "hits = [m for m in sys.modules for b in banned\n"
            "        if m == b or m.startswith(b + '.')]\n"
            "print('\\n'.join(hits))\n" % (str(SRC_ROOT), mods)
        )
        assert _run_and_report(code) == []

    def test_51c_runtime_integration_modules_import_no_ui_persistence(self) -> None:
        mods = ", ".join(repr(m) for m in RUNTIME_INTEGRATION_MODULES)
        code = (
            "import sys; sys.path.insert(0, r'%s');\n"
            "for m in (%s,): __import__(m)\n"
            "banned = ('PySide6', 'encomm_pcc.persistence', 'encomm_pcc.ui',\n"
            " 'encomm_pcc.core')\n"
            "hits = [m for m in sys.modules for b in banned\n"
            "        if m == b or m.startswith(b + '.')]\n"
            "print('\\n'.join(hits))\n" % (str(SRC_ROOT), mods)
        )
        assert _run_and_report(code) == []

    def test_52_coding_mode_production_files_unchanged(self) -> None:
        # No Coding Mode production file may reference the proposal domain.
        # Session 017 (by design): the UI package now hosts the Proposal Mode
        # operator surface (proposal_mode.py + proposal_worker.py) and the
        # MainWindow wiring — the core pipeline packages stay proposal-free.
        banned_fragments = ("proposal",)
        proposal_ui_modules = {
            SRC_ROOT / "encomm_pcc" / "ui" / "proposal_mode.py",
            SRC_ROOT / "encomm_pcc" / "ui" / "proposal_worker.py",
            # Session 017 wiring: the mode-stack surface + the Simple Mode
            # navigation affordance (brief §2: changes limited to it).
            SRC_ROOT / "encomm_pcc" / "ui" / "main_window.py",
            SRC_ROOT / "encomm_pcc" / "ui" / "simple_mode.py",
            # Session 020: the readiness-disclaimer label module (imports
            # ONLY proposal.readiness's constant; name-only exemption).
            SRC_ROOT / "encomm_pcc" / "ui" / "readiness_disclaimer.py",
        }
        offenders: list[str] = []
        for pkg in ("core", "domain", "drivers", "persistence", "ui"):
            for path in (SRC_ROOT / "encomm_pcc" / pkg).rglob("*.py"):
                if path in proposal_ui_modules:
                    continue
                text = path.read_text(encoding="utf-8")
                for fragment in banned_fragments:
                    if fragment in text:
                        offenders.append(f"{path.relative_to(REPO_ROOT)}: {fragment}")
        assert offenders == []

    def test_53_no_real_ai_calls_possible_in_suite_modules(self) -> None:
        # The Session 015 machinery must not spawn processes, open sockets
        # or import provider SDKs anywhere.
        forbidden = (
            "import socket",
            "import requests",
            "import httpx",
            "urllib.request",
            "subprocess",
        )
        offenders: list[str] = []
        for name in PURE_INTEGRATION_MODULES + RUNTIME_INTEGRATION_MODULES:
            rel = Path(*name.split(".")).with_suffix(".py")
            text = (SRC_ROOT / rel).read_text(encoding="utf-8")
            for fragment in forbidden:
                if fragment in text:
                    offenders.append(f"{name}: {fragment}")
        assert offenders == []

    def test_54_no_provider_hardcoding(self) -> None:
        forbidden = (
            "codex",
            "hermes",
            "openrouter",
            "glm",
            "minimax",
            "claude",
            "openai",
            "anthropic",
        )
        offenders: list[str] = []
        for name in PURE_INTEGRATION_MODULES + RUNTIME_INTEGRATION_MODULES:
            rel = Path(*name.split(".")).with_suffix(".py")
            text = (SRC_ROOT / rel).read_text(encoding="utf-8").lower()
            for fragment in forbidden:
                if fragment in text:
                    offenders.append(f"{name}: {fragment}")
        assert offenders == []

    def test_55_no_transcript_persistence_in_writers(self) -> None:
        # The integration writers must never serialise raw model output:
        # scan the WRITING modules for the mechanisms, not the concept words.
        writer_modules = (
            "encomm_pcc.proposal_runtime.integration_artifacts",
            "encomm_pcc.proposal_runtime.revision_handoff",
        )
        offenders: list[str] = []
        for name in writer_modules:
            rel = Path(*name.split(".")).with_suffix(".py")
            text = (SRC_ROOT / rel).read_text(encoding="utf-8")
            for mechanism in ("raw_excerpt", "prompt_text", "raw_text"):
                if mechanism in text:
                    offenders.append(f"{name}: {mechanism}")
        assert offenders == []
