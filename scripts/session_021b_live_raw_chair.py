#!/usr/bin/env python3
"""SESSION 021B — LIVE CODEX RAW CHAIR CONTRACT TEST (disposable workspace).

RAW production-contract proof, explicitly distinct from the Session 021A
TRANSPORT test:

    RAW MODEL OUTPUT
        ↓
    production parser (parse_proposal_integration, strict, fail closed)
        ↓
    production runtime (run_integration → all-or-rollback pair commit)

with ZERO output rewriting / injection:
  - no 'target' injection            (the only live call is the CHAIR; the
                                      reviewers reaching INTEGRATION are
                                      offline scripted doubles, so there is
                                      no live reviewer output to repair);
  - no hash injection                (the chair must echo BOTH hashes itself);
  - no envelope repair;
  - no revised_blueprint injection   (the chair must return it or honestly
                                      keep the Blueprint byte-identical via
                                      null — the runtime's verbatim re-commit
                                      is PRODUCTION behavior, not a harness
                                      patch);
  - no JSON repair.

If the raw model output violates the strict contract, this test FAILS —
honestly.  The fix belongs in the production prompt, never in this harness.

Chair proof uses the SAME real selected Codex session/resume flow proven in
Session 021A: one tiny seed call creates a real session, discovery must see
it with a workspace match, and the chair runs through
RESUME_SELECTED_SESSION (the same real thread id returns).

The S021B invariants are also proven live: BLUEPRINT_STATE advances with the
pair (no current_drift afterwards), and the real source_pack_id survives
into DOCUMENT_PAIR_STATE.

Zero rewriting is asserted STRUCTURALLY: the live chair driver class never
overrides ``wait_for_completion``.

Rules honoured: disposable fictional workspace (temp dir, removed at exit),
ONE seed + ONE chair call (no campaign), no real proposal data, nothing
committed to any repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from encomm_pcc.drivers.base import SessionRequest  # noqa: E402
from encomm_pcc.drivers.codex import CodexDriver  # noqa: E402
from encomm_pcc.drivers.process import SubprocessRunner  # noqa: E402
from encomm_pcc.domain import AgentRole  # noqa: E402
from encomm_pcc.domain.enums import SessionPolicy  # noqa: E402
from encomm_pcc.proposal.document_pair import (  # noqa: E402
    try_load_document_pair_state,
)
from encomm_pcc.proposal.enums import ProposalPhase, ProposalRole  # noqa: E402
from encomm_pcc.proposal.fingerprint import proposal_fingerprint  # noqa: E402
from encomm_pcc.proposal.living_blueprint import (  # noqa: E402
    ensure_current_blueprint,
    load_blueprint_state,
)
from encomm_pcc.proposal.models import ProposalAgentConfig  # noqa: E402
from encomm_pcc.proposal.source_import import import_source  # noqa: E402
from encomm_pcc.proposal.state_machine import ProposalStateMachine  # noqa: E402
from encomm_pcc.proposal.workspace import ProposalWorkspace  # noqa: E402
from encomm_pcc.proposal_runtime.integration_executor import (  # noqa: E402
    ProposalIntegrationOutcome,
    run_integration,
)
from encomm_pcc.proposal_runtime.review_loop import (  # noqa: E402
    REVIEW_SEQUENCE,
    run_review_cycle,
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def step(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"LIVE RAW CHAIR TEST FAILED at: {name}")


TEMPLATE = "# 1. Excellence\n\n# 2. Impact\n"
BLUEPRINT = (
    "# Blueprint (fictional test rocket project)\n\n"
    "A single-stage test vehicle. Evidence: lab notebook A.\n"
)
PROPOSAL_V1 = (
    "# 1. Excellence\n\n"
    "The single-stage test vehicle achieves orbit-equivalent delta-v.\n\n"
    "# 2. Impact\n\n"
    "Results feed the open-source rocketry community.\n"
)


class ScriptedReviewer:
    """OFFLINE scripted evaluator double (never a live call, never repaired)."""

    driver_id = "scripted-021b-raw-reviewer"

    def __init__(self, role: ProposalRole, proposal_hash_holder: dict) -> None:
        self.role = role
        self.holder = proposal_hash_holder

    @classmethod
    def capabilities(cls):
        from encomm_pcc.drivers.base import DriverCapabilities

        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Scripted Raw Reviewer",
            supports_sessions=False,
            supports_resume=False,
            requires_profile=False,
            supports_model_selection=False,
            supports_streaming=False,
            supports_cancellation=False,
        )

    def start_session(self, request):
        from encomm_pcc.drivers.base import DriverSession

        return DriverSession(driver_id=self.driver_id, role=request.role)

    def resume_session(self, session_id, request):
        raise NotImplementedError

    def send_prompt(self, session, prompt):
        from encomm_pcc.drivers.base import PromptHandle

        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle, timeout_s=None):
        from encomm_pcc.drivers.base import PromptResult

        ph = self.holder["hash"]
        body = {
            "reviewer_role": self.role.value,
            "iteration_number": 1,
            "proposal_hash": ph,
            "verdict": "NEEDS_REVISION",
            "summary": f"{self.role.value} raw-test position",
            "findings": [
                {
                    "severity": "medium",
                    "category": "missing_evidence",
                    "section": "1. Excellence",
                    "message": f"{self.role.value}: add the measurement citation.",
                    "evidence": "cite the lab notebook",
                    "source_refs": [],
                    "suggested_change": "add the citation",
                    "target": "PROPOSAL",
                }
            ],
            "proposed_patches": [],
            "unverified_claims": [],
        }
        text = (
            "<<<ENCOMM_PROPOSAL_REVIEW_START>>>\n"
            + json.dumps(body)
            + "\n<<<ENCOMM_PROPOSAL_REVIEW_END>>>"
        )
        return PromptResult(ok=True, text=text, session_id="scripted-1")


class EvidenceCodexDriver(CodexDriver):
    """Records REAL argv + resume calls only — output path UNTOUCHED.

    Zero-rewriting proof: this class deliberately does NOT override
    ``wait_for_completion`` (asserted structurally in main()).
    """

    def __init__(self, runner=None, *args, **kwargs):
        super().__init__(runner=runner, *args, **kwargs)
        self.resume_calls: list[tuple[str, SessionRequest]] = []
        self._argv: list[list[str]] = []

    def _build_argv(self, **kwargs):  # type: ignore[override]
        argv = super()._build_argv(**kwargs)
        self._argv.append(list(argv))
        return argv

    def resume_session(self, session_id, request):
        self.resume_calls.append((str(session_id), request))
        return super().resume_session(session_id, request)


def main() -> int:
    print("SESSION 021B — LIVE CODEX RAW CHAIR CONTRACT TEST (disposable workspace)")
    model = os.environ.get("ENCOMM_PCC_LIVE_MODEL", "")
    effort = os.environ.get("ENCOMM_PCC_LIVE_EFFORT", "low")
    tmp = Path(tempfile.mkdtemp(prefix="s021b-raw-live-"))
    runner = SubprocessRunner()
    try:
        # -- structural zero-rewriting proof --------------------------------
        step(
            "0. chair driver NEVER overrides wait_for_completion",
            "wait_for_completion" not in EvidenceCodexDriver.__dict__,
            "raw output reaches the strict parser unmodified",
        )

        # -- disposable dual workspace ---------------------------------------
        ws = tmp / "ws"
        ProposalWorkspace(ws).initialize()
        subprocess.run(["git", "init", "-q", str(ws)], check=False, capture_output=True)
        src_bp = tmp / "bp.md"
        src_bp.write_bytes(BLUEPRINT.encode("utf-8"))
        import_source(ws, src_bp, import_role="master_blueprint")
        ensure_current_blueprint(ws)
        src_tpl = tmp / "tpl.md"
        src_tpl.write_bytes(TEMPLATE.encode("utf-8"))
        import_source(ws, src_tpl, import_role="application_template")
        (ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md").write_text(
            PROPOSAL_V1, encoding="utf-8", newline="\n"
        )
        original_bp_bytes = (
            ws / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md"
        ).read_bytes()
        frozen_current = (
            ws / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md"
        ).read_bytes()
        step("workspace seeded (disposable, dual, git repo)", ws.is_dir(), str(ws))

        driver = EvidenceCodexDriver(runner=runner)

        # -- 1. ONE tiny real call creates the session -----------------------
        request = SessionRequest(
            role=AgentRole.ORCHESTRATOR,
            workspace_path=str(ws),
            session_policy=SessionPolicy.ALWAYS_NEW,
            model=model,
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
                "LIVE RAW CHAIR TEST BLOCKED: seed Codex call failed "
                f"({seed_result.error or 'no error'}). Auth/CLI environment "
                "required; nothing was faked."
            )
        real_session_id = str(seed_result.session_id)
        step("1. real session created", True, f"id={real_session_id}")

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

        # -- 2. OFFLINE scripted reviewers reach INTEGRATION -----------------
        holder = {
            "hash": proposal_fingerprint(
                ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
            )
        }
        machine = ProposalStateMachine()
        reviewers = {
            role: ScriptedReviewer(role, holder) for role in REVIEW_SEQUENCE
        }
        cycle = run_review_cycle(
            workspace=ws,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="live-021b-raw",
            reviewer_drivers=reviewers,  # type: ignore[arg-type]
        )
        if machine.phase is not ProposalPhase.INTEGRATION:
            raise SystemExit(
                f"LIVE RAW CHAIR TEST BLOCKED: scripted review cycle ended at "
                f"{machine.phase.value} ({cycle.error or cycle.outcome.value})."
            )
        step("2. scripted reviewers reached INTEGRATION (offline)", True)

        # -- 3. THE RAW CHAIR CALL (RESUME_SELECTED_SESSION, zero rewriting) -
        chair_config = ProposalAgentConfig(
            role=ProposalRole.ORCHESTRATOR,
            engine="codex",
            model=model,
            session_mode="RESUME_SELECTED_SESSION",
            session_id=real_session_id,
            reasoning_effort=effort
            if effort.lower() in {"minimal", "low", "medium", "high", "xhigh"}
            else "",
        )
        chair_driver = EvidenceCodexDriver(runner=runner)
        source_pack_id = "srcpack-live-021b-raw"
        report = run_integration(
            workspace=ws,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="live-021b-raw",
            orchestrator_driver=chair_driver,  # type: ignore[arg-type]
            orchestrator_agent_config=chair_config,
            session_policy=SessionPolicy.ALWAYS_NEW,
            timeout_s=900.0,
            source_pack_id=source_pack_id,
        )
        if report.outcome not in (
            ProposalIntegrationOutcome.COMPLETED_CHANGED,
            ProposalIntegrationOutcome.COMPLETED_NO_CHANGE,
        ):
            print(
                "RAW CONTRACT VERDICT: the real model output FAILED the "
                f"strict production contract ({report.outcome.value})."
            )
            print("parse_reason:", report.parse_reason or "(n/a)")
            print("raw excerpt (bounded):")
            print(report.raw_excerpt[:2000])
            raise SystemExit(
                "LIVE RAW CHAIR TEST FAILED — the raw model output violated "
                "the strict contract. Per the raw-contract rule this is an "
                "honest FAIL; the fix belongs in the production prompt, "
                "never in this harness."
            )
        step("3. RAW chair output passed the STRICT production parser", True,
             report.outcome.value)

        # -- 4. resume + argv evidence (transport facts, not output edits) ---
        step(
            "4. chair ran through RESUME_SELECTED_SESSION",
            len(chair_driver.resume_calls) == 1
            and chair_driver.resume_calls[0][0] == real_session_id,
        )
        step(
            "5. the SAME real session id returned after the call",
            report.session_id == real_session_id,
            f"report.session_id={report.session_id}",
        )
        chair_argv = chair_driver._argv[-1] if chair_driver._argv else []
        step(
            "6. configured reasoning effort reached the invocation",
            any(
                a == f"model_reasoning_effort={chair_config.reasoning_effort}"
                and chair_argv[i - 1] == "-c"
                for i, a in enumerate(chair_argv)
            ) if chair_config.reasoning_effort else True,
            f"effort={chair_config.reasoning_effort or 'default'}",
        )

        # -- 7. pair commit + S021B invariants, live -------------------------
        pair = try_load_document_pair_state(ws)
        step("7. pair committed (DOCUMENT_PAIR_STATE exists)", pair is not None)
        live_bp = (ws / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md").read_bytes()
        live_pr = (ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md").read_bytes()
        step(
            "8. manifest hashes match the actual files",
            pair is not None
            and pair.blueprint_hash == sha(live_bp)
            and pair.proposal_hash == sha(live_pr),
        )
        step(
            "9. real source_pack_id preserved into the pair manifest",
            pair is not None and pair.source_pack_id == source_pack_id,
            f"source_pack_id={pair.source_pack_id if pair else '—'}",
        )
        state = load_blueprint_state(ws)
        step(
            "10. BLUEPRINT_STATE advanced with the pair (no current_drift)",
            pair is not None
            and state.current_blueprint_hash == sha(live_bp)
            and state.current_blueprint_iteration == pair.iteration
            and state.last_pair_revision_id == pair.pair_revision_id,
            f"iteration={state.current_blueprint_iteration}",
        )
        ensure_current_blueprint(ws)  # must NOT raise current_drift
        step("11. ensure_current_blueprint() succeeds after the commit", True)
        step(
            "12. immutable MASTER_BLUEPRINT byte-identical",
            (ws / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md").read_bytes()
            == original_bp_bytes,
        )
        if report.output_blueprint_hash and report.output_blueprint_hash != sha(
            frozen_current
        ):
            step(
                "13. the chair itself revised the Blueprint (its own bytes)",
                report.output_blueprint_hash == sha(live_bp),
                "raw revised_blueprint accepted",
            )
        else:
            step(
                "13. chair kept the Blueprint byte-identical (honest null)",
                live_bp == frozen_current,
                "runtime verbatim re-commit (production behavior)",
            )

        # -- 14. still discoverable ------------------------------------------
        after = driver.discover_sessions(workspace_path=str(ws))
        step(
            "14. session remains discoverable",
            after.ok
            and any(
                s.session_id == real_session_id and s.matches_workspace
                for s in after.sessions
            ),
        )

        print()
        print("LIVE CODEX RAW CHAIR CONTRACT PASSED")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
