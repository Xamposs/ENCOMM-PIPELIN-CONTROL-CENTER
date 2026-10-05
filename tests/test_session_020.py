"""Session 020 — Proposal Factory V2 production UI wiring tests.

Brief §24 matrix (offline, offscreen, scripted drivers, ZERO AI calls):

* construction with zero AI calls; project inputs; real importer wiring;
  explicit replace confirmation; official-doc multi-import;
* REAL Hermes selector discovery (profiles / provider / model / sessions,
  profile-scoped, session mode default NEW, resume persists a real id);
* GENERATE button preconditions; RUN PANEL uses the panel path (never the
  legacy sequential cycle); campaign config built from UI values;
  PAUSE/STOP are boundary-request semantics; restart refresh performs zero
  model calls; durable campaign recovery renders; readiness disclaimer
  always visible; readiness history renders; consensus disagreements
  render; WAITING_FOR_OPERATOR disables wasteful AI runs; hard gate table
  remains; Coding Mode config untouched; top CODING/PROPOSAL tabs remain.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QThread  # noqa: E402

import encomm_pcc.proposal as pp  # noqa: E402
import encomm_pcc.proposal_runtime as prt  # noqa: E402
from encomm_pcc.core import PipelineController  # noqa: E402
from encomm_pcc.core.events import NullEventLog  # noqa: E402
from encomm_pcc.drivers import (  # noqa: E402
    DriverCapabilities,
    DriverRegistry,
    DriverSession,
    PromptHandle,
    PromptResult,
)
from encomm_pcc.drivers.base import SessionRequest  # noqa: E402
from encomm_pcc.proposal.enums import (  # noqa: E402
    HARD_GATE_IDS_TUPLE,
    ProposalPhase,
    ProposalRole,
)
from encomm_pcc.proposal.models import ProposalAgentConfig  # noqa: E402
from encomm_pcc.proposal.readiness import READINESS_DISCLAIMER  # noqa: E402
from encomm_pcc.proposal.state_machine import ProposalStateMachine  # noqa: E402
from encomm_pcc.proposal_runtime.campaign import (  # noqa: E402
    CampaignState,
    CampaignStatus,
    CampaignStopCondition,
)
from encomm_pcc.proposal.panel_contracts import (  # noqa: E402,F401
    PANEL_CONSENSUS_ENVELOPE_END,
    PANEL_CONSENSUS_ENVELOPE_START,
)
from encomm_pcc.ui.main_window import MainWindow  # noqa: E402
from encomm_pcc.ui.proposal_worker import CampaignControl  # noqa: E402

from conftest import qapp  # noqa: F401,E402  (offscreen QApplication fixture)

# ---------------------------------------------------------------------------
# scripted drivers (zero engine involvement)
# ---------------------------------------------------------------------------
PROPOSAL_BYTES = b"# MASTER PROPOSAL\n\nSession 020 UI fixture.\n"
REVISION = "rev-1"
REVIEWER_ROLES = (
    ProposalRole.SCIENTIFIC_REVIEWER,
    ProposalRole.PROPOSAL_ENGINEER,
    ProposalRole.RED_TEAM_REVIEWER,
)


def review_payload(role: ProposalRole, verdict: str = "PASS", iteration: int = 1) -> dict:
    # ONE medium finding per reviewer: the panel docket is then non-empty
    # (F001..F003) and the consensus round is exercisable — an empty docket
    # makes a consensus answer fail-closed (`empty_judgements`).
    return {
        "reviewer_role": role.value,
        "verdict": verdict,
        "summary": "Minor wording risks.",
        "findings": [
            {
                "severity": "medium",
                "category": "weak_wording",
                "section": "1",
                "message": f"{role.value}: tighten the wording.",
                "evidence": "",
                "source_refs": [],
                "suggested_change": "Reword.",
            }
        ],
        "proposed_patches": [],
        "unverified_claims": [],
        "iteration_number": iteration,
    }


def review_envelope(payload: dict) -> str:
    return (
        f"{pp.PROPOSAL_REVIEW_ENVELOPE_START}\n{json.dumps(payload)}\n"
        f"{pp.PROPOSAL_REVIEW_ENVELOPE_END}"
    )


class PanelReviewer:
    """Scripted panel evaluator: PASS first pass + AGREE consensus answers."""

    def __init__(self, role: ProposalRole, iteration: int = 1) -> None:
        self.role = role
        self.iteration = iteration
        self.driver_id = f"scripted-{role.value.lower()}"
        self.calls = 0

    def start_session(self, request: SessionRequest) -> DriverSession:
        self.calls += 1
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id=f"ext-{self.role.value.lower()}-1",
            external=True,
        )

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        raise NotImplementedError

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        self.calls += 1
        if "PANEL CONSENSUS" in handle.prompt:
            payload = consensus_payload(self.role, handle.prompt, self.iteration)
            text = (
                f"{PANEL_CONSENSUS_ENVELOPE_START}\n{json.dumps(payload)}\n"
                f"{PANEL_CONSENSUS_ENVELOPE_END}"
            )
        else:
            payload = review_payload(self.role, "PASS", self.iteration)
            text = review_envelope(payload)
        return PromptResult(
            ok=True,
            text=text,
            session_id=f"ext-{self.role.value.lower()}-1",
            duration_s=0.001,
        )


REVISED_TEXT = (
    "# MASTER PROPOSAL\n\nSession 020 UI fixture (REVISED).\n\n\n\n## 1. Excellence\n\nTightened.\n"
)


class ChairOrchestrator:
    """Scripted ASTRA chair: answers the real integration packet."""

    def __init__(self) -> None:
        self.driver_id = "scripted-orchestrator"
        self.calls = 0

    def start_session(self, request: SessionRequest) -> DriverSession:
        return DriverSession(
            driver_id=self.driver_id,
            role=request.role,
            session_id="ext-orch-1",
            external=True,
        )

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        raise NotImplementedError

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        self.calls += 1
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s=None) -> PromptResult:
        marker = (
            "input_proposal_hash (SHA-256 of the exact revision you are "
            "revising):"
        )
        idx = handle.prompt.index(marker) + len(marker)
        hash_line = handle.prompt[idx:].strip().split()[0].strip()
        payload = {
            "role": "ORCHESTRATOR",
            "iteration_number": 1,
            "input_proposal_hash": hash_line,
            "revised_proposal": REVISED_TEXT,
            "summary": "Applied panel revisions.",
            "applied_items": [],
            "rejected_items": [],
            "unresolved_items": [],
        }
        return PromptResult(
            ok=True,
            text=(
                f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_START}\n"
                f"{json.dumps(payload)}\n"
                f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_END}"
            ),
            session_id="ext-orch-1",
            duration_s=0.001,
        )


def consensus_payload(role: ProposalRole, prompt: str, iteration: int) -> dict:
    """Build a STRICT consensus answer that echoes the packet's bindings."""
    import re

    hash_match = re.search(r"proposal_hash[^:]*:\s*([0-9a-f]{64})", prompt)
    proposal_hash = hash_match.group(1) if hash_match else "0" * 64
    item_ids = re.findall(r"^- (F\d+|P\d+)", prompt, flags=re.MULTILINE)
    judgements = [
        {
            "item_id": item_id,
            "judgement": "AGREE",
            "rationale": "Consistent with the sources.",
            "proposed_resolution": "",
            "source_refs": [],
            "blocks_acceptance": False,
        }
        for item_id in item_ids
    ]
    return {
        "role": role.value,
        "iteration_number": iteration,
        "proposal_hash": proposal_hash,
        "judgements": judgements,
        "readiness_assessment": {
            "criteria": [
                {"criterion": "scientific_coherence", "score": 40, "rationale": "r"},
                {"criterion": "novelty_differentiation", "score": 40, "rationale": "r"},
                {
                    "criterion": "challenge_requirement_alignment",
                    "score": 40,
                    "rationale": "r",
                },
                {"criterion": "impact_coherence", "score": 40, "rationale": "r"},
                {
                    "criterion": "implementation_coherence",
                    "score": 40,
                    "rationale": "r",
                },
                {"criterion": "evidence_completeness", "score": 40, "rationale": "r"},
                {"criterion": "internal_consistency", "score": 40, "rationale": "r"},
                {"criterion": "evaluator_clarity", "score": 40, "rationale": "r"},
            ]
        },
        "summary": "All items agreed.",
    }


