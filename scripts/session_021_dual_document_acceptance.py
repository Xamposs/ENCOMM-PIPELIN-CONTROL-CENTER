#!/usr/bin/env python3
"""SESSION 021 — OFFLINE DUAL-DOCUMENT ACCEPTANCE (brief §20).

Fully OFFLINE and SCRIPTED: real production Proposal runtime code (source
import → living-Blueprint initialization → initial generation → parallel
panel review with document TARGETS → parallel consensus → ASTRA chair with
the ALL-OR-ROLLBACK DOCUMENT PAIR COMMIT → dual freshness → deterministic
hard gates → COMPLETE).  Only the model/driver outputs are scripted.  A
TEMPORARY proposal workspace is used; the PCC repository is never modified.

Run::

    python scripts/session_021_dual_document_acceptance.py

Expected final line:  DUAL DOCUMENT PROPOSAL ACCEPTANCE PASSED
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from encomm_pcc.drivers.base import (  # noqa: E402
    BaseDriver,
    DriverCapabilities,
    DriverSession,
    PromptHandle,
    PromptResult,
    SessionRequest,
)
from encomm_pcc.proposal.document_pair import (  # noqa: E402
    DOCUMENT_PAIR_STATE_RELPATH,
    try_load_document_pair_state,
)
from encomm_pcc.proposal.enums import ProposalPhase, ProposalRole  # noqa: E402
from encomm_pcc.proposal.fingerprint import proposal_fingerprint  # noqa: E402
from encomm_pcc.proposal.hard_gates import HARD_GATE_EVIDENCE_SCHEMA  # noqa: E402
from encomm_pcc.proposal.living_blueprint import (  # noqa: E402
    ensure_current_blueprint,
    master_blueprint_path,
)
from encomm_pcc.proposal.source_import import import_source  # noqa: E402
from encomm_pcc.proposal.state_machine import ProposalStateMachine  # noqa: E402
from encomm_pcc.proposal.workspace import ProposalWorkspace  # noqa: E402
from encomm_pcc.proposal_runtime import run_hard_gates  # noqa: E402
from encomm_pcc.proposal_runtime.initial_generation import (  # noqa: E402
    InitialGenerationOutcome,
    run_initial_generation,
)
from encomm_pcc.proposal_runtime.panel_chair import (  # noqa: E402
    PanelChairOutcome,
    run_panel_chair_iteration,
)
from encomm_pcc.proposal_runtime.review_freshness import (  # noqa: E402
    evaluate_review_freshness,
)

REVISION = "rev-accept-021"
TEMPLATE = "# 1. Excellence\n\n# 2. Impact\n"
BLUEPRINT_ORIGINAL = b"# Blueprint\n\nKALHAS evidence base.\n"
BLUEPRINT_V2 = (
    "# Blueprint\n\nKALHAS evidence base.\n\n"
    "## Revised design note\n\nEvidence pipeline added by the chair.\n"
)
PROPOSAL_V1 = (
    "# 1. Excellence\n\nInitial KALHAS draft.\n\n# 2. Impact\n\n"
    "[INPUT REQUIRED: uptake evidence]\n"
)
PROPOSAL_V2 = (
    "# 1. Excellence\n\nKALHAS drives the architecture with evidence.\n\n"
    "# 2. Impact\n\nWide uptake, quantified.\n"
)

GOOD_SECTIONS: dict = {
    "mandatory_sections": {"required_headings": ["1. Excellence", "2. Impact"]},
    "challenge_mapping": {
        "required_markers": ["C1"],
        "mappings": [
            {"marker": "C1", "proposal_section": "1. Excellence", "evidence_text": "yes"}
        ],
    },
    "terminology": {
        "rules": [{"canonical": "KALHAS", "forbidden_aliases": ["Kalchas"]}]
    },
    "workplan": {
        "work_packages": [
            {
                "id": "WP1",
                "tasks": [{"id": "T1.1", "owner": "AAI", "person_months": 12}],
                "person_months": 12,
                "deliverables": [{"id": "D1.1", "task_ids": ["T1.1"]}],
                "milestones": [{"id": "MS1", "wp_ref": "WP1"}],
            }
        ],
        "total_person_months": 12,
    },
    "budget": {
        "categories": [{"name": "staff", "amount": 100}],
        "participant_total": 100,
        "declared_total": 100,
    },
    "subcontracting": {"entries": []},
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ScriptedDriver(BaseDriver):
    """One scripted driver answering by prompt shape — the ONLY scripted part.

    Dual-document aware: the ASTRA integration answer echoes the living
    Blueprint hash parsed from the prompt and returns a REVISED Blueprint
    (parsed from the prompt hash, never a literal), and review findings
    carry explicit document TARGETS.
    """

    driver_id = "scripted-s021"

    def __init__(self, role_value: str, *, verdict: str = "NEEDS_REVISION",
                 severity: str = "high", finding_target: str = "PROPOSAL",
                 first_judgement: str = "AGREE") -> None:
        self.role_value = role_value
        self._verdict = verdict
        self._severity = severity
        self._finding_target = finding_target
        self._first_judgement = first_judgement
        self.review_calls = 0
        self.consensus_calls = 0
        self.astra_calls = 0
        self.specialist_calls = 0
        base = 88 if verdict == "PASS" else 78
        self._readiness_scores = [base + 2, base - 4, base + 1]

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Scripted S021 Driver",
            supports_sessions=False,
            supports_resume=False,
            requires_profile=False,
            supports_model_selection=False,
            supports_streaming=False,
            supports_cancellation=False,
        )

    @classmethod
    def probe_availability(cls) -> bool:
        return True

    @classmethod
    def describe(cls) -> dict:
        return {"driver_id": cls.driver_id}

    def discover_sessions(self, **kwargs):
        raise NotImplementedError

    def start_session(self, request: SessionRequest) -> DriverSession:
        return DriverSession(driver_id=self.driver_id, role=request.role)

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        raise NotImplementedError

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        return PromptHandle(session=session, prompt=prompt)

    @staticmethod
    def _meta(prompt: str, key: str) -> str:
        for line in prompt.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"- {key}"):
                raw = stripped.split(":", 1)[1].strip()
                token = raw.split(" ", 1)[0].strip()
                if key.endswith("hash") and len(token) != 64:
                    continue
                return token
        return ""

    @staticmethod
    def _env(start: str, payload: dict, end: str) -> str:
        return f"{start}\n{json.dumps(payload)}\n{end}"

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        prompt = handle.prompt
        if "INITIAL GENERATION — SPECIALIST" in prompt:
            self.specialist_calls += 1
            return PromptResult(
                ok=True,
                text=(
                    "<<<INITIAL_CONTRIBUTION_START>>>\n"
                    f"{self.role_value} contribution grounded in the blueprint.\n"
                    "<<<INITIAL_CONTRIBUTION_END>>>"
                ),
                session_id="s",
            )
        if "INITIAL GENERATION — ASTRA SYNTHESIS" in prompt:
            self.astra_calls += 1
            parts = [
                "<<<INITIAL_PROPOSAL_START>>>\n",
                "# 1. Excellence\n\nInitial KALHAS draft.\n\n# 2. Impact\n\n",
                "[INPUT REQUIRED: uptake evidence]\n",
                "<<<INITIAL_PROPOSAL_END>>>",
            ]
            if "INITIAL_BLUEPRINT_START" in prompt:
                # Dual generation: the chair keeps the living Blueprint
                # unchanged here (a valid, honest choice) — omit the block.
                pass
            return PromptResult(
                ok=True, text="\n".join(parts), session_id="s",
            )
        if "PANEL CONSENSUS" in prompt:
            self.consensus_calls += 1
            body = {
                "role": self.role_value,
                "iteration_number": int(self._meta(prompt, "iteration_number") or 1),
                "proposal_hash": self._meta(prompt, "proposal_hash"),
                "judgements": [
                    {
                        "item_id": "F001",
                        "judgement": self._first_judgement,
                        "rationale": "assessed against sources",
                        "proposed_resolution": "add the evidence",
                        "source_refs": [],
                        "blocks_acceptance": False,
                    }
                ],
                "readiness_assessment": {
                    "criteria": [
                        {"criterion": "scientific_coherence",
                         "score": self._readiness_scores[0], "rationale": ""},
                        {"criterion": "impact_coherence",
                         "score": self._readiness_scores[1], "rationale": ""},
                        {"criterion": "evidence_completeness",
                         "score": self._readiness_scores[2], "rationale": ""},
                    ]
                },
                "summary": f"{self.role_value} consensus position",
            }
            return PromptResult(
                ok=True,
                text=self._env(
                    "<<<ENCOMM_PANEL_CONSENSUS_START>>>", body,
                    "<<<ENCOMM_PANEL_CONSENSUS_END>>>",
                ),
                session_id="s",
            )
        if "PROPOSAL INTEGRATION REQUEST" in prompt:
            self.astra_calls += 1
            body: dict = {
                "role": "ORCHESTRATOR",
                "iteration_number": int(self._meta(prompt, "iteration_number") or 1),
                "input_proposal_hash": self._meta(prompt, "input_proposal_hash"),
                "summary": "panel synthesis applied; evidence added",
                "revised_proposal": PROPOSAL_V2,
            }
            bp_hash = self._meta(prompt, "input_blueprint_hash")
            if bp_hash:
                # Dual contract: echo the parsed hash; the chair revises the
                # LIVING Blueprint (never the immutable original).
                body["input_blueprint_hash"] = bp_hash
                body["revised_blueprint"] = BLUEPRINT_V2
            return PromptResult(
                ok=True,
                text=self._env(
                    "<<<ENCOMM_PROPOSAL_INTEGRATION_START>>>", body,
                    "<<<ENCOMM_PROPOSAL_INTEGRATION_END>>>",
                ),
                session_id="s",
            )
        # default: independent review
        self.review_calls += 1
        findings = (
            [
                {
                    "severity": self._severity,
                    "category": "missing_evidence",
                    "section": "1. Excellence",
                    "message": f"{self.role_value}: claim needs evidence",
                    "evidence": "state the source",
                    "source_refs": [],
                    "suggested_change": "add evidence",
                    "target": self._finding_target,
                }
            ]
            if self._verdict == "NEEDS_REVISION"
            else []
        )
        body = {
            "reviewer_role": self.role_value,
            "iteration_number": int(self._meta(prompt, "iteration_number") or 1),
            "proposal_hash": self._meta(prompt, "proposal_hash"),
            "verdict": self._verdict,
            "summary": f"{self.role_value} review position",
            "findings": findings,
            "proposed_patches": [],
            "unverified_claims": [],
        }
        return PromptResult(
            ok=True,
            text=self._env(
                "<<<ENCOMM_PROPOSAL_REVIEW_START>>>", body,
                "<<<ENCOMM_PROPOSAL_REVIEW_END>>>",
            ),
            session_id="s",
        )


def step(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"ACCEPTANCE FAILED at: {name}")


def main() -> int:
    print("SESSION 021 — OFFLINE DUAL-DOCUMENT ACCEPTANCE (scripted drivers)")
    tmp = Path(tempfile.mkdtemp(prefix="s021-acceptance-"))
    try:
        workspace = tmp / "proposal-workspace"

        # -- 1. workspace + ORIGINAL blueprint import -------------------------
        ProposalWorkspace(workspace).initialize()
        blueprint_src = tmp / "blueprint.md"
        blueprint_src.write_bytes(BLUEPRINT_ORIGINAL)
        record = import_source(workspace, blueprint_src, import_role="master_blueprint")
        template = tmp / "template.md"
        template.write_bytes(TEMPLATE.encode("utf-8"))
        import_source(workspace, template, import_role="application_template")
        step(
            "1. source import (immutable original preserved)",
            record.extraction_status == "ok"
            and master_blueprint_path(workspace).read_bytes()
            .replace(b"\r\n", b"\n").replace(b"\r", b"\n")  # canonical LF form
            == b"# Blueprint\n\nKALHAS evidence base.\n",
            f"original_sha={record.sha256_original[:12]}",
        )

        # -- 2. LIVING BLUEPRINT initialization -------------------------------
        state, migrated = ensure_current_blueprint(workspace)
        current_path = workspace / "00_SOURCE_OF_TRUTH" / "CURRENT_BLUEPRINT.md"
        canonical_original = current_path.read_bytes()
        step(
            "2. living Blueprint created (CURRENT := MASTER exact bytes)",
            migrated
            and current_path.is_file()
            and state.current_blueprint_hash == state.original_blueprint_hash
            and state.current_blueprint_iteration == 0,
            f"hash={state.current_blueprint_hash[:12]}",
        )

        # -- 3. INITIAL GENERATION (3 specialists + ASTRA) ---------------------
        machine = ProposalStateMachine()
        specialists = {
            role: ScriptedDriver(role.value)
            for role in (
                ProposalRole.SCIENTIFIC_REVIEWER,
                ProposalRole.PROPOSAL_ENGINEER,
                ProposalRole.RED_TEAM_REVIEWER,
            )
        }
        astra = ScriptedDriver("ORCHESTRATOR")
        gen = run_initial_generation(
            workspace=workspace,
            state_machine=machine,
            proposal_revision=REVISION,
            specialist_drivers=specialists,
            orchestrator_driver=astra,
        )
        step(
            "3. initial generation (3 specialists + 1 chair; CURRENT kept)",
            gen.outcome is InitialGenerationOutcome.COMPLETED
            and machine.phase is ProposalPhase.IDLE
            and sum(d.specialist_calls for d in specialists.values()) == 3
            and astra.astra_calls == 1
            and current_path.read_bytes() == canonical_original,
            "model_calls=4",
        )
        step(
            "4. both documents durable (CURRENT_BLUEPRINT + MASTER_PROPOSAL)",
            bool(current_path.read_bytes().strip())
            and bool(
                (workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md")
                .read_bytes().strip()
            ),
        )

        # -- 5. ITERATION 1: panel + consensus + ASTRA chair (dual) ------------
        reviewers = {
            role: ScriptedDriver(
                role.value,
                verdict="NEEDS_REVISION",
                severity="high",
                # The scientific evaluator targets the BLUEPRINT: the design
                # itself must change (typed target chain proof).
                finding_target=(
                    "BLUEPRINT"
                    if role is ProposalRole.SCIENTIFIC_REVIEWER
                    else "PROPOSAL"
                ),
            )
            for role in (
                ProposalRole.SCIENTIFIC_REVIEWER,
                ProposalRole.PROPOSAL_ENGINEER,
                ProposalRole.RED_TEAM_REVIEWER,
            )
        }
        it1 = run_panel_chair_iteration(
            workspace=workspace,
            state_machine=machine,
            iteration_number=1,
            proposal_revision=REVISION,
            reviewer_drivers=reviewers,
            orchestrator_driver=astra,
        )
        step(
            "5. full panel iteration (3 parallel + 3 consensus + 1 chair)",
            it1.outcome is PanelChairOutcome.READY_FOR_NEXT_ITERATION
            and it1.model_calls_used == 7,
            f"calls={it1.model_calls_used}",
        )

        # -- 6. findings carry document TARGETS --------------------------------
        it_dir = workspace / "04_REVIEWS" / "iteration_001"
        docket = json.loads(
            (it_dir / "panel_docket.json").read_text(encoding="utf-8")
        )
        targets = {item["item_id"]: item.get("target") for item in docket["items"]}
        consensus = json.loads(
            (it_dir / "panel_consensus.json").read_text(encoding="utf-8")
        )
        row_targets = {row["item_id"]: row.get("target") for row in consensus["rows"]}
        step(
            "6. docket + consensus preserve the document TARGET",
            targets.get("F001") == "BLUEPRINT"
            and row_targets.get("F001") == "BLUEPRINT",
            f"F001 target={targets.get('F001')}",
        )

        # -- 7. chair revised the BLUEPRINT: PAIR COMMIT ------------------------
        new_current = current_path.read_bytes()
        new_master = (workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md").read_bytes()
        original_master_hash = proposal_fingerprint(
            master_blueprint_path(workspace)
        )
        pair = try_load_document_pair_state(workspace)
        step(
            "7. chair revised the living Blueprint → ALL-OR-ROLLBACK pair commit",
            new_current == BLUEPRINT_V2.encode("utf-8")
            and new_master.decode("utf-8").strip() == PROPOSAL_V2.strip()
            and pair is not None
            and pair.iteration == 1
            and pair.blueprint_hash == sha(new_current)
            and pair.proposal_hash == sha(new_master)
            and pair.previous_blueprint_hash == sha(canonical_original),
            f"pair_revision={pair.pair_revision_id[:12] if pair else '—'}",
        )
        step(
            "8. immutable ORIGINAL never mutated by any run",
            proposal_fingerprint(master_blueprint_path(workspace))
            == original_master_hash
            and original_master_hash == state.original_blueprint_hash,
        )

        # -- 9. versions preserve BOTH documents per iteration -----------------
        versions = workspace / "06_VERSIONS"
        expected_versions = (
            "iteration_001_pre_review.md",
            "iteration_001_pre_review_blueprint.md",
            "iteration_001_post_integration.md",
            "iteration_001_post_integration_blueprint.md",
        )
        step(
            "9. 06_VERSIONS freezes BOTH documents (pre + post)",
            all((versions / name).is_file() for name in expected_versions)
            and (versions / expected_versions[3]).read_bytes() == new_current,
        )

        # -- 10. DUAL FRESHNESS reacts to either document -----------------------
        proposal_hash_now = sha(new_master)
        fresh_after_it2_needed = False  # placeholder for clarity below
        del fresh_after_it2_needed
        stale_after_it1 = evaluate_review_freshness(
            workspace=workspace, current_proposal_hash=proposal_hash_now,
        )
        step(
            "10a. post-integration pair change makes reviews STALE",
            stale_after_it1.is_current is False
            and stale_after_it1.blueprint_current is False,
            f"reviewed_bp={stale_after_it1.reviewed_blueprint_hash[:12]}…",
        )
        # An operator proposal edit ALSO breaks freshness (proposal leg).
        mutated = new_master + b"\n<!-- operator edit -->\n"
        (workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md").write_bytes(mutated)
        stale_by_proposal = evaluate_review_freshness(
            workspace=workspace, current_proposal_hash=sha(mutated),
        )
        (workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md").write_bytes(new_master)
        step(
            "10b. a proposal-only change ALSO invalidates freshness",
            stale_by_proposal.is_current is False,
        )

        # -- 11. ITERATION 2: clean pass → gates on the fresh PAIR --------------
        clean = {
            role: ScriptedDriver(role.value, verdict="PASS")
            for role in (
                ProposalRole.SCIENTIFIC_REVIEWER,
                ProposalRole.PROPOSAL_ENGINEER,
                ProposalRole.RED_TEAM_REVIEWER,
            )
        }
        it2 = run_panel_chair_iteration(
            workspace=workspace,
            state_machine=machine,
            iteration_number=2,
            proposal_revision=f"{REVISION}-2",
            reviewer_drivers=clean,
            orchestrator_driver=astra,
        )
        fresh_now = evaluate_review_freshness(
            workspace=workspace,
            current_proposal_hash=proposal_fingerprint(
                workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
            ),
        )
        step(
            "11. second iteration reviews the NEW pair (unchanged pair stays current)",
            it2.outcome is PanelChairOutcome.READY_FOR_HARD_GATES
            and machine.phase is ProposalPhase.HARD_GATE_VALIDATION
            and it2.model_calls_used == 6
            and fresh_now.is_current is True,
            f"calls={it2.model_calls_used}",
        )

        # -- 12. HARD GATES → COMPLETE (existing canonical 14 gates) ------------
        current_hash = proposal_fingerprint(
            workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        )
        evidence: dict = {
            "schema": HARD_GATE_EVIDENCE_SCHEMA,
            "iteration_number": 2,
            "proposal_hash": current_hash,
            "applicability": {},
        }
        evidence.update(json.loads(json.dumps(GOOD_SECTIONS)))
        control = workspace / "05_CONTROL"
        (control / "HARD_GATE_EVIDENCE.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8", newline="\n"
        )
        (control / "UNVERIFIED_CLAIMS.json").write_text(
            '{"unresolved": []}', encoding="utf-8", newline="\n"
        )
        (control / "CONTRADICTIONS.json").write_text(
            '{"unresolved": []}', encoding="utf-8", newline="\n"
        )
        (control / "PAGE_BUDGET.json").write_text(
            json.dumps(
                {
                    "max_pages": 10,
                    "measurement_method": "harness-fixture",
                    "measured_pages": 2,
                },
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        (workspace / "02_EVIDENCE" / "SOURCE_REGISTRY.json").write_text(
            '{"sources": []}', encoding="utf-8", newline="\n"
        )
        (workspace / "02_EVIDENCE" / "CLAIM_LEDGER.json").write_text(
            '{"claims": []}', encoding="utf-8", newline="\n"
        )
        gates = run_hard_gates(
            workspace=workspace,
            state_machine=machine,
            iteration_number=2,
        )
        step(
            "12. hard-gate path remains coherent (COMPLETE on a valid pair)",
            gates.outcome.value == "COMPLETE"
            and machine.phase is ProposalPhase.COMPLETE,
            f"outcome={gates.outcome.value}",
        )

        step(
            "13. pair manifest intact after the full campaign",
            try_load_document_pair_state(workspace) is not None
            and (workspace / DOCUMENT_PAIR_STATE_RELPATH).is_file(),
        )

        print()
        print("DUAL DOCUMENT PROPOSAL ACCEPTANCE PASSED")
        return 0
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
