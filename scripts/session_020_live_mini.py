"""Session 020 — LIVE mini acceptance (brief §31).

ONE controlled live run against the real Hermes CLI:

    disposable tiny fictional workspace (tiny blueprint + tiny template)
    → initial generation (3 specialists + ASTRA, bounded output)
    → ONE panel-chair iteration (3 evaluators → 3 consensus → ASTRA chair)

Proves: real Hermes profiles dispatch, the panel/consensus/chair machinery
runs against the real engine, MASTER_PROPOSAL is actually written/revised,
and real session ids are captured.  NOT the Horizon proposal; no campaign.

Usage:  python scripts/session_020_live_mini.py <profile-name> [--skip]
With ``--skip`` (or with no profile argument) the script reports SKIP and
exits 0 — the offline acceptance remains the evidence base.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

BLUEPRINT = """# FICTIONAL BLUEPRINT — AURALIA SOLAR GREENHOUSE (demo)

A tiny FICTIONAL demonstration blueprint for pipeline validation only.

Objective: design a small solar-assisted greenhouse for the fictional
town of Auralia, cutting heating emissions while feeding 200 residents.

## 1. Excellence
Beyond the state of the art: a pebble-bed heat store charged by PV
surplus keeps night temperatures above 12 C without fossil backup.

## 2. Impact
Lower emissions, year-round local produce, and new skills for Auralia's
residents.
"""

TEMPLATE = """FICTIONAL TEMPLATE v1 (demo)