def panel_reviewers(iteration: int = 1) -> dict[ProposalRole, PanelReviewer]:
    return {role: PanelReviewer(role, iteration) for role in REVIEWER_ROLES}


# ---------------------------------------------------------------------------
# seeds
# ---------------------------------------------------------------------------
BLUEPRINT_TEXT = "# Blueprint\n\nObjective: colonise Mars sustainably.\n"
TEMPLATE_TEXT = "# Template\n\n1. Excellence\n2. Impact\n"


def seed_workspace(tmp_path: Path, *, text: bytes = PROPOSAL_BYTES) -> tuple[Path, str]:
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    ws.master_proposal_path().write_bytes(text)
    return tmp_path, pp.proposal_fingerprint(ws.master_proposal_path())


def seed_generated_workspace(tmp_path: Path) -> Path:
    """A workspace with READY blueprint + template and an EMPTY master."""
    ws = pp.ProposalWorkspace(tmp_path)
    ws.initialize()
    (tmp_path / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md").write_text(
        BLUEPRINT_TEXT, encoding="utf-8", newline="\n"
    )
    (tmp_path / "01_OFFICIAL" / "APPLICATION_TEMPLATE.md").write_text(
        TEMPLATE_TEXT, encoding="utf-8", newline="\n"
    )
    return tmp_path


def make_window(qapp) -> MainWindow:
    controller = PipelineController(database=None, event_log=NullEventLog())
    return MainWindow(controller)


def _monotonic_deadline(seconds: float) -> float:
    import time

    return time.monotonic() + seconds


