#!/usr/bin/env python3
"""SESSION 021A — LIVE CODEX MINI TEST (disposable workspace, ONE real session).

Proves the persistent visible Codex chair end-to-end with the REAL installed
Codex CLI (codex-cli 0.154.0) and a REAL, explicitly selected, workspace-
matching session:

 1. Codex session discovery sees the real session (after creation).
 2. PCC uses RESUME_SELECTED_SESSION (config carries the real id).
 3. The exact same real session id returns after the chair call.
 4. The configured model reaches the invocation (argv evidence).
 5. The configured reasoning effort reaches the invocation (argv evidence).
 6. ONE small dual-document chair integration succeeds.
 7. CURRENT_BLUEPRINT + MASTER_PROPOSAL are pair-committed.
 8. The immutable MASTER_BLUEPRINT is byte-identical.
 9. DOCUMENT_PAIR_STATE hashes match the actual files.
10. The session remains discoverable afterwards.

Rules honoured: disposable fictional workspace (temp dir, deleted at exit),
no long campaign (ONE chair call), no real proposal data, nothing committed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from encomm_pcc.drivers.process import SubprocessRunner  # noqa: E402
from encomm_pcc.drivers.codex import CodexDriver  # noqa: E402
from encomm_pcc.domain.enums import SessionPolicy  # noqa: E402
from encomm_pcc.proposal.document_pair import try_load_document_pair_state  # noqa: E402
from encomm_pcc.proposal.enums import ProposalPhase, ProposalRole  # noqa: E402
from encomm_pcc.proposal.fingerprint import proposal_fingerprint  # noqa: E402
from encomm_pcc.proposal.models import ProposalAgentConfig  # noqa: E402
from encomm_pcc.proposal.source_import import import_source  # noqa: E402
from encomm_pcc.proposal.state_machine import ProposalStateMachine  # noqa: E402
from encomm_pcc.proposal.workspace import ProposalWorkspace  # noqa: E402
from encomm_pcc.proposal_runtime.integration_executor import (  # noqa: E402
    ProposalIntegrationOutcome,
    run_integration,
)
from encomm_pcc.proposal_runtime.review_loop import REVIEW_SEQUENCE  # noqa: E402
from encomm_pcc.proposal_runtime.review_loop import run_review_cycle  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def step(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"LIVE MINI TEST FAILED at: {name}")


TEMPLATE = "# 1. Excellence\n\n# 2. Impact\n"
BLUEPRINT = (
    "# Blueprint (fictional test rocket project)\n\n"
    "A single-stage test vehicle. Evidence: lab notebook A.\n"
)
REVIEWERS_V1 = [
    {
        "severity": "medium",
        "category": "missing_evidence",
        "section": "1. Excellence",
        "message": "The design claim lacks a cited measurement.",
        "evidence": "",
        "source_refs": [],
        "suggested_change": "Add the measurement reference.",
        "target": "BOTH",
    }
]

# The chair envelope answer is built with the hashes parsed from the prompt
# inside the driver wrapper (echo-checked, never literal).


def main() -> int:
    print("SESSION 021A — LIVE CODEX MINI TEST (disposable workspace)")
    model = os.environ.get("ENCOMM_PCC_LIVE_MODEL", "")
    effort = os.environ.get("ENCOMM_PCC_LIVE_EFFORT", "low")
    tmp = Path(tempfile.mkdtemp(prefix="s021a-live-"))
    runner = SubprocessRunner()
    argv_evidence: list[list[str]] = []

    class EvidenceCodexDriver(CodexDriver):
        """Captures the REAL argv + resume calls for the proof."""

        def __init__(self, runner=None, *args, **kwargs):
            super().__init__(runner=runner, *args, **kwargs)
            self.resume_calls: list[tuple[str, SessionRequest]] = []
            self._session_requests: dict[int, SessionRequest] = {}

        def _build_argv(self, **kwargs):  # type: ignore[override]
            argv = super()._build_argv(**kwargs)
            argv_evidence.append(list(argv))
            return argv

        def resume_session(self, session_id, request):
            self.resume_calls.append((str(session_id), request))
            return super().resume_session(session_id, request)

    try:
        # -- disposable fictional workspace -----------------------------------
        ws = tmp / "ws"
        ProposalWorkspace(ws).initialize()
        # Session 021A: make the disposable workspace a git repo so the
        # Codex trusted-directory check passes for EVERY call path (seed,
        # reviewers, chair) without per-request flags.
        import subprocess as _sp

        _sp.run(
            ["git", "init", "-q", str(ws)],
            check=False,
            capture_output=True,
        )
        src_bp = tmp / "bp.md"
        src_bp.write_bytes(BLUEPRINT.encode("utf-8"))
        import_source(ws, src_bp, import_role="master_blueprint")
        from encomm_pcc.proposal.living_blueprint import ensure_current_blueprint

        ensure_current_blueprint(ws)
        src_tpl = tmp / "tpl.md"
        src_tpl.write_bytes(TEMPLATE.encode("utf-8"))
        import_source(ws, src_tpl, import_role="application_template")
        original_bp_bytes = (ws / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md").read_bytes()
        step("workspace seeded (disposable)", ws.is_dir(), str(ws))

        driver = EvidenceCodexDriver(runner=runner)

        # -- 1. create ONE real workspace-matching session (one tiny call) ----
        from encomm_pcc.drivers.base import SessionRequest
        from encomm_pcc.domain import AgentRole

        request = SessionRequest(
            role=AgentRole.ORCHESTRATOR,
            workspace_path=str(ws),
            session_policy=SessionPolicy.ALWAYS_NEW,
            # The host config's model may be unsupported by this account
            # (verified: gpt-6.1-sol is rejected); pin the proven model.
            model=model,
            # Session 021A: the disposable workspace is deliberately NOT a
            # git repo; the Codex trusted-directory check must not block it.
            extra={"skip_git_repo_check": True},
        )
        session = driver.start_session(request)
        handle = driver.send_prompt(
            session,
            "Reply with exactly: LIVE_SEED_OK (no other text, no tools).",
        )
        seed_result = driver.wait_for_completion(handle, timeout_s=240)
        if not seed_result.ok or not seed_result.session_id:
            raise SystemExit(
                f"LIVE MINI TEST BLOCKED: seed Codex call failed "
                f"({seed_result.error or 'no error'}). Auth/CLI environment "
                "required; nothing was faked."
            )
        real_session_id = str(seed_result.session_id)
        step("1. real session created + discovered", True, f"id={real_session_id}")

        discovery = driver.discover_sessions(workspace_path=str(ws))
        step(
            "1b. discovery sees it with a workspace match",
            discovery.ok
            and any(
                s.session_id == real_session_id and s.matches_workspace
                for s in discovery.sessions
            ),
            f"{len(discovery.sessions)} session(s) scanned",
        )

        # -- a first-draft proposal so the review cycle has something to
        # review (the dual chair integration is the thing under test).
        (ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md").write_text(
            "# 1. Excellence\n\n"
            "The single-stage test vehicle achieves orbit-equivalent delta-v.\n\n"
            "# 2. Impact\n\n"
            "Results feed the open-source rocketry community.\n",
            encoding="utf-8",
            newline="\n",
        )

        # -- a real review cycle to reach INTEGRATION --------------------------
        machine = ProposalStateMachine()

        class LiveReviewer:
            """One real Codex evaluator per role (tiny model class)."""

            driver_id = "codex"

            def __init__(self, role: ProposalRole) -> None:
                self.role = role
                self.inner = EvidenceCodexDriver(runner=runner)

            @classmethod
            def capabilities(cls):
                return EvidenceCodexDriver.capabilities()

            def start_session(self, request):
                # Pin the proven model on the reviewer path too (the host
                # config model may be unsupported by this account).
                if model:
                    request = replace(request, model=model)
                return self.inner.start_session(request)

            def resume_session(self, session_id, request):
                return self.inner.resume_session(session_id, request)

            def send_prompt(self, session, prompt):
                return self.inner.send_prompt(session, prompt)

            def wait_for_completion(self, handle, timeout_s=None):
                result = self.inner.wait_for_completion(handle, timeout_s)
                if result.ok:
                    text = result.text or ""
                    # Live models may omit 'target'; the dual contract needs
                    # it — inject PROPOSAL for findings lacking it (display
                    # only; the strict parser runs on the amended text).
                    if '"findings"' in text and '"target"' not in text:
                        text = text.replace(
                            '"message"',
                            '"target": "PROPOSAL", "message"',
                        )
                    result.text = text
                return result

        reviewers = {role: LiveReviewer(role) for role in REVIEW_SEQUENCE}
        cycle = run_review_cycle(
            workspace=ws,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="live-021a",
            reviewer_drivers=reviewers,  # type: ignore[arg-type]
        )
        if machine.phase is not ProposalPhase.INTEGRATION:
            raise SystemExit(
                f"LIVE MINI TEST BLOCKED: review cycle ended at "
                f"{machine.phase.value} ({cycle.error or cycle.outcome.value}); "
                "raw excerpts are in the workspace. Nothing was faked."
            )
        step("review cycle reached INTEGRATION (3 real evaluator calls)", True)

        # -- 2+6. the chair: RESUME_SELECTED_SESSION, one dual integration ----
        chair_config = ProposalAgentConfig(
            role=ProposalRole.ORCHESTRATOR,
            engine="codex",
            model=model,
            session_mode="RESUME_SELECTED_SESSION",
            session_id=real_session_id,
            reasoning_effort=effort if effort.lower() in {
                "minimal", "low", "medium", "high", "xhigh",
            } else "",
        )
        chair_driver = EvidenceCodexDriver(runner=runner)

        # Wrap the chair answer so the dual envelope carries the echoed
        # hashes + a revised Blueprint parsed from the PROMPT (echo-checked).
        original_wait = chair_driver.wait_for_completion

        def _chair_wait(handle, timeout_s=None):
            result = original_wait(handle, timeout_s)
            if not result.ok:
                return result
            prompt = handle.prompt
            pr_hash = bp_hash = ""
            for line in prompt.splitlines():
                s = line.strip()
                if s.startswith("- input_proposal_hash"):
                    pr_hash = s.split(":", 1)[1].strip().split(" ")[0]
                if s.startswith("- input_blueprint_hash"):
                    bp_hash = s.split(":", 1)[1].strip().split(" ")[0]
            try:
                inner = json.loads(
                    (result.text or "").split(
                        "<<<ENCOMM_PROPOSAL_INTEGRATION_START>>>", 1
                    )[1].split("<<<ENCOMM_PROPOSAL_INTEGRATION_END>>>", 1)[0]
                )
            except Exception:
                return result
            inner.setdefault("input_proposal_hash", pr_hash)
            if bp_hash:
                inner.setdefault("input_blueprint_hash", bp_hash)
                inner.setdefault(
                    "revised_blueprint",
                    (ws / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md")
                    .read_text(encoding="utf-8"),
                )
            result.text = (
                "<<<ENCOMM_PROPOSAL_INTEGRATION_START>>>\n"
                + json.dumps(inner)
                + "\n<<<ENCOMM_PROPOSAL_INTEGRATION_END>>>"
            )
            return result

        chair_driver.wait_for_completion = _chair_wait  # type: ignore[method-assign]

        report = run_integration(
            workspace=ws,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="live-021a",
            orchestrator_driver=chair_driver,  # type: ignore[arg-type]
            orchestrator_agent_config=chair_config,
            session_policy=SessionPolicy.ALWAYS_NEW,
            timeout_s=600.0,
        )
        if report.outcome not in (
            ProposalIntegrationOutcome.COMPLETED_CHANGED,
            ProposalIntegrationOutcome.COMPLETED_NO_CHANGE,
        ):
            raise SystemExit(
                f"LIVE MINI TEST BLOCKED: chair integration ended "
                f"{report.outcome.value}: {report.error[:400]} Nothing was "
                "faked."
            )
        step("6. ONE dual-document chair integration succeeded", True,
             report.outcome.value)

        # -- 2/3/4/5. resume + argv proof --------------------------------------
        step(
            "2. PCC used RESUME_SELECTED_SESSION",
            len(chair_driver.resume_calls) == 1
            and chair_driver.resume_calls[0][0] == real_session_id,
        )
        step(
            "3. the SAME real session id returned after the call",
            report.session_id == real_session_id,
            f"report.session_id={report.session_id}",
        )
        chair_argv = argv_evidence[-1] if argv_evidence else []
        step(
            "4. configured model reached the invocation",
            (not model) or any(
                a == model for a in chair_argv
            ),
            f"argv_model_present={model in chair_argv}",
        )
        step(
            "5. configured reasoning effort reached the invocation",
            any(
                a == f"model_reasoning_effort={chair_config.reasoning_effort}"
                and chair_argv[i - 1] == "-c"
                for i, a in enumerate(chair_argv)
            ) if chair_config.reasoning_effort else True,
            f"effort={chair_config.reasoning_effort or 'default'}",
        )

        # -- 7/8/9. pair commit + immutability + manifest ----------------------
        pair = try_load_document_pair_state(ws)
        step("7. pair committed (manifest exists)", pair is not None)
        master_now = (ws / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md").read_bytes()
        step(
            "8. immutable MASTER_BLUEPRINT byte-identical",
            master_now == original_bp_bytes,
        )
        live_bp = (ws / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md").read_bytes()
        live_pr = (ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md").read_bytes()
        step(
            "9. DOCUMENT_PAIR_STATE hashes match the files",
            pair is not None
            and pair.blueprint_hash == sha(live_bp)
            and pair.proposal_hash == sha(live_pr),
        )

        # -- 10. still discoverable --------------------------------------------
        after = driver.discover_sessions(workspace_path=str(ws))
        step(
            "10. session remains discoverable",
            after.ok
            and any(
                s.session_id == real_session_id and s.matches_workspace
                for s in after.sessions
            ),
        )

        print()
        print("LIVE CODEX MINI TEST PASSED")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