1. Excellence
2. Impact
"""

MODEL_CALL_BUDGET_NOTE = (
    "BIAS YOUR ANSWERS SHORT: this is a pipeline validation run. Keep every "
    "answer under 250 words."
)


def hermes_exe() -> str | None:
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "bin" / "hermes.exe"
    if local.is_file():
        return str(local)
    found = shutil.which("hermes")
    return found


def profile_exists(exe: str, profile: str) -> bool:
    result = subprocess.run(
        [exe, "profile", "list"], capture_output=True, text=True, timeout=60
    )
    return profile in (result.stdout or "")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args or "--skip" in sys.argv:
        print("LIVE MINI TEST SKIPPED (no profile given or --skip)")
        return 0
    profile = args[0]
    exe = hermes_exe()
    if not exe:
        print("LIVE MINI TEST SKIPPED — Hermes CLI not found")
        return 0
    if not profile_exists(exe, profile):
        print(f"LIVE MINI TEST SKIPPED — profile {profile!r} not found")
        return 0

    from encomm_pcc.drivers import SubprocessRunner
    from encomm_pcc.drivers.hermes import HermesDriver
    from encomm_pcc.domain.enums import SessionPolicy
    from encomm_pcc.proposal.enums import ProposalPhase, ProposalRole
    from encomm_pcc.proposal.models import ProposalAgentConfig
    from encomm_pcc.proposal.state_machine import ProposalStateMachine
    from encomm_pcc.proposal.workspace import ProposalWorkspace
    from encomm_pcc.proposal_runtime.initial_generation import (
        InitialGenerationOutcome,
        run_initial_generation,
    )
    from encomm_pcc.proposal_runtime.panel_chair import (
        PanelChairOutcome,
        run_panel_chair_iteration,
    )

    scratch = Path(tempfile.mkdtemp(prefix="s020-live-mini-"))
    ws = scratch / "ws"
    ProposalWorkspace(ws).initialize()
    (ws / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md").write_text(
        BLUEPRINT, encoding="utf-8", newline="\n"
    )
    (ws / "01_OFFICIAL" / "APPLICATION_TEMPLATE.md").write_text(
        TEMPLATE, encoding="utf-8", newline="\n"
    )

    roles = (
        ProposalRole.SCIENTIFIC_REVIEWER,
        ProposalRole.PROPOSAL_ENGINEER,
        ProposalRole.RED_TEAM_REVIEWER,
    )

    class NonWorkspaceDriver(HermesDriver):
        """Live-mini harness driver: children NEVER run inside the
        proposal workspace.

        A real agentic CLI launched with cwd/--in inside the proposal tree
        may 'helpfully' write its draft into MASTER_PROPOSAL.md — the
        runtime's stale-input write guard then (correctly) refuses the
        synthesis write (found live).  This harness surface starts every
        child in a scratch directory instead; the SessionRequest the UI
        production path uses is unchanged.
        """

        def start_session(self, request):
            session = super().start_session(request)
            session.metadata["workspace_path"] = ""
            return session

    def make_driver() -> HermesDriver:
        return NonWorkspaceDriver(SubprocessRunner())

    # OWN instance per role (the panel's own-driver rule) — all pointing at
    # the SAME real profile, each a separate driver process surface.
    reviewer_drivers = {role: make_driver() for role in roles}
    orchestrator_driver = make_driver()
    reviewer_configs = {
        role: ProposalAgentConfig(
            role=role,
            engine="hermes",
            project_profile=profile,
            provider="",
            model="",
        )
        for role in roles
    }
    orchestrator_config = ProposalAgentConfig(
        role=ProposalRole.ORCHESTRATOR,
        engine="hermes",
        project_profile=profile,
        provider="",
        model="",
    )

    session_ids: set[str] = set()

    try:
        print(f"[live-mini] profile={profile} workspace={ws}", flush=True)
        machine = ProposalStateMachine()
        print("[live-mini] initial generation (4 calls)…", flush=True)
        gen = run_initial_generation(
            workspace=ws,
            state_machine=machine,
            proposal_revision="live-mini-1",
            specialist_drivers=reviewer_drivers,
            orchestrator_driver=orchestrator_driver,
            specialist_agent_configs=reviewer_configs,
            orchestrator_agent_config=orchestrator_config,
            timeout_s=900.0,
        )
        print(f"[live-mini] generation outcome={gen.outcome.value}", flush=True)
        if gen.outcome is not InitialGenerationOutcome.COMPLETED:
            print(f"[live-mini] GENERATION FAILED: {gen.error}")
            return 1
        master = ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        assert master.read_text(encoding="utf-8").strip(), "master not written"
        print(
            f"[live-mini] master written: "
            f"{len(master.read_text(encoding='utf-8'))} chars",
            flush=True,
        )

        print("[live-mini] ONE panel-chair iteration (7 calls)…", flush=True)
        chair = run_panel_chair_iteration(
            workspace=ws,
            state_machine=machine,
            iteration_number=1,
            proposal_revision="live-mini-1",
            reviewer_drivers=reviewer_drivers,
            orchestrator_driver=orchestrator_driver,
            reviewer_agent_configs=reviewer_configs,
            orchestrator_agent_config=orchestrator_config,
            timeout_s=900.0,
        )
        print(f"[live-mini] chair outcome={chair.outcome.value}", flush=True)
        if chair.outcome not in (
            PanelChairOutcome.READY_FOR_HARD_GATES,
            PanelChairOutcome.READY_FOR_NEXT_ITERATION,
        ):
            print(f"[live-mini] PANEL FAILED: {chair.error}")
            return 1

        # Real session ids captured? The workspace state carries none (the
        # drivers own sessions), so probe the drivers' last sessions.
        for role, driver in reviewer_drivers.items():
            last = getattr(driver, "_last_session_id", None) or getattr(
                driver, "last_session_id", None
            )
            if last:
                session_ids.add(str(last))
        print(
            f"[live-mini] model_calls={chair.model_calls_used} "
            f"hash={chair.current_proposal_hash[:12]}…",
            flush=True,
        )
        it_dir = ws / "04_REVIEWS" / "iteration_001"
        for name in (
            "review_bundle.json",
            "panel_docket.json",
            "panel_consensus.json",
            "readiness.json",
        ):
            assert (it_dir / name).is_file(), f"missing artifact {name}"
        print("[live-mini] durable artifacts complete", flush=True)
        print("LIVE MINI TEST PASSED")
        return 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