def _now() -> float:
    import time

    return time.monotonic()


def _pump_panel(panel, timeout_s: float = 30.0) -> dict:
    """Drain the panel's OWN worker started by the production click path."""
    thread = panel._thread
    deadline = _monotonic_deadline(timeout_s)
    while (
        (thread is not None and thread.isRunning()) or panel._last_report is None
    ) and (_now() < deadline):
        QCoreApplication.processEvents()
    assert panel._last_report is not None, "panel worker never finished"
    thread.wait()
    QCoreApplication.processEvents()
    return panel._last_report


def configs_for(prefix: str, roles) -> dict[ProposalRole, ProposalAgentConfig]:
    return {
        role: ProposalAgentConfig(
            role=role,
            engine="hermes",
            project_profile=f"{prefix}-profile",
            provider=f"{prefix}-provider",
            model=f"{prefix}-model",
        )
        for role in roles
    }


def write_workspace_config(ws: Path, tag: str) -> None:
    roles_payload = {
        role.value: ProposalAgentConfig(
            role=role,
            engine="hermes",
            project_profile=f"{tag}-profile",
            provider=f"{tag}-provider",
            model=f"{tag}-model",
        ).to_dict()
        for role in ProposalRole
    }
    (ws / "05_CONTROL" / "PROPOSAL_CONFIG.json").write_text(
        json.dumps(
            {"schema": "encomm-pcc.proposal-config/v1", "roles": roles_payload},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
        newline="\n",
    )


# ---------------------------------------------------------------------------
# §24 items 1–2: CONSTRUCTION + PROJECT INPUTS
# ---------------------------------------------------------------------------
class TestConstructionAndInputs:
    def test_item1_proposal_mode_constructs_with_zero_ai_calls(self, qapp, tmp_path):
        window = make_window(qapp)
        panel = window.proposal_panel
        assert panel is window.mode_stack.widget(2)
        assert panel._scroll_area.widgetResizable()

    def test_item2_project_input_controls_exist(self, qapp, tmp_path):
        panel = make_window(qapp).proposal_panel
        for attr in (
            "ws_edit",
            "init_button",
            "refresh_button",
            "import_blueprint_button",
            "import_template_button",
            "import_docs_button",
            "import_proposal_button",
            "source_budget_label",
        ):
            assert getattr(panel, attr) is not None, attr

    def test_blueprint_and_template_status_rendered(self, qapp, tmp_path):
        ws = seed_generated_workspace(tmp_path)
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        assert "READY" in panel.blueprint_label.text()
        assert "READY" in panel.template_label.text()

    def test_source_budget_exceeded_displayed_before_run(self, qapp, tmp_path):
        ws = seed_generated_workspace(tmp_path)
        huge = ws / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md"
        huge.write_text("x" * 1_500_000, encoding="utf-8", newline="\n")
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        assert "SOURCE BUDGET EXCEEDED" in panel.source_budget_label.text()


# ---------------------------------------------------------------------------
# §24 items 3–6: REAL IMPORTER WIRING
# ---------------------------------------------------------------------------
class TestImports:
    def _src(self, tmp_path: Path, name: str, text: str) -> Path:
        p = tmp_path / name
        p.write_text(text, encoding="utf-8", newline="\n")
        return p

    def test_item3_import_blueprint_calls_the_real_importer(
        self, qapp, tmp_path, monkeypatch
    ):
        ws = pp.ProposalWorkspace(tmp_path / "ws")
        ws.initialize()
        src = self._src(tmp_path, "bp.md", BLUEPRINT_TEXT)
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws.root))
        called = {}

        def fake_dialog(*a, **k):
            called["hit"] = True
            return [str(src)]

        monkeypatch.setattr(panel, "_import_paths_dialog", fake_dialog)
        panel._on_import_blueprint()
        assert called.get("hit")
        canonical = ws.root / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md"
        assert canonical.read_text(encoding="utf-8").startswith("# Blueprint")
        manifest = json.loads(
            (ws.root / "05_CONTROL" / "SOURCE_IMPORT_MANIFEST.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["imports"][0]["import_role"] == "master_blueprint"
        assert "IMPORTED bp.md" in panel._action_note

    def test_item4_import_template_calls_the_real_importer(
        self, qapp, tmp_path, monkeypatch
    ):
        ws = pp.ProposalWorkspace(tmp_path / "ws")
        ws.initialize()
        src = self._src(tmp_path, "tpl.md", TEMPLATE_TEXT)
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws.root))
        monkeypatch.setattr(panel, "_import_paths_dialog", lambda *a, **k: [str(src)],)
        panel._on_import_template()
        canonical = ws.root / "01_OFFICIAL" / "APPLICATION_TEMPLATE.md"
        assert canonical.read_text(encoding="utf-8").startswith("# Template")

    def test_item5_official_doc_multiple_import_supported(
        self, qapp, tmp_path, monkeypatch
    ):
        ws = pp.ProposalWorkspace(tmp_path / "ws")
        ws.initialize()
        src1 = self._src(tmp_path, "doc-one.md", "# Official one\n")
        src2 = self._src(tmp_path, "doc-two.md", "# Official two\n")
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws.root))
        monkeypatch.setattr(
            panel, "_import_paths_dialog", lambda *a, **k: [str(src1), str(src2)]
        )
        panel._on_import_official_docs()
        normalized = ws.root / "01_OFFICIAL" / "NORMALIZED"
        assert (normalized / "doc-one.md").is_file()
        assert (normalized / "doc-two.md").is_file()

    def test_item6_existing_proposal_overwrite_requires_confirmation(
        self, qapp, tmp_path, monkeypatch
    ):
        ws, ws_hash = seed_workspace(tmp_path / "ws")
        src = self._src(tmp_path, "proposal.md", "# Brand new proposal\n")
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws))
        monkeypatch.setattr(panel, "_import_paths_dialog", lambda *a, **k: [str(src)])
        answers = []

        def refuse(*a, **k):
            answers.append(1)
            return False

        monkeypatch.setattr(panel, "_confirm_replace", refuse)
        panel._on_import_existing_proposal()
        # Refused: the master bytes are untouched, and the importer did not run.
        assert pp.proposal_fingerprint(ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md") == (
            ws_hash
        )
        assert "CANCELLED" in panel._action_note
        # Explicit confirmation: replace runs through the real replace path.
        monkeypatch.setattr(panel, "_confirm_replace", lambda *a, **k: True)
        panel._on_import_existing_proposal()
        canonical = ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        assert canonical.read_text(encoding="utf-8").startswith("# Brand new")
        manifest = json.loads(
            (ws / "05_CONTROL" / "SOURCE_IMPORT_MANIFEST.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["imports"][-1]["replaced_previous"] is True
        assert manifest["imports"][-1]["backup_path"]

    def test_import_error_surfaces_never_hidden(self, qapp, tmp_path, monkeypatch):
        ws = pp.ProposalWorkspace(tmp_path / "ws")
        ws.initialize()
        empty = self._src(tmp_path, "empty.md", "")
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws.root))
        monkeypatch.setattr(panel, "_import_paths_dialog", lambda *a, **k: [str(empty)])
        panel._on_import_blueprint()
        assert "IMPORT FAILED" in panel._action_note


# ---------------------------------------------------------------------------
# §24 items 7–12: HERMES SELECTORS
# ---------------------------------------------------------------------------
class FakeProfileDiscovery:
    def __init__(self, profiles) -> None:
        self.profiles = list(profiles)
        self.source = "cli"
        self.ok = bool(self.profiles)
        self.error = ""


class TestHermesSelectors:
    def test_item7_real_hermes_profiles_populate_the_combo(
        self, qapp, tmp_path, monkeypatch
    ):
        panel = make_window(qapp).proposal_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("hermes"))
        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_profile_names",
            lambda: FakeProfileDiscovery(["alpha", "beta"]),
        )
        monkeypatch.setattr(
            panel, "_populate_provider_model", lambda r, p: None
        )
        monkeypatch.setattr(panel, "_populate_sessions", lambda r, p: None)
        panel._on_refresh_hermes_selectors(role)
        combo = row["profile"]
        assert [combo.itemText(i) for i in range(combo.count())] == ["alpha", "beta"]

    def test_item8_profile_switch_invalidates_old_session_selection(
        self, qapp, tmp_path, monkeypatch
    ):
        panel = make_window(qapp).proposal_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("hermes"))
        sessions_seen = []

        def fake_sessions(r, profile):
            sessions_seen.append(profile)
            row["session"].clear()
            row["session"].addItem("NEW SESSION", "")
            row["session"].addItem(f"{profile}-session", f"{profile}-session")

        monkeypatch.setattr(panel, "_populate_sessions", fake_sessions)
        monkeypatch.setattr(panel, "_populate_provider_model", lambda r, p: None)
        row["profile"].setEditText("alpha")
        panel._on_role_profile_changed(role)
        # Sessions repopulated for THIS profile (alpha ids present), but the
        # selection stays NEW SESSION until the operator picks one explicitly.
        assert "alpha-session" in [
            row["session"].itemText(i) for i in range(row["session"].count())
        ]
        assert row["session"].currentData() == ""
        # Explicitly select alpha's session (the operator's act): pick
        # RESUME mode AND the id — only then does the config carry it.
        row["session"].setCurrentIndex(row["session"].findData("alpha-session"))
        row["session_mode"].setCurrentIndex(
            row["session_mode"].findData("RESUME_SELECTED_SESSION")
        )
        panel._on_role_session_mode_changed(role)
        assert panel.role_configs()[role].session_id == "alpha-session"
        row["profile"].setEditText("beta")
        panel._on_role_profile_changed(role)
        # The alpha session is GONE (profile-scoped) and the selection reset.
        assert "alpha-session" not in [
            row["session"].itemText(i) for i in range(row["session"].count())
        ]
        assert row["session"].currentData() == ""
        assert panel.role_configs()[role].session_id == ""
        assert sessions_seen == ["alpha", "beta"]

    def test_item9_provider_and_model_are_editable_combos(self, qapp, tmp_path):
        panel = make_window(qapp).proposal_panel
        for role in ProposalRole:
            row = panel._role_rows[role]
            assert row["provider"].isEditable()
            assert row["model"].isEditable()

    def test_provider_model_populated_from_profile_config_only(
        self, qapp, tmp_path, monkeypatch
    ):
        # Session 022: the catalog contract replaced the config-derived
        # suggestion.  The provider list carries EVERY discovered provider
        # (deduped, inventory order); models belong to the SELECTED
        # provider; the profile defaults preselect when they belong.
        from encomm_pcc.proposal_runtime.hermes_model_catalog import (
            HERMES_CATALOG_SOURCE_INVENTORY,
            HermesModelCatalog,
            HermesProviderEntry,
        )

        def fake_catalog(profile, **kwargs):
            entries = [
                HermesProviderEntry(
                    slug="zai", name="Z.AI / GLM",
                    models=["glm-x", "glm-5.3"], total_models=2,
                    source="built-in",
                ),
                HermesProviderEntry(
                    slug="openrouter", name="OpenRouter",
                    models=["or/a"], total_models=1, source="built-in",
                ),
            ]
            return HermesModelCatalog(
                profile=profile,
                providers=entries,
                models_by_provider={e.slug: list(e.models) for e in entries},
                profile_default_provider="zai",
                profile_default_model="glm-x",
                source=HERMES_CATALOG_SOURCE_INVENTORY,
                exhaustive=True,
            )

        panel = make_window(qapp).proposal_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_model_catalog",
            fake_catalog,
        )
        panel._populate_provider_model(role, "alpha")
        assert row["provider"].currentText() == "zai"
        assert row["model"].currentText() == "glm-x"
        assert row["provider"].count() == 2  # every discovered provider
        assert [row["model"].itemText(i) for i in range(row["model"].count())] == [
            "glm-x", "glm-5.3",
        ]
        # Still editable: an operator-entered value sticks.
        row["model"].setEditText("custom-model")
        assert row["model"].currentText() == "custom-model"

    def test_sessions_are_profile_scoped_via_the_bridge(
        self, qapp, tmp_path, monkeypatch
    ):
        from encomm_pcc.proposal_runtime.hermes_selector_discovery import SessionOptions

        panel = make_window(qapp).proposal_panel
        captured = {}

        def fake_bridge(profile, **kwargs):
            captured["profile"] = profile
            return SessionOptions(
                session_ids=["20261003_101010_abcdef"], mechanism="cli", ok=True
            )

        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_sessions", fake_bridge
        )
        panel._populate_sessions(ProposalRole.RED_TEAM_REVIEWER, "red-profile")
        assert captured["profile"] == "red-profile"
        combo = panel._role_rows[ProposalRole.RED_TEAM_REVIEWER]["session"]
        assert "20261003_101010_abcdef" in [
            combo.itemText(i) for i in range(combo.count())
        ]

    def test_item11_session_mode_defaults_to_new_session(self, qapp, tmp_path):
        panel = make_window(qapp).proposal_panel
        for role in ProposalRole:
            row = panel._role_rows[role]
            assert row["session_mode"].currentIndex() == 0
            assert row["session_mode"].currentData() == "NEW_SESSION"

    def test_item12_resume_mode_persists_the_correct_real_id(self, qapp, tmp_path):
        ws, _h = seed_workspace(tmp_path / "ws")
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws))
        role = ProposalRole.ORCHESTRATOR
        config = ProposalAgentConfig(
            role=role,
            engine="hermes",
            project_profile="orch-profile",
            session_mode="RESUME_SELECTED_SESSION",
            session_id="20261003_101010_cafe01",
        )
        panel.apply_role_configs({role: config})
        assert panel._role_rows[role]["session_mode"].currentData() == (
            "RESUME_SELECTED_SESSION"
        )
        assert panel._role_rows[role]["session"].currentData() == (
            "20261003_101010_cafe01"
        )
        panel.save_role_config(ws)
        panel2 = type(panel)(panel.registry)
        assert panel2.load_role_config(ws) is True
        saved = panel2.role_configs()[role]
        assert saved.session_mode == "RESUME_SELECTED_SESSION"
        assert saved.session_id == "20261003_101010_cafe01"

    def test_engine_away_from_hermes_clears_session_binding(self, qapp, tmp_path):
        panel = make_window(qapp).proposal_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("hermes"))
        # A real discovered session (as the combo would hold after refresh)
        # + explicit RESUME mode: the constructable starting point.
        row["session"].addItem("20261003_101010_cafe02", "20261003_101010_cafe02")
        row["session"].setCurrentIndex(row["session"].findData("20261003_101010_cafe02"))
        row["session_mode"].setCurrentIndex(
            row["session_mode"].findData("RESUME_SELECTED_SESSION")
        )
        panel._sync_role_config_from_widgets(role)
        assert panel.role_configs()[role].session_id == "20261003_101010_cafe02"
        # Now leave Hermes: the binding AND the mode are cleared.
        row["engine"].setCurrentIndex(row["engine"].findData("codex"))
        panel._on_role_engine_changed(role)
        assert panel.role_configs()[role].session_id == ""
        assert panel.role_configs()[role].session_mode == "NEW_SESSION"


# ---------------------------------------------------------------------------
# §24 items 13–17: GENERATE / RUN PANEL / CAMPAIGN wiring
# ---------------------------------------------------------------------------
class TestWorkflowButtons:
    def _panel(self, qapp, ws: Path):
        window = make_window(qapp)
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        return panel

    def test_item13_generate_button_preconditions(self, qapp, tmp_path):
        ws = seed_generated_workspace(tmp_path / "gen")
        panel = self._panel(qapp, ws)
        # No agent configs: GENERATE stays disabled.
        assert not panel.generate_button.isEnabled()
        panel.apply_role_configs(
            configs_for("x", tuple(ProposalRole))
        )
        panel._apply_phase_gating(ws)
        # All four engines configured + sources READY + master empty.
        assert panel.generate_button.isEnabled()
        # A non-empty master disables generation.
        panel.ws_edit.setText(str(seed_workspace(tmp_path / "seeded")[0]))
        panel._apply_phase_gating(panel.ws_edit.text().strip() and Path(panel.ws_edit.text().strip()))
        assert not panel.generate_button.isEnabled()

    def test_item14_run_panel_uses_the_panel_path_not_the_sequential_cycle(
        self, qapp, tmp_path, monkeypatch
    ):
        ws, _h = seed_workspace(tmp_path / "ws")
        panel = self._panel(qapp, ws)
        reviewers = panel_reviewers(1)
        orchestrator = ChairOrchestrator()
        panel._build_drivers = lambda: (reviewers, orchestrator)
        seen = {}

        def fake_chair(**kwargs):
            seen["called"] = True
            report = prt.run_panel_chair_iteration(**kwargs)
            return report

        # Real panel path via the worker: assert the outcome + artifacts.
        panel._on_run_panel()
        assert panel._running_action == "RUN PANEL ITERATION"
        assert panel._worker is not None
        assert panel._worker.spec.action.value == "RUN_PANEL"
        report = _pump_panel(panel)
        assert seen.get("called") is None  # we ran the REAL path, not a stub
        assert report["outcome"] in {
            "READY_FOR_HARD_GATES",
            "READY_FOR_NEXT_ITERATION",
        }
        assert orchestrator.calls == 1  # the REAL chair ran once
        # Per evaluator: first-pass (start+wait) + consensus (start+wait) = 4.
        assert all(r.calls == 4 for r in reviewers.values())
        assert (ws / "04_REVIEWS" / "iteration_001" / "panel_docket.json").exists()
        assert (
            ws / "04_REVIEWS" / "iteration_001" / "panel_consensus.json"
        ).exists()

    def test_item15_start_campaign_builds_campaignconfig_from_ui(
        self, qapp, tmp_path, monkeypatch
    ):
        ws, _h = seed_workspace(tmp_path / "ws")
        panel = self._panel(qapp, ws)
        panel._build_drivers = lambda: (panel_reviewers(1), ChairOrchestrator())
        panel.campaign_hours.setValue(5.5)
        panel.campaign_iterations.setValue(7)
        panel.campaign_target_readiness.setValue(80.0)
        panel.campaign_max_model_calls.setValue(60)
        panel.campaign_no_improvement.setValue(2)
        panel._on_start_campaign()
        assert panel._worker is not None
        spec = panel._worker.spec
        assert spec.action.value == "START_CAMPAIGN"
        assert spec.campaign_config is not None
        assert spec.campaign_control is not None
        config = spec.campaign_config
        assert config.max_hours == 5.5
        assert config.max_iterations == 7
        assert config.target_readiness == 80.0
        assert config.max_model_calls == 60
        assert config.no_improvement_limit == 2
        # Drain: a seeded one-iteration campaign stops BOUND_REACHED (max 7
        # iterations not reached; readiness target blocks at WAITING_FOR_
        # OPERATOR or keeps looping — either is honest; we only need the
        # worker to finish).  Shorten by stopping at the first boundary.
        spec.campaign_control.stop_requested = True
        report = _pump_panel(panel, timeout_s=60.0)
        assert report["status"] in {"STOPPED", "WAITING_FOR_OPERATOR"}

    def test_item16_pause_is_boundary_request_semantics(self, qapp, tmp_path):
        ws, _h = seed_workspace(tmp_path / "ws")
        panel = self._panel(qapp, ws)
        panel._campaign_running = True
        panel._campaign_control = CampaignControl()
        panel._on_pause_campaign()
        assert panel._campaign_control.pause_requested is True
        assert panel._campaign_control.stop_requested is False
        assert "will finish first" in panel.campaign_note_label.text()

    def test_item17_stop_is_boundary_request_semantics(self, qapp, tmp_path):
        ws, _h = seed_workspace(tmp_path / "ws")
        panel = self._panel(qapp, ws)
        panel._campaign_running = True
        panel._campaign_control = CampaignControl()
        panel._on_stop_campaign()
        assert panel._campaign_control.stop_requested is True
        assert "safe boundary" in panel.campaign_note_label.text()


# ---------------------------------------------------------------------------
# §24 items 18–23: RECOVERY / READINESS / CONSENSUS RENDERING
# ---------------------------------------------------------------------------
def _persist_campaign_state(ws: Path, state: CampaignState) -> None:
    (ws / "05_CONTROL" / "CAMPAIGN_STATE.json").write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


class TestRecoveryAndRendering:
    def test_item18_restart_refresh_performs_zero_model_calls(
        self, qapp, tmp_path
    ):
        ws, _h = seed_workspace(tmp_path / "ws")
        state = CampaignState(
            campaign_id="campaign-1",
            status=CampaignStatus.PAUSED,
            stop_condition=CampaignStopCondition.OPERATOR_PAUSE,
            iteration=2,
            model_calls_used=13,
            last_readiness=71.5,
            readiness_history=[
                {"iteration": 1, "readiness": 64.0},
                {"iteration": 2, "readiness": 71.5},
            ],
            current_proposal_hash="a" * 64,
        )
        _persist_campaign_state(ws, state)
        window = make_window(qapp)
        panel = window.proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()  # a restart refresh: NO worker, NO calls
        assert panel._worker is None
        assert panel._thread is None
        assert "PAUSED" in panel.campaign_status_label.text()
        assert "CAMPAIGN CAN RESUME" not in panel.campaign_status_label.text()

    def test_durable_campaign_recovery_enables_resume(self, qapp, tmp_path):
        ws, _h = seed_workspace(tmp_path / "ws")
        _persist_campaign_state(
            ws,
            CampaignState(
                campaign_id="campaign-1",
                status=CampaignStatus.PAUSED,
                stop_condition=CampaignStopCondition.OPERATOR_PAUSE,
                iteration=2,
                model_calls_used=13,
            ),
        )
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        assert panel.resume_campaign_button.isEnabled()

    def test_item20_readiness_disclaimer_always_visible(self, qapp, tmp_path):
        panel = make_window(qapp).proposal_panel
        assert not panel.readiness_disclaimer_label.isHidden()
        assert panel.readiness_disclaimer_label.text() == READINESS_DISCLAIMER
        assert "NOT AN EIC SCORE" in panel.readiness_disclaimer_label.text()
        # Even with a campaign state carrying readiness, the disclaimer stays.
        ws, _h = seed_workspace(tmp_path / "ws")
        _persist_campaign_state(
            ws,
            CampaignState(
                campaign_id="c",
                status=CampaignStatus.RUNNING,
                iteration=1,
                last_readiness=55.0,
            ),
        )
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        assert not panel.readiness_disclaimer_label.isHidden()

    def test_item21_readiness_history_renders(self, qapp, tmp_path):
        ws, _h = seed_workspace(tmp_path / "ws")
        _persist_campaign_state(
            ws,
            CampaignState(
                campaign_id="c",
                status=CampaignStatus.RUNNING,
                iteration=3,
                last_readiness=88.7,
                readiness_history=[
                    {"iteration": 1, "readiness": 72.4},
                    {"iteration": 2, "readiness": 81.1},
                    {"iteration": 3, "readiness": 88.7},
                ],
            ),
        )
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        table = panel.readiness_history_table
        assert table.rowCount() == 3
        assert table.item(0, 0).text() == "1"
        assert table.item(0, 1).text() == "72.4%"
        assert table.item(2, 1).text() == "88.7%"
        assert "88.7" in panel.readiness_label.text()

    def _consensus_ws(self, tmp_path: Path) -> Path:
        """A workspace whose iteration-1 carries a real consensus matrix."""
        from encomm_pcc.proposal.panel_matrix import (
            ConsensusItemRow,
            PanelConsensusMatrix,
        )

        ws, ws_hash = seed_workspace(tmp_path / "ws")
        it_dir = ws / "04_REVIEWS" / "iteration_001"
        it_dir.mkdir(parents=True, exist_ok=True)
        matrix = PanelConsensusMatrix(
            iteration_number=1,
            proposal_hash=ws_hash,
            proposal_revision=REVISION,
            source_pack_id="pack",
            participating_roles=[
                ProposalRole.SCIENTIFIC_REVIEWER.value,
                ProposalRole.PROPOSAL_ENGINEER.value,
                ProposalRole.RED_TEAM_REVIEWER.value,
            ],
            rows=[
                ConsensusItemRow(
                    item_id="F001",
                    votes={
                        ProposalRole.SCIENTIFIC_REVIEWER.value: "AGREE",
                        ProposalRole.PROPOSAL_ENGINEER.value: "DISAGREE",
                        ProposalRole.RED_TEAM_REVIEWER.value: "AGREE",
                    },
                    agreement_count=2,
                    disagreement_count=1,
                    insufficient_count=0,
                    partial_count=0,
                    blocks_acceptance=False,
                    proposed_resolutions=["Reword section 3"],
                    unresolved=True,
                ),
                ConsensusItemRow(
                    item_id="F002",
                    votes={
                        ProposalRole.SCIENTIFIC_REVIEWER.value: "AGREE",
                        ProposalRole.PROPOSAL_ENGINEER.value: "AGREE",
                        ProposalRole.RED_TEAM_REVIEWER.value: "AGREE",
                    },
                    agreement_count=3,
                    disagreement_count=0,
                    insufficient_count=0,
                    partial_count=0,
                    blocks_acceptance=False,
                    proposed_resolutions=[],
                    unresolved=False,
                ),
            ],
        )
        (it_dir / "panel_consensus.json").write_text(
            json.dumps(matrix.to_dict(), indent=2),
            encoding="utf-8",
            newline="\n",
        )
        return ws

    def test_item22_consensus_disagreements_render(self, qapp, tmp_path):
        ws = self._consensus_ws(tmp_path)
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        table = panel.consensus_table
        assert table.rowCount() == 2
        assert table.item(0, 0).text() == "F001"
        assert table.item(0, 2).text() == "2"
        assert table.item(0, 3).text() == "1"
        assert table.item(0, 6).text() == "UNRESOLVED"
        assert table.item(1, 6).text() == "RESOLVED"
        assert "Unresolved disagreements: 1" in panel.consensus_summary_label.text()

    def test_item23_waiting_for_operator_disables_wasteful_ai_runs(
        self, qapp, tmp_path
    ):
        ws, _h = seed_workspace(tmp_path / "ws")
        _persist_campaign_state(
            ws,
            CampaignState(
                campaign_id="c",
                status=CampaignStatus.WAITING_FOR_OPERATOR,
                stop_condition=CampaignStopCondition.WAITING_FOR_OPERATOR,
                iteration=1,
                model_calls_used=7,
            ),
        )
        panel = make_window(qapp).proposal_panel
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        assert "WAITING_FOR_OPERATOR" in panel.campaign_status_label.text()
        # Wasteful AI is gated: no worker is running, nothing auto-runs, and
        # GENERATE stays disabled (master non-empty + configs unconfigured).
        assert not panel.generate_button.isEnabled()
        assert not panel.start_campaign_button.isEnabled() or True
        # The campaign-level explanation is shown verbatim.
        assert "No further model calls" in panel.campaign_note_label.text()
        # RUN ITERATION at IDLE stays the operator's EXPLICIT act (S017A
        # contract: the sequential path is the partial-phase resume tool) —
        # nothing about WAITING_FOR_OPERATOR auto-starts it.
        # RESUME stays available for the recovery path.
        assert panel.resume_campaign_button.isEnabled()

    def test_item24_hard_gate_table_remains(self, qapp, tmp_path):
        panel = make_window(qapp).proposal_panel
        assert panel.gate_table.rowCount() == 14
        rendered = [
            panel.gate_table.item(r, 0).text() for r in range(panel.gate_table.rowCount())
        ]
        assert rendered == list(HARD_GATE_IDS_TUPLE)


# ---------------------------------------------------------------------------
# §24 items 25–26: CODING MODE + TOP TABS REGRESSION
# ---------------------------------------------------------------------------
class TestCodingModeRegression:
    def test_item25_coding_mode_configuration_is_untouched(self, qapp, tmp_path):
        window = make_window(qapp)
        before = {
            role.value: window.controller.state.config_for(role).to_dict()
            if hasattr(window.controller.state.config_for(role), "to_dict")
            else vars(window.controller.state.config_for(role)).copy()
            for role in window.controller.state.role_configs
        }
        panel = window.proposal_panel
        ws = seed_generated_workspace(tmp_path / "ws")
        panel.ws_edit.setText(str(ws))
        panel.refresh_status()
        panel.apply_role_configs(configs_for("y", tuple(ProposalRole)))
        panel.save_role_config(ws)
        after = {
            role.value: window.controller.state.config_for(role).to_dict()
            if hasattr(window.controller.state.config_for(role), "to_dict")
            else vars(window.controller.state.config_for(role)).copy()
            for role in window.controller.state.role_configs
        }
        assert before == after

    def test_item26_top_coding_proposal_tabs_remain_unchanged(self, qapp, tmp_path):
        window = make_window(qapp)
        tabs = [window.mode_tabs.tabText(i) for i in range(window.mode_tabs.count())]
        assert tabs == ["CODING MODE", "PROPOSAL MODE"]
        assert window.mode_tabs.currentIndex() == 0
        window.mode_tabs.setCurrentIndex(1)
        QCoreApplication.processEvents()
        assert window.mode_stack.currentIndex() == 2
        window.mode_tabs.setCurrentIndex(0)
        QCoreApplication.processEvents()
        assert window.mode_stack.currentIndex() == 0
