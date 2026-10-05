"""Proposal Mode — the operator-facing production surface (Sessions 017–020).

A THIRD MainWindow surface (mode_stack index 2).  It composes the existing
Proposal Mode backend (``encomm_pcc.proposal`` + ``encomm_pcc.proposal_runtime``)
into operator controls; it implements NO orchestration logic of its own.

Session 020 layout (brief §3) — vertically scrollable sections, in order:

A. PROJECT INPUTS  — workspace + the REAL ``source_import`` importers
   (blueprint / template / official docs / existing proposal; explicit
   replace confirmation; extraction warnings surfaced; source budget shown
   BEFORE any AI action);
B. AGENTS          — the four proposal roles with REAL Hermes selector
   discovery (profiles / the authoritative provider-model inventory /
   profile-scoped sessions; combos stay EDITABLE — nothing is invented).
   Session 022: selecting a provider repopulates its models immediately;
   controls an engine cannot use are disabled N/A (capability-gated);
C. PANEL / CAMPAIGN— GENERATE INITIAL PROPOSAL, RUN PANEL ITERATION (the
   REAL panel-chair path, never the legacy sequential cycle), RUN HARD
   GATES, and the bounded AUTONOMOUS PANEL CAMPAIGN with boundary-only
   PAUSE / RESUME / STOP;
D. CURRENT STATE & READINESS — phase, campaign status, iteration, hash,
   model calls, elapsed time, the advisory INTERNAL READINESS (always
   labelled ``INTERNAL READINESS — NOT AN EIC SCORE``) + history;
E. PANEL RESULTS   — per-evaluator first-pass verdicts + the persisted
   consensus matrix (disagreements, blocking flags, unresolved count);
F. HARD GATES      — the 14-gate renderer (unchanged semantics);
G. EVIDENCE        — the six operator-authored evidence surfaces.

Honesty rule: every status rendered traces to a real durable artifact;
WARN renders INCOMPLETE, BLOCKED renders BLOCKED, nothing is ever faked
green.  Recovery is READ-ONLY: a restart never auto-runs AI.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..drivers.base import BaseDriver
from ..drivers.registry import DriverRegistry
from ..domain.enums import SessionPolicy
from ..proposal.enums import (
    HARD_GATE_IDS_TUPLE,
    ProposalPhase,
    ProposalRole,
)
from ..proposal.models import ProposalAgentConfig
from ..proposal.source_budget import (
    DEFAULT_SOURCE_BUDGET_CHARS,
    SourceBudget,
    SourceBudgetExceededError,
    check_source_budget,
)
from ..proposal.source_import import (
    SourceImportError,
    blueprint_status,
    import_proposal,
    import_source,
)
from ..proposal.state_machine import ProposalStateMachine
from ..proposal.workspace import ProposalWorkspace
from ..proposal_runtime import (
    HARD_GATES_SNAPSHOT_FILENAME,
    load_workspace_status,
)
from ..proposal_runtime.campaign import (
    CAMPAIGN_STATE_FILENAME,
    CampaignConfig,
    CampaignStatus,
    load_campaign_state,
)
from ..proposal_runtime.hermes_model_catalog import (
    discover_hermes_model_catalog,
)
from ..proposal_runtime.hermes_selector_discovery import (
    discover_hermes_profile_names,
    discover_hermes_sessions,
)
from ..proposal.panel_matrix import PanelConsensusMatrix
from .proposal_worker import (
    CampaignControl,
    ProposalRunSpec,
    ProposalWorkerAction,
    start_proposal_worker,
)
from .readiness_disclaimer import READINESS_DISCLAIMER_LABEL

__all__ = ["ProposalModePanel", "PROPOSAL_CONFIG_FILENAME"]

PROPOSAL_CONFIG_FILENAME = "PROPOSAL_CONFIG.json"

#: The six operator-authored evidence surfaces (brief §12) — render status only.
_EVIDENCE_FILES: tuple[tuple[str, str], ...] = (
    ("HARD_GATE_EVIDENCE.json", "05_CONTROL"),
    ("SOURCE_REGISTRY.json", "02_EVIDENCE"),
    ("CLAIM_LEDGER.json", "02_EVIDENCE"),
    ("UNVERIFIED_CLAIMS.json", "05_CONTROL"),
    ("PAGE_BUDGET.json", "05_CONTROL"),
    ("CONTRADICTIONS.json", "05_CONTROL"),
)

_REVIEW_FILES: tuple[tuple[str, str], ...] = (
    ("SCIENTIFIC", "scientific_review.json"),
    ("PROPOSAL ENGINEER", "implementation_review.json"),
    ("RED TEAM", "red_team_review.json"),
    ("INTEGRATION", "integration_result.json"),
)

_BUNDLE_FILE = "review_bundle.json"
_HARD_GATES_FILE = "hard_gates.json"
_PANEL_CONSENSUS_FILE = "panel_consensus.json"
_READINESS_FILE = "readiness.json"

#: Phases from which the production V2 PANEL iteration may start: the EXACT
#: entry phases ``run_parallel_panel_review_cycle()`` accepts (IDLE fresh or
#: REVISION_REQUIRED next-iteration).  Partial-phase resume belongs to the
#: legacy sequential RUN ITERATION button (unchanged).
_PANEL_RUN_PHASES: frozenset[ProposalPhase] = frozenset(
    {ProposalPhase.IDLE, ProposalPhase.REVISION_REQUIRED}
)

#: Phases from which RUN ITERATION (legacy sequential) may start (Session
#: 017A §10 — the exact entry phases ``run_review_cycle()`` accepts).
_ITERATION_RUN_PHASES: frozenset[ProposalPhase] = frozenset(
    {
        ProposalPhase.IDLE,
        ProposalPhase.SOURCE_VALIDATION,
        ProposalPhase.SCIENTIFIC_REVIEW,
        ProposalPhase.IMPLEMENTATION_REVIEW,
        ProposalPhase.RED_TEAM_REVIEW,
        ProposalPhase.REVISION_REQUIRED,
    }
)

#: Hermes engine id (selectors only apply to it; other engines keep the
#: plain editable fields).
_HERMES_ENGINE_ID = "hermes"
_CODEX_ENGINE_ID = "codex"

#: Session 022: Codex exposes NO model-listing command (verified against the
#: installed CLI), so its model field stays EDITABLE and this placeholder
#: tells the operator exactly what to enter.  It is display-only: the sync
#: never persists the placeholder text as a model value.
_CODEX_MODEL_PLACEHOLDER = "Enter exact Codex model id available to this account"

#: Operator-facing role labels (brief §7 — ASTRA is the panel chair label).
_ROLE_LABELS: dict[ProposalRole, str] = {
    ProposalRole.ORCHESTRATOR: "ASTRA / ORCHESTRATOR — PANEL CHAIR",
    ProposalRole.SCIENTIFIC_REVIEWER: "SCIENTIFIC EVALUATOR",
    ProposalRole.PROPOSAL_ENGINEER: "PROPOSAL / IMPLEMENTATION EVALUATOR",
    ProposalRole.RED_TEAM_REVIEWER: "RED TEAM EVALUATOR",
}

_SESSION_MODE_NEW = "NEW SESSION"
_SESSION_MODE_RESUME = "RESUME SELECTED SESSION"

#: Campaign control-boundary UX text (brief §13) — never instant STOPPED.
_PAUSE_REQUEST_TEXT = (
    "Pause requested — current model call/stage will finish first."
)
_STOP_REQUEST_TEXT = (
    "Stop requested — stopping at next safe boundary."
)

#: WAITING_FOR_OPERATOR explanation (brief §21).
_WAITING_FOR_OPERATOR_TEXT = (
    "Campaign paused because deterministic hard gates require operator "
    "evidence. No further model calls will be made until the evidence "
    "issue is resolved."
)

_IMPORT_SUFFIX_FILTER = (
    "Importable sources (*.md *.txt *.pdf *.docx *.rtf);;"
    "Markdown (*.md);;Text (*.txt);;PDF (*.pdf);;Word (*.docx);;RTF (*.rtf)"
)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Deterministic atomic JSON write (same convention as the runtime)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    data = (text + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-propcfg-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


class ProposalModePanel(QWidget):
    """Operator surface for the Proposal Mode review/gate pipeline."""

    #: Wired by MainWindow: return to Coding Simple Mode (index 0).
    request_coding: Any = None

    def __init__(
        self,
        registry: DriverRegistry,
        workspace_root: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry  # the SAME real registry (driver ids only)
        self.workspace_root = Path(workspace_root) if workspace_root else None
        self._machine: ProposalStateMachine | None = None
        #: Session 017A: the workspace the in-memory machine was recovered
        #: from — the machine is NEVER carried across workspaces.
        self._machine_workspace: Path | None = None
        #: Session 017A §9: the workspace whose PROPOSAL_CONFIG.json was
        #: already auto-loaded (once per workspace selection; never re-read
        #: mid-run so operator edits are not clobbered).
        self._config_loaded_for: Path | None = None
        #: Session 022A: the expensive inventory catalog is discovered ONCE
        #: per (role, profile) and reused across provider switches; it is
        #: invalidated on profile change, explicit REFRESH, and engine-away-
        #: from-Hermes, and NEVER persisted into PROPOSAL_CONFIG.json.
        self._catalog_cache: dict[tuple[ProposalRole, str], Any] = {}
        self._role_configs: dict[ProposalRole, ProposalAgentConfig] = {
            role: ProposalAgentConfig(role=role, engine="")
            for role in ProposalRole
        }
        self._thread = None
        self._worker = None
        self._running_action: str = ""
        self._init_note: str = ""
        #: Session 020: the live campaign boundary control surface (armed
        #: while a campaign worker runs; consumed by run_campaign at safe
        #: stage boundaries only).
        self._campaign_control: CampaignControl | None = None
        self._campaign_running: bool = False
        #: One-shot action feedback the renderer prefixes (never overwritten
        #: by refresh — the S017 lesson: route feedback through panel state).
        self._action_note: str = ""

        # -- scrollable production layout (Session 020 §23) ----------------
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        #: Test seam (§25): the panel is vertically scrollable.
        self._scroll_area = scroll
        layout = QVBoxLayout(content)

        # -- navigation ------------------------------------------------------
        nav_row = QHBoxLayout()
        self.back_button = QPushButton("BACK TO CODING MODE")
        self.back_button.clicked.connect(self._on_back)
        nav_row.addWidget(self.back_button)
        nav_row.addStretch(1)
        layout.addLayout(nav_row)

        # -- A. PROJECT INPUTS (Session 020 §4) -------------------------------
        inputs_box = QGroupBox("PROJECT INPUTS")
        inputs_form = QGridLayout(inputs_box)
        self.ws_edit = QLineEdit()
        if self.workspace_root is not None:
            self.ws_edit.setText(str(self.workspace_root))
        self.ws_edit.setPlaceholderText(r"C:\proposals\my-proposal-workspace")
        browse = QPushButton("BROWSE")
        browse.clicked.connect(self._on_browse)
        self.init_button = QPushButton("INITIALIZE")
        self.init_button.clicked.connect(self._on_initialize)
        self.refresh_button = QPushButton("REFRESH")
        self.refresh_button.clicked.connect(self._on_refresh)
        inputs_form.addWidget(QLabel("Workspace:"), 0, 0)
        inputs_form.addWidget(self.ws_edit, 0, 1)
        inputs_form.addWidget(browse, 0, 2)
        inputs_form.addWidget(self.init_button, 0, 3)
        inputs_form.addWidget(self.refresh_button, 0, 4)

        self.blueprint_label = QLabel("MASTER BLUEPRINT: MISSING")
        self.import_blueprint_button = QPushButton("IMPORT BLUEPRINT")
        self.import_blueprint_button.clicked.connect(self._on_import_blueprint)
        inputs_form.addWidget(self.blueprint_label, 1, 0, 1, 2)
        inputs_form.addWidget(self.import_blueprint_button, 1, 2, 1, 3)

        # Session 021: LIVING Blueprint + DOCUMENT PAIR status labels.
        self.current_blueprint_label = QLabel(
            "CURRENT / LIVING BLUEPRINT: MISSING"
        )
        self.current_blueprint_label.setWordWrap(True)
        inputs_form.addWidget(self.current_blueprint_label, 8, 0, 1, 5)
        self.pair_state_label = QLabel("DOCUMENT PAIR: not committed")
        self.pair_state_label.setWordWrap(True)
        inputs_form.addWidget(self.pair_state_label, 9, 0, 1, 5)

        self.template_label = QLabel("OFFICIAL TEMPLATE: MISSING")
        self.import_template_button = QPushButton("IMPORT TEMPLATE")
        self.import_template_button.clicked.connect(self._on_import_template)
        inputs_form.addWidget(self.template_label, 2, 0, 1, 2)
        inputs_form.addWidget(self.import_template_button, 2, 2, 1, 3)

        self.official_docs_label = QLabel("OFFICIAL DOCUMENTS: 0 files loaded")
        self.import_docs_button = QPushButton("ADD OFFICIAL DOCS")
        self.import_docs_button.clicked.connect(self._on_import_official_docs)
        self.open_imports_button = QPushButton("OPEN FOLDER")
        self.open_imports_button.clicked.connect(self._on_open_official_folder)
        inputs_form.addWidget(self.official_docs_label, 3, 0, 1, 2)
        inputs_form.addWidget(self.import_docs_button, 3, 2)
        inputs_form.addWidget(self.open_imports_button, 3, 3, 1, 2)

        self.existing_proposal_label = QLabel("EXISTING PROPOSAL: EMPTY")
        self.import_proposal_button = QPushButton("IMPORT PROPOSAL")
        self.import_proposal_button.clicked.connect(self._on_import_existing_proposal)
        inputs_form.addWidget(self.existing_proposal_label, 4, 0, 1, 2)
        inputs_form.addWidget(self.import_proposal_button, 4, 2, 1, 3)

        self.master_label = QLabel("MASTER PROPOSAL: EMPTY")
        self.open_master_button = QPushButton("OPEN FOLDER")
        self.open_master_button.clicked.connect(self._on_open_master_folder)
        inputs_form.addWidget(self.master_label, 5, 0, 1, 2)
        inputs_form.addWidget(self.open_master_button, 5, 2, 1, 3)

        self.source_budget_label = QLabel(
            f"Source pack: 0 / {DEFAULT_SOURCE_BUDGET_CHARS} chars"
        )
        self.source_budget_label.setWordWrap(True)
        inputs_form.addWidget(self.source_budget_label, 6, 0, 1, 5)
        self.ws_status_label = QLabel("Workspace not initialised.")
        self.ws_status_label.setWordWrap(True)
        inputs_form.addWidget(self.ws_status_label, 7, 0, 1, 5)
        layout.addWidget(inputs_box)

        # -- B. AGENTS (Session 020 §7/§8) -------------------------------------
        agents_box = QGroupBox("AGENTS")
        agents_form = QGridLayout(agents_box)
        engine_ids = self.registry.driver_ids()
        self._role_rows: dict[ProposalRole, dict[str, Any]] = {}
        for row, role in enumerate(ProposalRole):
            label = QLabel(_ROLE_LABELS[role])
            engine = QComboBox()
            engine.addItem("(select engine)", "")
            for driver_id in engine_ids:
                engine.addItem(driver_id, driver_id)
            profile = QComboBox()
            profile.setEditable(True)
            profile.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            profile.setToolTip("Real Hermes profiles (discovered; never invented)")
            provider = QComboBox()
            provider.setEditable(True)
            provider.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            provider.setToolTip(
                "Real Hermes providers (discovered inventory; editable)"
            )
            model = QComboBox()
            model.setEditable(True)
            model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            model.setToolTip(
                "Real models for the selected provider (editable)"
            )
            reasoning = QComboBox()
            reasoning.addItem("DEFAULT", "")
            for level in ("minimal", "low", "medium", "high", "xhigh"):
                reasoning.addItem(level.upper(), level)
            reasoning.setToolTip(
                "Per-invocation reasoning effort — meaningful ONLY for the "
                "codex engine (verified CLI contract). DEFAULT lets Codex "
                "use its own default."
            )
            session_mode = QComboBox()
            session_mode.addItem(_SESSION_MODE_NEW, "NEW_SESSION")
            session_mode.addItem(_SESSION_MODE_RESUME, "RESUME_SELECTED_SESSION")
            session = QComboBox()
            session.setEditable(False)
            session.addItem("NEW SESSION", "")
            session.setToolTip("Profile-scoped discovered Hermes sessions")
            # S017 seam: the legacy test drives the session field through
            # setText(); expose the editable mirror used by that contract.
            session.setText = session.setEditText  # type: ignore[method-assign]
            refresh = QPushButton("REFRESH")
            refresh.clicked.connect(
                lambda _=False, r=role: self._on_refresh_agent_selectors(r)
            )
            agents_form.addWidget(label, row, 0)
            agents_form.addWidget(QLabel("Engine:"), row, 1)
            agents_form.addWidget(engine, row, 2)
            agents_form.addWidget(QLabel("Profile:"), row, 3)
            agents_form.addWidget(profile, row, 4)
            agents_form.addWidget(QLabel("Provider:"), row, 5)
            agents_form.addWidget(provider, row, 6)
            agents_form.addWidget(QLabel("Model:"), row, 7)
            agents_form.addWidget(model, row, 8)
            agents_form.addWidget(QLabel("Reasoning:"), row, 9)
            agents_form.addWidget(reasoning, row, 10)
            agents_form.addWidget(QLabel("Session Mode:"), row, 11)
            agents_form.addWidget(session_mode, row, 12)
            agents_form.addWidget(QLabel("Session:"), row, 13)
            agents_form.addWidget(session, row, 14)
            agents_form.addWidget(refresh, row, 15)
            engine.activated.connect(
                lambda _idx, r=role: self._on_role_engine_changed(r)
            )
            profile.activated.connect(
                lambda _idx, r=role: self._on_role_profile_changed(r)
            )
            provider.activated.connect(
                lambda _idx, r=role: self._on_role_provider_changed(r)
            )
            provider.editTextChanged.connect(
                lambda _t, r=role: self._on_role_config_text_changed(r)
            )
            model.editTextChanged.connect(
                lambda _t, r=role: self._on_role_config_text_changed(r)
            )
            reasoning.activated.connect(
                lambda _idx, r=role: self._on_role_config_text_changed(r)
            )
            session_mode.activated.connect(
                lambda _idx, r=role: self._on_role_session_mode_changed(r)
            )
            session.activated.connect(
                lambda _idx, r=role: self._on_role_session_changed(r)
            )
            self._role_rows[role] = {
                "engine": engine,
                "profile": profile,
                "provider": provider,
                "model": model,
                "reasoning": reasoning,
                "session_mode": session_mode,
                "session": session,
                "refresh": refresh,
            }
        layout.addWidget(agents_box)

        # -- C. PANEL / CAMPAIGN (Session 020 §10) -------------------------------
        run_box = QGroupBox("PANEL / CAMPAIGN")
        run_form = QVBoxLayout(run_box)
        actions_row = QHBoxLayout()
        self.generate_button = QPushButton("GENERATE INITIAL PROPOSAL")
        self.generate_button.clicked.connect(self._on_generate_initial)
        self.run_panel_button = QPushButton("RUN PANEL ITERATION")
        self.run_panel_button.clicked.connect(self._on_run_panel)
        self.run_iteration_button = QPushButton("RUN ITERATION")
        self.run_iteration_button.clicked.connect(self._on_run_iteration)
        self.run_gates_button = QPushButton("RUN HARD GATES")
        self.run_gates_button.clicked.connect(self._on_run_hard_gates)
        for btn in (
            self.generate_button,
            self.run_panel_button,
            self.run_iteration_button,
            self.run_gates_button,
        ):
            actions_row.addWidget(btn)
        run_form.addLayout(actions_row)

        campaign_box = QGroupBox("AUTONOMOUS PANEL CAMPAIGN")
        campaign_form = QGridLayout(campaign_box)
        self.campaign_hours = QDoubleSpinBox()
        self.campaign_hours.setRange(1.0, 120.0)
        self.campaign_hours.setValue(24.0)
        self.campaign_iterations = QSpinBox()
        self.campaign_iterations.setRange(1, 50)
        self.campaign_iterations.setValue(10)
        self.campaign_target_readiness = QDoubleSpinBox()
        self.campaign_target_readiness.setRange(0.0, 100.0)
        self.campaign_target_readiness.setValue(92.0)
        self.campaign_max_model_calls = QSpinBox()
        self.campaign_max_model_calls.setRange(1, 100000)
        self.campaign_max_model_calls.setValue(1000)
        self.campaign_no_improvement = QSpinBox()
        self.campaign_no_improvement.setRange(1, 10)
        self.campaign_no_improvement.setValue(3)
        campaign_form.addWidget(QLabel("Max hours:"), 0, 0)
        campaign_form.addWidget(self.campaign_hours, 0, 1)
        campaign_form.addWidget(QLabel("Max iterations:"), 0, 2)
        campaign_form.addWidget(self.campaign_iterations, 0, 3)
        campaign_form.addWidget(QLabel("Target readiness:"), 1, 0)
        campaign_form.addWidget(self.campaign_target_readiness, 1, 1)
        campaign_form.addWidget(QLabel("Max model calls:"), 1, 2)
        campaign_form.addWidget(self.campaign_max_model_calls, 1, 3)
        campaign_form.addWidget(QLabel("No-improvement limit:"), 2, 0)
        campaign_form.addWidget(self.campaign_no_improvement, 2, 1)
        controls_row = QHBoxLayout()
        self.start_campaign_button = QPushButton("START CAMPAIGN")
        self.start_campaign_button.clicked.connect(self._on_start_campaign)
        self.pause_campaign_button = QPushButton("PAUSE")
        self.pause_campaign_button.clicked.connect(self._on_pause_campaign)
        self.resume_campaign_button = QPushButton("RESUME")
        self.resume_campaign_button.clicked.connect(self._on_resume_campaign)
        self.stop_campaign_button = QPushButton("STOP")
        self.stop_campaign_button.clicked.connect(self._on_stop_campaign)
        for btn in (
            self.start_campaign_button,
            self.pause_campaign_button,
            self.resume_campaign_button,
            self.stop_campaign_button,
        ):
            controls_row.addWidget(btn)
        campaign_form.addLayout(controls_row, 3, 0, 1, 4)
        self.campaign_note_label = QLabel("")
        self.campaign_note_label.setWordWrap(True)
        campaign_form.addWidget(self.campaign_note_label, 4, 0, 1, 4)
        run_form.addWidget(campaign_box)
        layout.addWidget(run_box)

        # -- D. CURRENT STATE & READINESS (Session 020 §14) ----------------------
        state_box = QGroupBox("CURRENT STATE & READINESS")
        state_layout = QVBoxLayout(state_box)
        self.campaign_status_label = QLabel("Campaign: NOT STARTED")
        self.campaign_status_label.setWordWrap(True)
        self.state_label = QLabel("Phase: IDLE")
        self.state_label.setWordWrap(True)
        self.iteration_label = QLabel("Iteration: 0")
        self.hash_label = QLabel("Proposal hash: —")
        self.hash_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.model_calls_label = QLabel("Model calls used: —")
        self.elapsed_label = QLabel("Elapsed campaign time: —")
        self.readiness_label = QLabel("Internal readiness: —")
        self.readiness_label.setWordWrap(True)
        #: The disclaimer is ALWAYS displayed directly under the readiness
        #: (brief §14 — never omitted).
        self.readiness_disclaimer_label = QLabel(READINESS_DISCLAIMER_LABEL)
        self.readiness_disclaimer_label.setWordWrap(True)
        self.readiness_history_table = QTableWidget(0, 2)
        self.readiness_history_table.setHorizontalHeaderLabels(
            ["Iteration", "Internal readiness"]
        )
        self.readiness_history_table.verticalHeader().setVisible(False)
        self.readiness_history_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.readiness_history_table.setMaximumHeight(160)
        # Session 017A spinner seam: the durable iteration the NEXT
        # operation legitimately uses (kept as a spin control so the
        # S017A recovery contract keeps its attribute + value()).
        self.iteration_spin = QSpinBox()
        self.iteration_spin.setRange(1, 999)
        self.iteration_spin.setVisible(False)
        state_layout.addWidget(self.iteration_spin)
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        state_layout.addWidget(self.campaign_status_label)
        state_layout.addWidget(self.state_label)
        state_layout.addWidget(self.iteration_label)
        state_layout.addWidget(self.hash_label)
        state_layout.addWidget(self.model_calls_label)
        state_layout.addWidget(self.elapsed_label)
        state_layout.addWidget(self.readiness_label)
        state_layout.addWidget(self.readiness_disclaimer_label)
        state_layout.addWidget(self.readiness_history_table)
        state_layout.addWidget(self.detail_label)
        layout.addWidget(state_box)

        # -- E. PANEL RESULTS (Session 020 §15/§16) ------------------------------
        results_box = QGroupBox("PANEL RESULTS")
        results_layout = QVBoxLayout(results_box)
        self.review_table = QTableWidget(0, 4)
        self.review_table.setHorizontalHeaderLabels(
            ["Reviewer", "Verdict", "Proposal hash", "Findings / claims"]
        )
        self.review_table.verticalHeader().setVisible(False)
        self.review_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        results_layout.addWidget(self.review_table)
        self.consensus_table = QTableWidget(0, 7)
        self.consensus_table.setHorizontalHeaderLabels(
            [
                "ID",
                "Section / target",
                "Agreement",
                "Disagreement",
                "Insufficient evidence",
                "Blocking?",
                "Status",
            ]
        )
        self.consensus_table.verticalHeader().setVisible(False)
        self.consensus_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        results_layout.addWidget(self.consensus_table)
        self.consensus_summary_label = QLabel("Unresolved disagreements: —")
        self.consensus_summary_label.setWordWrap(True)
        results_layout.addWidget(self.consensus_summary_label)
        layout.addWidget(results_box)

        # -- F. HARD GATES (§11) ---------------------------------------------------
        gates_box = QGroupBox("HARD GATES")
        gates_layout = QVBoxLayout(gates_box)
        self.gate_table = QTableWidget(0, 3)
        self.gate_table.setHorizontalHeaderLabels(["Gate", "Status", "Message"])
        self.gate_table.verticalHeader().setVisible(False)
        self.gate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        gates_layout.addWidget(self.gate_table)
        layout.addWidget(gates_box)

        # -- G. EVIDENCE (§12) -------------------------------------------------------
        evidence_box = QGroupBox("EVIDENCE")
        evidence_layout = QVBoxLayout(evidence_box)
        self.evidence_table = QTableWidget(0, 3)
        self.evidence_table.setHorizontalHeaderLabels(
            ["Evidence file", "Location", "Status"]
        )
        self.evidence_table.verticalHeader().setVisible(False)
        self.evidence_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        evidence_layout.addWidget(self.evidence_table)
        self.skeleton_button = QPushButton("CREATE EVIDENCE SKELETON")
        self.skeleton_button.clicked.connect(self._on_create_skeleton)
        evidence_layout.addWidget(self.skeleton_button)
        layout.addWidget(evidence_box)

        layout.addStretch(1)
        # The 14-gate table structure exists from construction (NOT_RUN
        # rows); a workspace refresh overwrites with real artifact values.
        self.gate_table.setRowCount(len(HARD_GATE_IDS_TUPLE))
        for r, gate_id in enumerate(HARD_GATE_IDS_TUPLE):
            self.gate_table.setItem(r, 0, QTableWidgetItem(gate_id))
            self.gate_table.setItem(r, 1, QTableWidgetItem("NOT_RUN"))
            self.gate_table.setItem(r, 2, QTableWidgetItem(""))
        self._set_running(False)
        self.refresh_status()

    # -- workspace helpers ----------------------------------------------------
    def _workspace(self) -> Path | None:
        text = self.ws_edit.text().strip()
        return Path(text) if text else None

    def _invalidate_codex_sessions_on_workspace_change(self) -> None:
        """Session 021A: Codex session ids are workspace-bound discoveries.

        Changing the workspace invalidates every Codex role's session
        selection AND resume mode (the selected thread belongs to the OLD
        workspace; a resumed thread inherits its original working root, so
        the binding is meaningless and fail-closed beats stale).
        """
        for role in ProposalRole:
            row = self._role_rows[role]
            if str(row["engine"].currentData() or "") == _CODEX_ENGINE_ID:
                row["session"].setCurrentIndex(0)
                row["session_mode"].setCurrentIndex(0)
                self._sync_role_config_from_widgets(role)

    def _on_browse(self) -> None:
        start = self.ws_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Proposal workspace", start)
        if chosen:
            if Path(chosen) != self._workspace():
                self._invalidate_codex_sessions_on_workspace_change()
            self.ws_edit.setText(chosen)
            self.refresh_status()

    def _on_back(self) -> None:
        if self.request_coding is not None:
            self.request_coding()

    # -- workspace init / refresh ------------------------------------------------
    def _on_initialize(self) -> None:
        ws = self._workspace()
        if ws is None:
            self.ws_status_label.setText("Choose a workspace path first.")
            return
        created = ProposalWorkspace(ws).initialize()
        self._init_note = (
            f"Workspace ready ({len(created)} path(s) created; existing files untouched)."
        )
        self.refresh_status()

    def _on_refresh(self) -> None:
        # Session 021A: a manual workspace-path edit (then REFRESH) is a
        # workspace change too — the same Codex binding invalidation as
        # BROWSE applies.
        self._invalidate_codex_sessions_on_workspace_change()
        self.refresh_status()

    # -- PROJECT INPUTS importers (Session 020 §4/§5/§6) ---------------------------
    def _import_paths_dialog(self, title: str, multi: bool) -> list[str]:
        if multi:
            chosen, _ = QFileDialog.getOpenFileNames(
                self, title, "", _IMPORT_SUFFIX_FILTER
            )
            return list(chosen)
        chosen, _ = QFileDialog.getOpenFileName(
            self, title, "", _IMPORT_SUFFIX_FILTER
        )
        return [chosen] if chosen else []

    def _confirm_replace(self, canonical_desc: str) -> bool:
        """Explicit operator confirmation before ANY canonical replace."""
        answer = QMessageBox.question(
            self,
            "Replace existing source?",
            f"{canonical_desc} already holds content.\n\n"
            "Replace it? The existing canonical text will be backed up "
            "first (backend backup-before-replace), and the replacement "
            "is recorded in the import manifest.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _run_import(
        self, source_paths: list[str], import_role: str, canonical_desc: str
    ) -> None:
        """Import through the REAL source_import implementation — one path."""
        ws = self._workspace()
        if ws is None:
            self._action_note = "Choose a workspace path first."
            self.refresh_status()
            return
        replace = False
        for path_text in source_paths:
            try:
                record = import_source(
                    ws,
                    Path(path_text),
                    import_role=import_role,
                    replace=replace,
                )
            except SourceImportError as exc:
                if exc.reason == "canonical_exists" and not replace:
                    if self._confirm_replace(canonical_desc):
                        replace = True
                        try:
                            record = import_source(
                                ws,
                                Path(path_text),
                                import_role=import_role,
                                replace=True,
                            )
                        except SourceImportError as exc2:
                            self._action_note = f"IMPORT FAILED: {exc2}"
                            self.refresh_status()
                            return
                    else:
                        self._action_note = (
                            "IMPORT CANCELLED — existing source untouched."
                        )
                        self.refresh_status()
                        return
                else:
                    self._action_note = f"IMPORT FAILED: {exc}"
                    self.refresh_status()
                    return
            if record.extraction_warning:
                self._action_note = (
                    f"IMPORTED {record.original_filename} "
                    f"(warning: {record.extraction_warning})"
                )
            else:
                self._action_note = (
                    f"IMPORTED {record.original_filename} → "
                    f"{record.normalized_path}"
                )
        self.refresh_status()

    def _on_import_blueprint(self) -> None:
        paths = self._import_paths_dialog("Import MASTER BLUEPRINT", multi=False)
        if not paths:
            return
        self._run_import(paths, "master_blueprint", "MASTER_BLUEPRINT.md")
        # Session 021: after a SUCCESSFUL blueprint import the LIVING
        # Blueprint is initialized with the EXACT canonical bytes (CURRENT :=
        # MASTER) so the document pair exists from day one.  A failed import
        # leaves the workspace untouched (the note keeps the import error).
        ws = self._workspace()
        if ws is None or not ws.is_dir():
            return
        master_path = ws / "00_SOURCE_OF_TRUTH" / "MASTER_BLUEPRINT.md"
        try:
            master_ok = (
                master_path.is_file() and bool(master_path.read_bytes().strip())
            )
        except OSError:
            master_ok = False
        if not master_ok:
            return
        try:
            from ..proposal.living_blueprint import ensure_current_blueprint

            _state, migrated = ensure_current_blueprint(ws)
        except Exception as exc:
            self._action_note = (
                f"{self._action_note} LIVING BLUEPRINT INIT FAILED: {exc}"
                if self._action_note
                else f"LIVING BLUEPRINT INIT FAILED: {exc}"
            )
            self.refresh_status()
            return
        if migrated:
            self._action_note = (
                f"{self._action_note} — LIVING Blueprint initialized "
                "(CURRENT := MASTER exact bytes)."
            )
        self.refresh_status()

    def _on_import_template(self) -> None:
        paths = self._import_paths_dialog("Import OFFICIAL TEMPLATE", multi=False)
        if paths:
            self._run_import(
                paths, "application_template", "APPLICATION_TEMPLATE.md"
            )

    def _on_import_official_docs(self) -> None:
        paths = self._import_paths_dialog(
            "Add OFFICIAL DOCUMENTS (multiple allowed)", multi=True
        )
        if paths:
            self._run_import(
                paths, "official_document", "official documents"
            )

    def _on_import_existing_proposal(self) -> None:
        ws = self._workspace()
        if ws is None:
            self._action_note = "Choose a workspace path first."
            self.refresh_status()
            return
        paths = self._import_paths_dialog("Import EXISTING PROPOSAL", multi=False)
        if not paths:
            return
        try:
            record = import_proposal(ws, Path(paths[0]))
        except SourceImportError as exc:
            if exc.reason == "master_not_empty":
                if not self._confirm_replace("MASTER_PROPOSAL.md"):
                    self._action_note = (
                        "IMPORT CANCELLED — existing master untouched."
                    )
                    self.refresh_status()
                    return
                try:
                    record = import_source(
                        ws,
                        Path(paths[0]),
                        import_role="existing_proposal",
                        replace=True,
                    )
                except SourceImportError as exc2:
                    self._action_note = f"IMPORT FAILED: {exc2}"
                    self.refresh_status()
                    return
            else:
                self._action_note = f"IMPORT FAILED: {exc}"
                self.refresh_status()
                return
        self._action_note = (
            f"IMPORTED {record.original_filename} → {record.normalized_path}"
        )
        self.refresh_status()

    def _on_open_official_folder(self) -> None:
        ws = self._workspace()
        if ws is not None:
            QFileDialog.getOpenFileNames(
                self, "01_OFFICIAL/NORMALIZED", str(ws / "01_OFFICIAL")
            )

    def _on_open_master_folder(self) -> None:
        ws = self._workspace()
        if ws is not None:
            QFileDialog.getOpenFileNames(
                self, "03_PROPOSAL", str(ws / "03_PROPOSAL")
            )

    def _invalidate_catalog_cache(self, role: ProposalRole) -> None:
        """Session 022A: drop this role's cached catalogs (any profile).

        The cache is keyed ``(role, profile)``; invalidation is per role
        across profiles so a stale profile entry can never survive.
        """
        for key in [k for k in self._catalog_cache if k[0] == role]:
            self._catalog_cache.pop(key, None)

    # -- agent selector discovery (Session 020 §8; dispatch per engine, 022A) ------
    def _on_refresh_agent_selectors(self, role: ProposalRole) -> None:
        """Session 022A: ONE engine-aware REFRESH dispatcher per role row.

        hermes: profiles + provider-model catalog + profile sessions.
        codex:  real Codex sessions; the operator's model, reasoning, and
                the disabled N/A profile/provider stay untouched; zero
                model calls.
        other:  an honest unavailable note (no capability-based discovery
                surface exists for generic engines yet).
        none:   no discovery call at all.
        """
        row = self._role_rows[role]
        engine = str(row["engine"].currentData() or "")
        if engine == _HERMES_ENGINE_ID:
            self._on_refresh_hermes_selectors(role)
        elif engine == _CODEX_ENGINE_ID:
            self._populate_codex_sessions(role)
            self._on_role_config_text_changed(role)
        elif not engine:
            self._action_note = "Select an engine first."
            self.refresh_status()
        else:
            caps = self._capabilities_for(engine)
            if caps is not None and bool(caps.supports_sessions):
                self._action_note = (
                    f"{engine} exposes no selector discovery surface in "
                    "this build; sessions/capabilities unchanged."
                )
            else:
                self._action_note = (
                    f"{engine} has no discoverable selector surface."
                )
            self.refresh_status()

    def _on_refresh_hermes_selectors(self, role: ProposalRole) -> None:
        row = self._role_rows[role]
        engine = str(row["engine"].currentData() or "")
        if engine != _HERMES_ENGINE_ID:
            self._action_note = "Hermes discovery applies to the hermes engine."
            self.refresh_status()
            return
        # Session 022A: an explicit REFRESH re-runs the inventory — drop
        # this role's cached catalogs first (any profile).
        self._invalidate_catalog_cache(role)
        profiles = discover_hermes_profile_names()
        profile_combo = row["profile"]
        current = profile_combo.currentText().strip()
        profile_combo.clear()
        if profiles.ok and profiles.profiles:
            for name in profiles.profiles:
                profile_combo.addItem(name, name)
            if current:
                idx = profile_combo.findText(current)
                if idx >= 0:
                    profile_combo.setCurrentIndex(idx)
        # An honest empty result stays empty — never a fabricated list.
        selected_profile = profile_combo.currentText().strip()
        catalog_note = ""
        if selected_profile:
            catalog_note = self._populate_provider_model(role, selected_profile)
            self._populate_sessions(role, selected_profile)
        self._apply_selector_gating(role)
        self._sync_reasoning_availability(role)
        self._on_role_config_text_changed(role)
        self._action_note = (
            f"Hermes discovery: {len(profiles.profiles)} profile(s) "
            f"({profiles.source or profiles.error or 'unavailable'})"
            + (f"; {catalog_note}" if catalog_note else "")
            + "."
        )
        self.refresh_status()

    def _populate_provider_model(self, role: ProposalRole, profile: str) -> str:
        """Session 022: populate provider + models from the REAL inventory.

        Provider items = every provider the authoritative catalog lists
        (deterministic inventory order); the profile's default provider is
        selected when present.  Models belong to the SELECTED provider —
        the profile's default model is preselected only when it belongs to
        that provider.  Combos stay EDITABLE either way (discovery is a
        suggestion, never a cage).  Returns a short honest note for the
        action line.
        """
        row = self._role_rows[role]
        provider_combo = row["provider"]
        model_combo = row["model"]
        key = (role, str(profile or "").strip())
        catalog = self._catalog_cache.get(key)
        if catalog is None:
            catalog = discover_hermes_model_catalog(profile)
            self._catalog_cache[key] = catalog
        with QSignalBlocker(provider_combo), QSignalBlocker(model_combo):
            provider_combo.clear()
            model_combo.clear()
            if catalog.available:
                for name in catalog.provider_names():
                    provider_combo.addItem(name, name)
                default_provider = catalog.profile_default_provider
                if default_provider:
                    idx = provider_combo.findText(default_provider)
                    if idx >= 0:
                        provider_combo.setCurrentIndex(idx)
                selected_provider = provider_combo.currentText().strip()
                for model_id in catalog.models_for_provider(selected_provider):
                    model_combo.addItem(model_id, model_id)
                default_model = catalog.profile_default_model
                if default_model and model_combo.findText(default_model) >= 0:
                    model_combo.setCurrentIndex(
                        model_combo.findText(default_model)
                    )
        if catalog.available:
            return (
                f"{len(catalog.providers)} provider(s) via "
                f"{catalog.source} (editable)"
            )
        return f"provider/model catalog unavailable ({catalog.error})"

    def _on_role_provider_changed(self, role: ProposalRole) -> None:
        """Session 022: provider selection repopulates models IMMEDIATELY.

        The manually-entered model is preserved only when it already
        belongs to the newly selected provider (item match); otherwise the
        provider's real models replace the list — an unrelated provider's
        model is never silently kept or substituted.
        """
        row = self._role_rows[role]
        engine = str(row["engine"].currentData() or "")
        if engine != _HERMES_ENGINE_ID:
            return
        provider = row["provider"].currentText().strip()
        previous_model = row["model"].currentText().strip()
        key = (role, row["profile"].currentText().strip())
        catalog = self._catalog_cache.get(key)
        models = (
            catalog.models_for_provider(provider)
            if catalog is not None and catalog.available
            else []
        )
        with QSignalBlocker(row["model"]):
            row["model"].clear()
            for model_id in models:
                row["model"].addItem(model_id, model_id)
            if previous_model and row["model"].findText(previous_model) >= 0:
                row["model"].setCurrentIndex(
                    row["model"].findText(previous_model)
                )
        self._on_role_config_text_changed(role)

    def _apply_selector_gating(self, role: ProposalRole) -> None:
        """Session 022: enable only the controls the engine can actually use.

        Capability-driven (brief §5): a profile is enabled only when the
        engine ``requires_profile``; the provider selector is Hermes-only;
        the model field is enabled only when the engine
        ``supports_model_selection``.  Codex has NO Hermes profile/provider
        contract: those combos are DISABLED showing "N/A" (never a
        misleading empty editable box).
        """
        row = self._role_rows[role]
        engine = str(row["engine"].currentData() or "")
        is_codex = engine == _CODEX_ENGINE_ID
        is_hermes = engine == _HERMES_ENGINE_ID
        requires_profile = True
        model_selectable = True
        if not engine:
            # Session 022A: no engine — EVERYTHING engine-specific goes
            # neutral.  This is its own branch, never the Codex fallback.
            for key in (
                "profile", "provider", "model", "reasoning",
                "session_mode", "session", "refresh",
            ):
                row[key].setEnabled(False)
            return
        caps = self._capabilities_for(engine)
        if is_hermes:
            requires_profile = True
            model_selectable = True
        elif is_codex:
            requires_profile = False
            model_selectable = True
        elif caps is not None:
            requires_profile = bool(caps.requires_profile)
            model_selectable = bool(caps.supports_model_selection)
            row["session_mode"].setEnabled(bool(caps.supports_sessions))
            row["session"].setEnabled(bool(caps.supports_sessions))
        else:  # unknown engine id: stay conservative
            requires_profile = True
            model_selectable = True
        row["profile"].setEnabled(requires_profile)
        row["provider"].setEnabled(is_hermes)
        row["model"].setEnabled(model_selectable)
        if is_codex:
            with QSignalBlocker(row["profile"]), QSignalBlocker(row["provider"]):
                row["profile"].setEditText("N/A")
                row["provider"].setEditText("N/A")
            self._populate_codex_model(role)

    def _populate_codex_model(self, role: ProposalRole) -> None:
        """Codex model display: placeholder ONLY when the field is empty.

        There is NO verified Codex model listing (the installed CLI exposes
        no model-listing command), so NOTHING is ever put into the list and
        the Hermes inventory is never copied across engines.  An
        operator-entered or persisted model id is never overwritten.
        """
        row = self._role_rows[role]
        with QSignalBlocker(row["model"]):
            if not row["model"].currentText().strip():
                row["model"].setEditText(_CODEX_MODEL_PLACEHOLDER)

    def _sync_reasoning_availability(self, role: ProposalRole) -> None:
        """Session 022: reasoning is a VERIFIED Codex-only contract.

        On non-Codex engines the combo is disabled and shows "N/A"; the
        underlying selection stays DEFAULT (data "") so a persisted config
        never carries a reasoning effort for a non-Codex engine.
        """
        row = self._role_rows[role]
        engine = str(row["engine"].currentData() or "")
        is_codex = engine == _CODEX_ENGINE_ID
        row["reasoning"].setEnabled(is_codex)
        row["reasoning"].setItemText(0, "DEFAULT" if is_codex else "N/A")
        if not is_codex:
            row["reasoning"].setCurrentIndex(0)

    def _populate_sessions(self, role: ProposalRole, profile: str) -> None:
        row = self._role_rows[role]
        session_combo = row["session"]
        session_combo.clear()
        session_combo.addItem("NEW SESSION", "")
        options = discover_hermes_sessions(profile=profile)
        for session_id in options.session_ids:
            session_combo.addItem(session_id, session_id)
        # An honest empty/unavailable result leaves only NEW SESSION.

    def _capabilities_for(self, engine: str):
        """Session 022A: the engine's real DriverCapabilities (or None).

        Unknown/empty engines resolve to None; callers decide the honest
        fallback instead of this helper inventing capabilities.
        """
        engine = str(engine or "").strip()
        if not engine:
            return None
        try:
            return self.registry.capabilities(engine)
        except KeyError:
            return None

    def _on_role_engine_changed(self, role: ProposalRole) -> None:
        row = self._role_rows[role]
        engine = str(row["engine"].currentData() or "")
        is_hermes = engine == _HERMES_ENGINE_ID
        is_codex = engine == _CODEX_ENGINE_ID
        if not is_hermes:
            # Session 022A: ANY engine change away from Hermes (codex,
            # generic, none) invalidates this role's cached catalogs —
            # including the codex branch below, which has no other pop.
            self._invalidate_catalog_cache(role)
        # Session 022A: session controls follow the REAL capabilities —
        # never a hardcoded "Hermes or Codex" pair.  An engine that cannot
        # keep sessions gets NEW_SESSION-only widgets; one that cannot
        # resume gets NEW SESSION usable with RESUME not actionable.
        caps = self._capabilities_for(engine)
        supports_sessions = bool(caps.supports_sessions) if caps else False
        supports_resume = bool(caps.supports_resume) if caps else False
        row["session_mode"].setEnabled(supports_sessions)
        row["session"].setEnabled(supports_sessions)
        row["refresh"].setEnabled(is_hermes or is_codex)
        if supports_sessions and not supports_resume:
            # NEW SESSION stays usable; RESUME must not be actionable.
            resume_index = row["session_mode"].findData(
                "RESUME_SELECTED_SESSION"
            )
            if resume_index >= 0:
                row["session_mode"].removeItem(resume_index)
        elif row["session_mode"].findData("RESUME_SELECTED_SESSION") < 0:
            row["session_mode"].addItem(
                _SESSION_MODE_RESUME, "RESUME_SELECTED_SESSION"
            )
        if is_codex:
            # Session 021: Codex carries NO profile/provider (N/A for the
            # engine contract) but an EDITABLE model and real session
            # discovery.  Stale Hermes bindings are invalidated exactly like
            # an engine switch away: session selection AND resume mode reset
            # (an unconstructable RESUME-without-id is never armed).
            row["profile"].clear()
            row["provider"].clear()
            row["session"].setCurrentIndex(0)
            row["session_mode"].setCurrentIndex(0)
            self._populate_codex_sessions(role)
        elif not supports_sessions:
            # Session 022A: an engine that cannot keep sessions must not
            # carry a session_mode/session binding (persisted NEW_SESSION/""
            # below via the sync's own supports_sessions gate).
            row["session"].setCurrentIndex(0)
            row["session_mode"].setCurrentIndex(0)
        elif not is_hermes:
            # Engine away from Hermes: clear the incompatible Hermes session
            # binding AND the resume mode (a RESUME mode with no id would be
            # an unconstructable config — fail-closed, same class as the
            # profile-switch reset).
            row["session"].setCurrentIndex(0)
            row["session_mode"].setCurrentIndex(0)
            row["profile"].clear()
            row["model"].clear()
            self._invalidate_catalog_cache(role)
        else:
            self._on_refresh_hermes_selectors(role)
            return
        self._apply_selector_gating(role)
        self._sync_reasoning_availability(role)
        self._on_role_config_text_changed(role)

    def _populate_codex_sessions(self, role: ProposalRole) -> None:
        """Session 021: REAL Codex session discovery — zero model calls.

        Uses the existing read-only ``CodexSessionDiscovery`` (via the
        driver's ``SessionDiscoverer`` contract).  Titles are display-only;
        the REAL full session id rides as item data.  Workspace-matching
        sessions sort FIRST and carry a ``[WS]`` marker; other workspaces
        are visibly marked.  NEW SESSION stays the default and NOTHING is
        ever auto-selected for RESUME.
        """
        row = self._role_rows[role]
        session_combo = row["session"]
        session_combo.clear()
        session_combo.addItem("NEW SESSION", "")
        ws = self._workspace()
        try:
            driver = self.registry.create(_CODEX_ENGINE_ID)
            discover = getattr(driver, "discover_sessions", None)
            if discover is None:
                self._action_note = (
                    "Codex discovery unavailable: the codex driver exposes "
                    "no session discovery."
                )
                return
            result = discover(
                workspace_path=str(ws) if ws is not None else None,
            )
        except Exception as exc:  # DriverError / missing CLI — honest note
            self._action_note = f"Codex discovery unavailable: {exc}"
            return
        if not result.ok:
            self._action_note = (
                f"Codex discovery failed: {result.error or 'unknown error'}"
            )
            return
        sessions = sorted(
            result.sessions,
            key=lambda s: (not s.matches_workspace, str(s.title or s.session_id)),
        )
        shown = 0
        for s in sessions:
            if s.matches_workspace:
                label = s.label()
                session_combo.addItem(f"[WS] {label}", s.session_id)
            else:
                # Session 021A: other-workspace sessions stay VISIBLE for
                # operator awareness but are DISABLED as resume targets —
                # a resumed thread inherits its original working root, so
                # they must never be selectable (not just display text).
                label = s.label()
                index = session_combo.count()
                session_combo.addItem(f"[other ws] {label}", s.session_id)
                model = session_combo.model()
                item = model.item(index)
                item.setEnabled(False)
                item.setForeground(QBrush(QColor(128, 128, 128)))
            shown += 1
        self._action_note = (
            f"Codex discovery: {shown} session(s) "
            f"({result.mechanism or 'read-only'}) — select one explicitly "
            "for RESUME; nothing is auto-armed."
        )

    def _on_role_profile_changed(self, role: ProposalRole) -> None:
        row = self._role_rows[role]
        # A profile switch invalidates the old session selection: sessions
        # are profile-scoped, so the previous profile's ids are gone.
        profile = row["profile"].currentText().strip()
        self._invalidate_catalog_cache(role)
        self._populate_sessions(role, profile)
        self._populate_provider_model(role, profile)
        self._apply_selector_gating(role)
        self._sync_reasoning_availability(role)
        # The selection AND the mode RESET: sessions are profile-scoped, so
        # the previous profile's resume binding is meaningless — leaving
        # RESUME_SELECTED_SESSION armed with no id would be an unconstructable
        # config (fail-closed). The operator re-picks mode + id explicitly
        # from the rediscovered list (Session 019's resume contract).
        row["session"].setCurrentIndex(0)
        row["session_mode"].setCurrentIndex(0)
        self._on_role_config_text_changed(role)

    def _on_role_session_mode_changed(self, role: ProposalRole) -> None:
        self._on_role_config_text_changed(role)

    def _on_role_session_changed(self, role: ProposalRole) -> None:
        self._on_role_config_text_changed(role)

    def _on_role_config_text_changed(self, role: ProposalRole) -> None:
        self._sync_role_config_from_widgets(role)
        self._persist_config_if_workspace()

    def _sync_role_config_from_widgets(self, role: ProposalRole) -> None:
        row = self._role_rows[role]
        engine = str(row["engine"].currentData() or "")
        supports_sessions = False
        if engine and self.registry.is_registered(engine):
            try:
                supports_sessions = bool(
                    self.registry.capabilities(engine).supports_sessions
                )
            except KeyError:
                supports_sessions = False
        session_mode = str(row["session_mode"].currentData() or "NEW_SESSION")
        session_text = ""
        if engine == _HERMES_ENGINE_ID and session_mode == "RESUME_SELECTED_SESSION":
            session_text = str(row["session"].currentData() or "")
        elif engine == _CODEX_ENGINE_ID and session_mode == "RESUME_SELECTED_SESSION":
            # Session 021: the Codex combo carries the REAL full session id
            # as item data (titles are display-only); the runtime resume
            # contract re-verifies the id against the real engine.
            session_text = str(row["session"].currentData() or "")
        if not supports_sessions:
            session_mode = "NEW_SESSION"
            session_text = ""
        reasoning = ""
        if engine == _CODEX_ENGINE_ID:
            # The reasoning setting is meaningful ONLY for Codex (the
            # verified CLI contract); other engines always persist "".
            reasoning = str(row["reasoning"].currentData() or "")
        # Session 022: Codex carries NO Hermes profile/provider (persisted
        # empty, per the engine contract) and its placeholder is display-
        # only; Hermes never persists a reasoning effort (Codex-only).
        is_codex_engine = engine == _CODEX_ENGINE_ID
        model_text = row["model"].currentText().strip()
        self._role_configs[role] = ProposalAgentConfig(
            role=role,
            engine=engine,
            project_profile=(
                "" if is_codex_engine
                else row["profile"].currentText().strip()
            ),
            provider=(
                "" if is_codex_engine
                else row["provider"].currentText().strip()
            ),
            model=(
                ""
                if is_codex_engine and model_text == _CODEX_MODEL_PLACEHOLDER
                else model_text
            ),
            session_policy="persistent_optional",
            session_id=session_text,
            session_mode=session_mode,
            reasoning_effort=reasoning,
        )

    def _persist_config_if_workspace(self) -> None:
        ws = self._workspace()
        if ws is not None and ws.is_dir():
            self.save_role_config(ws)

    # -- role config (file-scoped, §5) ---------------------------------------------
    def _on_role_changed(self, role: ProposalRole) -> None:
        """Legacy S017 seam: one refresh from a plain text edit.

        The S020 combos carry the live widgets now, but the contract that
        engine/profile/provider/model/session edits flow into
        ``ProposalAgentConfig`` and persist is unchanged.
        """
        self._sync_role_config_from_widgets(role)
        self._persist_config_if_workspace()

    def apply_role_configs(
        self, configs: dict[ProposalRole, ProposalAgentConfig]
    ) -> None:
        """Programmatic config load (tests); refreshes the widgets."""
        for role, config in configs.items():
            self._role_configs[role] = config
            row = self._role_rows[role]
            index = row["engine"].findData(config.engine)
            row["engine"].setCurrentIndex(index if index >= 0 else 0)
            self._set_editable_text(row["profile"], config.project_profile)
            self._set_editable_text(row["provider"], config.provider)
            self._set_editable_text(row["model"], config.model)
            mode_index = (
                1
                if config.session_mode == "RESUME_SELECTED_SESSION"
                else 0
            )
            row["session_mode"].setCurrentIndex(mode_index)
            reasoning_index = row["reasoning"].findData(
                str(getattr(config, "reasoning_effort", "") or "")
            )
            row["reasoning"].setCurrentIndex(
                reasoning_index if reasoning_index >= 0 else 0
            )
            # Session 022: restore the engine-honest selector state.
            self._sync_reasoning_availability(role)
            self._apply_selector_gating(role)
            if config.session_id:
                if row["session"].findData(config.session_id) < 0:
                    row["session"].addItem(config.session_id, config.session_id)
                row["session"].setCurrentIndex(
                    row["session"].findData(config.session_id)
                )
            else:
                row["session"].setCurrentIndex(0)

    @staticmethod
    def _set_editable_text(combo: QComboBox, text: str) -> None:
        """Set an editable combo's line edit (or select a matching item)."""
        idx = combo.findText(text)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(text)

    def role_configs(self) -> dict[ProposalRole, ProposalAgentConfig]:
        return dict(self._role_configs)

    # -- isolated proposal config persistence (§5) ------------------------------
    def save_role_config(self, workspace: Path) -> None:
        payload = {
            "schema": "encomm-pcc.proposal-config/v1",
            "roles": {
                role.value: config.to_dict()
                for role, config in sorted(
                    self._role_configs.items(), key=lambda kv: kv[0].value
                )
            },
        }
        _atomic_write_json(
            Path(workspace) / "05_CONTROL" / PROPOSAL_CONFIG_FILENAME, payload
        )

    def load_role_config(self, workspace: Path) -> bool:
        path = Path(workspace) / "05_CONTROL" / PROPOSAL_CONFIG_FILENAME
        try:
            raw = path.read_bytes()
        except OSError:
            return False
        if not raw.strip():
            return False
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        roles = data.get("roles") if isinstance(data, dict) else None
        if not isinstance(roles, dict):
            return False
        loaded: dict[ProposalRole, ProposalAgentConfig] = {}
        for role in ProposalRole:
            entry = roles.get(role.value)
            if isinstance(entry, dict):
                entry = dict(entry)
                entry["role"] = role.value
                try:
                    loaded[role] = ProposalAgentConfig.from_dict(entry)
                except ValueError:
                    return False
        if loaded:
            self.apply_role_configs(loaded)
        return True

    # -- run controls -----------------------------------------------------------
    def _set_running(self, running: bool, action: str = "") -> None:
        self._running_action = action if running else ""
        if not running:
            self._campaign_running = False
            if self._campaign_control is not None:
                # The worker finished; a fresh run gets a fresh control object.
                self._campaign_control = None
        self._apply_run_gating()

    def _configs_valid(self) -> bool:
        return all(
            bool(config.engine)
            and self.registry.is_registered(config.engine)
            for config in self._role_configs.values()
        )

    def _source_budget_for(self) -> SourceBudget:
        return SourceBudget(blueprint_max_chars=DEFAULT_SOURCE_BUDGET_CHARS)

    def _master_state(self, ws: Path) -> tuple[bool, bool]:
        """(exists, non-empty) for MASTER_PROPOSAL."""
        master = ws / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
        if not master.is_file():
            return False, False
        try:
            return True, bool(master.read_bytes().strip())
        except OSError:
            return True, False

    def _apply_run_gating(self) -> None:
        """Enable run buttons ONLY for states the runtime accepts.

        GENERATE INITIAL PROPOSAL: workspace valid + blueprint READY +
        template READY + master EMPTY + all agent configs valid + nothing
        running.  RUN PANEL ITERATION: master non-empty + source budget
        passes + configs valid + phase in the panel entry set + nothing
        running.  RUN ITERATION / RUN HARD GATES: the S017A phase gates
        (unchanged).  Campaign controls: RUNNING → PAUSE/STOP enabled;
        PAUSED/recoverable → RESUME enabled.
        """
        running = bool(self._running_action)
        ws = self._workspace()
        ws_valid = ws is not None and ws.is_dir()

        generate_ok = False
        panel_ok = False
        iteration_ok = False
        gates_ok = False
        start_ok = False
        pause_ok = False
        stop_ok = False
        resume_ok = False

        if not running and ws_valid and ws is not None:
            bp_ready = blueprint_status(ws) == "READY"
            tpl_ready = blueprint_status(ws) == "READY"  # replaced below
            from ..proposal.source_import import template_status

            tpl_ready = template_status(ws) == "READY"
            master_exists, master_nonempty = self._master_state(ws)
            configs_ok = self._configs_valid()
            budget_ok = True
            try:
                check_source_budget(ws, self._source_budget_for())
            except SourceBudgetExceededError:
                budget_ok = False
            generate_ok = (
                bp_ready and tpl_ready and not master_nonempty and configs_ok
            )
            panel_ok = (
                master_nonempty
                and configs_ok
                and budget_ok
                and self._machine is not None
                and self._machine.phase in _PANEL_RUN_PHASES
            )
            if self._machine is not None:
                phase = self._machine.phase
                iteration_ok = phase in _ITERATION_RUN_PHASES
                gates_ok = phase is ProposalPhase.HARD_GATE_VALIDATION
            start_ok = configs_ok and budget_ok

        if running:
            if self._campaign_running and self._campaign_control is not None:
                pause_ok = True
                stop_ok = True

        if not running and ws_valid and ws is not None:
            state = self._load_campaign_state_safe(ws)
            if state is not None and state.status in (
                CampaignStatus.PAUSED,
                CampaignStatus.WAITING_FOR_OPERATOR,
                CampaignStatus.STOPPED,
                CampaignStatus.BOUND_REACHED,
                CampaignStatus.CONVERGED,
            ):
                resume_ok = True

        self.generate_button.setEnabled(generate_ok)
        self.run_panel_button.setEnabled(panel_ok)
        self.run_iteration_button.setEnabled(iteration_ok)
        self.run_gates_button.setEnabled(gates_ok)
        self.start_campaign_button.setEnabled(start_ok)
        self.pause_campaign_button.setEnabled(pause_ok)
        self.stop_campaign_button.setEnabled(stop_ok)
        self.resume_campaign_button.setEnabled(resume_ok)

    def _load_campaign_state_safe(self, ws: Path):
        try:
            return load_campaign_state(ws)
        except (ValueError, OSError):
            return None

    # -- state-machine recovery (Session 017A §6) ----------------------------
    def _recover_machine(self, ws: Path, status: Any) -> ProposalStateMachine | None:
        """Return the machine bound to THIS workspace, rebuilt from artifacts.

        Deterministic recovery contract:

        * the in-memory machine is discarded whenever the selected workspace
          changes — a machine is NEVER carried from workspace A into
          workspace B;
        * with no active worker, the machine is reconstructed from the
          workspace's durable status phase (an unambiguous restart at
          HARD_GATE_VALIDATION really runs gates FROM that phase, never
          from a fake IDLE);
        * an AMBIGUOUS recovery fabricates NO phase and NO runnable
          machine — execution stays disabled until the operator resolves
          it.

        Durable artifacts are never mutated here (the loader is read-only).
        """
        if self._machine is not None and self._machine_workspace == ws:
            return self._machine
        if status.ambiguous or status.phase is ProposalPhase.PAUSED:
            # Ambiguous (or pause-deskewed) recovery: no machine, no run.
            self._machine = None
            self._machine_workspace = ws
            return None
        self._machine = ProposalStateMachine(status.phase)
        self._machine_workspace = ws
        return self._machine

    def _require_ready(self) -> tuple[Path, ProposalStateMachine] | None:
        ws = self._workspace()
        if ws is None:
            self._action_note = "Choose a workspace path first."
            self.refresh_status()
            return None
        if self._running_action:
            return None
        if self._machine is None or self._machine_workspace != ws:
            # Session 017A §6: the machine ALWAYS belongs to the selected
            # workspace — a workspace switch (typed or browsed) discards the
            # old machine and reconstructs from the NEW workspace's durable
            # status.  An ambiguous recovery never fabricates a runnable
            # machine.
            status = load_workspace_status(ws)
            machine = self._recover_machine(ws, status)
            if machine is None:
                self._action_note = "Recovery requires operator confirmation"
                self.refresh_status()
                return None
            self._machine = machine
        return ws, self._machine

    def _build_drivers(self) -> tuple[dict[ProposalRole, BaseDriver], Optional[BaseDriver]]:
        """Real drivers through the registry for the configured engines.

        Tests inject scripted drivers by monkeypatching this method — the
        panel itself never hardcodes an engine or a provider.
        """
        from ..drivers import SubprocessRunner

        runner = SubprocessRunner()
        reviewers: dict[ProposalRole, BaseDriver] = {}
        orchestrator: BaseDriver | None = None
        for role in ProposalRole:
            config = self._role_configs[role]
            if not config.engine or not self.registry.is_registered(config.engine):
                raise ValueError(
                    f"{role.value}: no configured engine; set the AGENTS row first."
                )
            driver = self.registry.create(config.engine, runner)
            if role is ProposalRole.ORCHESTRATOR:
                orchestrator = driver
            else:
                reviewers[role] = driver
        return reviewers, orchestrator

    def _reviewer_configs(self) -> dict[ProposalRole, ProposalAgentConfig]:
        return {
            role: self._role_configs[role]
            for role in (
                ProposalRole.SCIENTIFIC_REVIEWER,
                ProposalRole.PROPOSAL_ENGINEER,
                ProposalRole.RED_TEAM_REVIEWER,
            )
        }

    def _base_spec_kwargs(self, ws: Path, machine: ProposalStateMachine) -> dict[str, Any]:
        return {
            "workspace": ws,
            "iteration_number": self._next_iteration_hint(ws),
            "proposal_revision": self._revision_for(ws),
            "reviewer_agent_configs": self._reviewer_configs(),
            "orchestrator_agent_config": self._role_configs[
                ProposalRole.ORCHESTRATOR
            ],
            "state_machine": machine,
            "source_budget": self._source_budget_for(),
        }

    def _next_iteration_hint(self, ws: Path) -> int:
        """The iteration number the NEXT operation legitimately uses."""
        try:
            status = load_workspace_status(ws)
        except Exception:
            return 1
        next_n = status.detail.get("next_iteration_number")
        if isinstance(next_n, int) and next_n >= 1:
            return next_n
        return max(1, status.latest_iteration)

    def _revision_for(self, ws: Path) -> str:
        return f"rev-{self._next_iteration_hint(ws)}"

    # -- action handlers (Session 020 §10–§12) --------------------------------
    def _on_generate_initial(self) -> None:
        ready = self._require_ready()
        if ready is None or self._running_action:
            return
        ws, machine = ready
        master_exists, master_nonempty = self._master_state(ws)
        if machine.phase is not ProposalPhase.IDLE or master_nonempty:
            self._action_note = (
                "GENERATE INITIAL PROPOSAL requires an EMPTY master at IDLE."
            )
            self.refresh_status()
            return
        try:
            reviewers, orchestrator = self._build_drivers()
        except ValueError as exc:
            self._action_note = str(exc)
            self.refresh_status()
            return
        if orchestrator is None:
            self._action_note = "ASTRA / ORCHESTRATOR engine is not configured."
            self.refresh_status()
            return
        base = self._base_spec_kwargs(ws, machine)
        base.pop("proposal_revision", None)
        spec = ProposalRunSpec(
            action=ProposalWorkerAction.GENERATE_INITIAL,
            iteration_number=base.pop("iteration_number", 1),
            proposal_revision=f"initial-{self._next_iteration_hint(ws)}",
            reviewer_drivers=reviewers,
            orchestrator_driver=orchestrator,
            **base,
        )
        self._start_worker(spec, "GENERATE INITIAL")

    def _on_run_panel(self) -> None:
        ready = self._require_ready()
        if ready is None or self._running_action:
            return
        ws, machine = ready
        if machine.phase not in _PANEL_RUN_PHASES:
            self._action_note = (
                "RUN PANEL ITERATION runs FULL panel iterations from IDLE "
                f"(or REVISION_REQUIRED); the machine is at "
                f"{machine.phase.value}. Use RUN ITERATION for partial-phase "
                "resume."
            )
            self.refresh_status()
            return
        try:
            reviewers, orchestrator = self._build_drivers()
        except ValueError as exc:
            self._action_note = str(exc)
            self.refresh_status()
            return
        if orchestrator is None:
            self._action_note = "ASTRA / ORCHESTRATOR engine is not configured."
            self.refresh_status()
            return
        spec = ProposalRunSpec(
            action=ProposalWorkerAction.RUN_PANEL,
            reviewer_drivers=reviewers,
            orchestrator_driver=orchestrator,
            proposal_revision=self._revision_for(ws),
            **{
                k: v
                for k, v in self._base_spec_kwargs(ws, machine).items()
                if k not in {"proposal_revision"}
            },
        )
        self._start_worker(spec, "RUN PANEL ITERATION")

    def _on_run_iteration(self) -> None:
        ready = self._require_ready()
        if ready is None or self._running_action:
            return
        ws, machine = ready
        try:
            reviewers, orchestrator = self._build_drivers()
        except ValueError as exc:
            self._action_note = str(exc)
            self.refresh_status()
            return
        # Session 017A: the FOUR current ProposalAgentConfig objects reach
        # the worker/runtime — the values the operator sees in AGENTS are
        # the exact values the drivers receive in their SessionRequest.
        spec = ProposalRunSpec(
            action=ProposalWorkerAction.RUN_ITERATION,
            workspace=ws,
            iteration_number=self._iteration_spin_value(),
            proposal_revision=self._revision_for(ws),
            reviewer_drivers=reviewers,
            orchestrator_driver=orchestrator,
            reviewer_session_policies={
                role: SessionPolicy.ALWAYS_NEW for role in reviewers
            },
            orchestrator_session_policy=SessionPolicy.ALWAYS_NEW,
            state_machine=machine,
            reviewer_agent_configs=self._reviewer_configs(),
            orchestrator_agent_config=self._role_configs[
                ProposalRole.ORCHESTRATOR
            ],
        )
        self._start_worker(spec, "RUN ITERATION")

    def _iteration_spin_value(self) -> int:
        """Legacy S017 spinner seam (kept; synced from durable state)."""
        return max(1, int(self._next_iteration_hint(self._workspace() or Path("."))))

    def _on_run_hard_gates(self) -> None:
        ready = self._require_ready()
        if ready is None or self._running_action:
            return
        ws, machine = ready
        if machine.phase is not ProposalPhase.HARD_GATE_VALIDATION:
            # Phase-aware gating (Session 017A §10): gates run ONLY from
            # HARD_GATE_VALIDATION — never from a fake IDLE after recovery.
            self._action_note = (
                "RUN HARD GATES requires phase HARD_GATE_VALIDATION "
                f"(current: {machine.phase.value})."
            )
            self.refresh_status()
            return
        spec = ProposalRunSpec(
            action=ProposalWorkerAction.RUN_HARD_GATES,
            workspace=ws,
            iteration_number=self._iteration_spin_value(),
            proposal_revision=self._revision_for(ws),
            state_machine=machine,
        )
        self._start_worker(spec, "RUN HARD GATES")

    # -- campaign controls (Session 020 §10–§13) --------------------------------
    def _campaign_config_from_ui(self) -> CampaignConfig:
        return CampaignConfig(
            max_hours=float(self.campaign_hours.value()),
            max_iterations=int(self.campaign_iterations.value()),
            target_readiness=float(self.campaign_target_readiness.value()),
            max_model_calls=int(self.campaign_max_model_calls.value()),
            no_improvement_limit=int(self.campaign_no_improvement.value()),
        )

    def _on_start_campaign(self) -> None:
        ready = self._require_ready()
        if ready is None or self._running_action:
            return
        ws, machine = ready
        if self._load_campaign_state_safe(ws) is not None and (
            self._load_campaign_state_safe(ws).status
            in (CampaignStatus.RUNNING, CampaignStatus.PAUSED)
        ):
            self._action_note = (
                "A campaign is already recorded for this workspace — use "
                "RESUME."
            )
            self.refresh_status()
            return
        try:
            reviewers, orchestrator = self._build_drivers()
        except ValueError as exc:
            self._action_note = str(exc)
            self.refresh_status()
            return
        if orchestrator is None:
            self._action_note = "ASTRA / ORCHESTRATOR engine is not configured."
            self.refresh_status()
            return
        try:
            config = self._campaign_config_from_ui()
        except ValueError as exc:
            self._action_note = f"Campaign bounds invalid: {exc}"
            self.refresh_status()
            return
        self._campaign_control = CampaignControl()
        base = self._base_spec_kwargs(ws, machine)
        base.pop("proposal_revision", None)
        spec = ProposalRunSpec(
            action=ProposalWorkerAction.START_CAMPAIGN,
            proposal_revision=f"campaign-{self._next_iteration_hint(ws)}",
            reviewer_drivers=reviewers,
            orchestrator_driver=orchestrator,
            campaign_config=config,
            campaign_control=self._campaign_control,
            **base,
        )
        self._campaign_running = True
        self._start_worker(spec, "START CAMPAIGN")

    def _on_resume_campaign(self) -> None:
        ws = self._workspace()
        if ws is None or self._running_action:
            return
        state = self._load_campaign_state_safe(ws)
        if state is None:
            self._action_note = "No durable campaign state to resume."
            self.refresh_status()
            return
        if state.status in (CampaignStatus.COMPLETE, CampaignStatus.FAILED):
            self._action_note = (
                f"Campaign is terminal ({state.status.value}); nothing to resume."
            )
            self.refresh_status()
            return
        ready = self._require_ready()
        if ready is None:
            return
        ws2, machine = ready
        try:
            reviewers, orchestrator = self._build_drivers()
        except ValueError as exc:
            self._action_note = str(exc)
            self.refresh_status()
            return
        if orchestrator is None:
            self._action_note = "ASTRA / ORCHESTRATOR engine is not configured."
            self.refresh_status()
            return
        config = self._campaign_config_from_ui()
        self._campaign_control = CampaignControl()
        base = self._base_spec_kwargs(ws2, machine)
        base.pop("proposal_revision", None)
        spec = ProposalRunSpec(
            action=ProposalWorkerAction.RESUME_CAMPAIGN,
            proposal_revision=f"campaign-{self._next_iteration_hint(ws2)}",
            reviewer_drivers=reviewers,
            orchestrator_driver=orchestrator,
            campaign_config=config,
            campaign_control=self._campaign_control,
            resume_campaign=True,
            **base,
        )
        self._campaign_running = True
        self._start_worker(spec, "RESUME CAMPAIGN")

    def _on_pause_campaign(self) -> None:
        """Boundary-request semantics (brief §13): arm the flag; the
        campaign honours it at the NEXT safe stage boundary."""
        if not self._campaign_running or self._campaign_control is None:
            return
        self._campaign_control.pause_requested = True
        self.campaign_note_label.setText(_PAUSE_REQUEST_TEXT)

    def _on_stop_campaign(self) -> None:
        if not self._campaign_running or self._campaign_control is None:
            return
        self._campaign_control.stop_requested = True
        self.campaign_note_label.setText(_STOP_REQUEST_TEXT)

    # -- worker lifecycle ---------------------------------------------------------
    def _start_worker(self, spec: ProposalRunSpec, label: str) -> None:
        self._set_running(True, label)
        self.detail_label.setText(f"Running… ({label})")
        self._action_note = ""
        self._thread, self._worker = start_proposal_worker(spec, parent=self)
        self._worker.finished.connect(self._on_worker_finished)
        # Session 017A (found by the item-9 recovery test): the factory
        # returns an UNSTARTED thread — the caller must start it.  Without
        # this the panel showed "Running…" forever and the run buttons
        # never recovered (the click path was never exercised by the S017
        # tests, which start the worker thread themselves).
        self._thread.start()

    def _on_worker_finished(self, report: object) -> None:
        self._set_running(False)
        self._last_report = report
        self.refresh_status()

    #: Last worker report (test/diagnostic surface).
    _last_report: object = None

    # -- phase-aware run controls (Session 017A §10; S020 superset) ------------
    def _apply_phase_gating(self, ws: Path | None) -> None:
        """The S017A contract entry point — now the full S020 gating."""
        self._apply_run_gating()

    # -- status rendering ---------------------------------------------------------
    def refresh_status(self) -> None:
        """Re-read durable artifacts; honest renderer, no recomputation.

        Session 020: this is also the DETERMINISTIC RECOVERY point — the
        in-memory machine is (re)bound to the selected workspace from the
        workspace's durable status, the campaign state is re-read from
        ``CAMPAIGN_STATE.json`` (read-only; a recoverable campaign enables
        RESUME — nothing auto-runs), and ``PROPOSAL_CONFIG.json``
        auto-loads ONCE per selected workspace.  While a worker runs,
        recovery updates are suppressed (the in-flight run owns them).
        """
        ws = self._workspace()
        if ws is None or not ws.is_dir():
            self.ws_status_label.setText("Workspace directory does not exist yet.")
            self.state_label.setText("Phase: IDLE")
            self.campaign_status_label.setText("Campaign: NOT STARTED")
            self._apply_phase_gating(ws)
            return
        # -- §9: config auto-load, ONCE per selected workspace ----------------
        if self._config_loaded_for != ws:
            self._config_loaded_for = ws
            loaded = self.load_role_config(ws)
            if not loaded:
                # No valid config file: rows stay unconfigured — never
                # guessed, and nothing is written merely by reading.
                self._role_configs = {
                    role: ProposalAgentConfig(role=role, engine="")
                    for role in ProposalRole
                }
                self._sync_role_rows()
        # -- §6: deterministic machine recovery (never while running) ---------
        status = load_workspace_status(ws)
        if not self._running_action:
            self._recover_machine(ws, status)
        phase = status.phase
        extra = ""
        if status.has_master_proposal:
            if status.master_proposal_empty:
                extra = " — MASTER_PROPOSAL.md is EMPTY"
            elif status.current_proposal_hash:
                self.hash_label.setText(
                    f"Proposal hash: {status.current_proposal_hash[:16]}…"
                )
        else:
            extra = " — MASTER_PROPOSAL.md is MISSING"
        if status.ambiguous:
            extra += " — Recovery requires operator confirmation"
        init_note = f"{self._init_note} " if self._init_note else ""
        action_note = f"{self._action_note} " if self._action_note else ""
        self.ws_status_label.setText(
            f"{init_note}{action_note}{status.summary}{extra} "
            f"(latest iteration: {status.latest_iteration})"
        )
        self.state_label.setText(f"Phase: {phase.value}")
        # -- Session 020 §14: readiness + campaign ---------------------------
        self._render_campaign_and_readiness(ws, status)
        # -- §6 spinner seam ---------------------------------------------------
        next_n = status.detail.get("next_iteration_number")
        if isinstance(next_n, int) and next_n >= 1:
            self.iteration_label.setText(f"Iteration: {next_n}")
            self.iteration_spin.setValue(next_n)
        elif status.latest_iteration >= 1:
            shown = max(1, status.latest_iteration)
            self.iteration_label.setText(f"Iteration: {shown}")
            self.iteration_spin.setValue(shown)
        report = self._last_report
        if isinstance(report, dict):
            outcome = str(
                report.get("outcome") or report.get("worker_error") or ""
            )
            model_calls = report.get("model_calls_used")
            calls_text = (
                f" (model calls: {model_calls})"
                if isinstance(model_calls, int)
                else ""
            )
            base = f"Last run: {outcome or '—'}{calls_text}"
            if self._running_action:
                base = f"Running… ({self._running_action})"
            self.detail_label.setText(base)
        elif self._running_action:
            self.detail_label.setText(f"Running… ({self._running_action})")
        else:
            self.detail_label.setText("")
        self._render_reviews(ws, status)
        self._render_consensus(ws)
        self._render_gates(ws, status)
        self._render_evidence(ws)
        self._render_project_inputs(ws)
        self._apply_phase_gating(ws)

    def _render_project_inputs(self, ws: Path) -> None:
        """Session 020 §6 — source statuses + budget, always rendered."""
        bp = blueprint_status(ws)
        from ..proposal.source_import import template_status

        tpl = template_status(ws)
        self.blueprint_label.setText(f"MASTER BLUEPRINT: {bp}")
        self.template_label.setText(f"OFFICIAL TEMPLATE: {tpl}")
        # Session 021: the LIVING Blueprint + DOCUMENT PAIR status.
        from ..proposal.living_blueprint import (
            blueprint_pair_status,
            try_load_blueprint_state,
        )
        from ..proposal.document_pair import try_load_document_pair_state

        pair_status = blueprint_pair_status(ws)
        try:
            bp_state = try_load_blueprint_state(ws)
        except Exception:
            bp_state = None
        current_bp_text = f"CURRENT / LIVING BLUEPRINT: {pair_status.get('current', 'MISSING')}"
        if bp_state is not None:
            current_bp_text += (
                f" — hash {bp_state.current_blueprint_hash[:16]}…"
                f" — iteration {bp_state.current_blueprint_iteration}"
            )
        self.current_blueprint_label.setText(current_bp_text)
        try:
            pair_state = try_load_document_pair_state(ws)
        except Exception:
            pair_state = None
        if pair_state is not None:
            pair_text = (
                f"DOCUMENT PAIR: iteration {pair_state.iteration}"
                f" — revision {pair_state.pair_revision_id[:12]}…"
            )
        else:
            pair_text = "DOCUMENT PAIR: not committed"
        self.pair_state_label.setText(pair_text)
        normalized = ws / "01_OFFICIAL" / "NORMALIZED"
        doc_count = 0
        if normalized.is_dir():
            doc_count = sum(
                1 for p in normalized.iterdir() if p.is_file() and p.suffix == ".md"
            )
        self.official_docs_label.setText(
            f"OFFICIAL DOCUMENTS: {doc_count} files loaded"
        )
        master_exists, master_nonempty = self._master_state(ws)
        if master_nonempty:
            self.existing_proposal_label.setText("EXISTING PROPOSAL: LOADED")
            self.master_label.setText("MASTER PROPOSAL: READY")
        elif master_exists:
            self.existing_proposal_label.setText("EXISTING PROPOSAL: EMPTY")
            self.master_label.setText("MASTER PROPOSAL: EMPTY")
        else:
            self.existing_proposal_label.setText("EXISTING PROPOSAL: EMPTY")
            self.master_label.setText("MASTER PROPOSAL: MISSING")
        budget_text = f"Source pack: 0 / {DEFAULT_SOURCE_BUDGET_CHARS} chars"
        try:
            total = check_source_budget(ws, self._source_budget_for())
            budget_text = (
                f"Source pack: {total} / {DEFAULT_SOURCE_BUDGET_CHARS} chars"
            )
        except SourceBudgetExceededError as exc:
            budget_text = (
                f"SOURCE BUDGET EXCEEDED — {exc.total_chars} / "
                f"{exc.budget_chars} chars. Raise the budget or split the "
                "source before running."
            )
        except ValueError:
            pass
        self.source_budget_label.setText(budget_text)

    def _render_campaign_and_readiness(self, ws: Path, status: Any) -> None:
        state = self._load_campaign_state_safe(ws)
        if state is None:
            self.campaign_status_label.setText("Campaign: NOT STARTED")
            self.model_calls_label.setText("Model calls used: —")
            self.elapsed_label.setText("Elapsed campaign time: —")
            self.readiness_label.setText("Internal readiness: —")
            self.readiness_history_table.setRowCount(0)
            return
        self.campaign_status_label.setText(f"Campaign: {state.status.value}")
        self.model_calls_label.setText(
            f"Model calls used: {state.model_calls_used}"
        )
        self.elapsed_label.setText(
            f"Elapsed campaign time: {state.accumulated_run_s / 3600.0:.2f} h"
        )
        if state.last_readiness is not None:
            self.readiness_label.setText(
                f"Internal readiness: {state.last_readiness:.1f}%"
            )
        else:
            self.readiness_label.setText("Internal readiness: —")
        rows = list(state.readiness_history)
        self.readiness_history_table.setRowCount(len(rows))
        for r, entry in enumerate(rows):
            self.readiness_history_table.setItem(
                r, 0, QTableWidgetItem(str(entry.get("iteration") or ""))
            )
            readiness = entry.get("readiness")
            self.readiness_history_table.setItem(
                r,
                1,
                QTableWidgetItem(
                    f"{float(readiness):.1f}%"
                    if readiness is not None
                    else "—"
                ),
            )
        if state.status is CampaignStatus.WAITING_FOR_OPERATOR:
            self.campaign_note_label.setText(_WAITING_FOR_OPERATOR_TEXT)
        elif state.status is CampaignStatus.PAUSED:
            self.campaign_note_label.setText(
                "Campaign PAUSED at a safe boundary — RESUME continues at "
                "the persisted checkpoint."
            )

    def _render_consensus(self, ws: Path) -> None:
        """Session 020 §16 — the persisted panel consensus matrix."""
        try:
            status = load_workspace_status(ws)
            iteration = status.latest_iteration
        except Exception:
            iteration = 0
        artifact = None
        if iteration >= 1:
            artifact = _read_json(
                ws
                / "04_REVIEWS"
                / f"iteration_{iteration:03d}"
                / _PANEL_CONSENSUS_FILE
            )
        if artifact is None:
            self.consensus_table.setRowCount(0)
            self.consensus_summary_label.setText("Unresolved disagreements: —")
            return
        try:
            matrix = PanelConsensusMatrix.from_dict(artifact)
        except (ValueError, KeyError):
            self.consensus_table.setRowCount(0)
            self.consensus_summary_label.setText(
                "Unresolved disagreements: — (consensus artifact unreadable)"
            )
            return
        docket = _read_json(
            ws / "04_REVIEWS" / f"iteration_{iteration:03d}" / "panel_docket.json"
        )
        sections: dict[str, str] = {}
        if docket:
            for item in docket.get("items") or []:
                if isinstance(item, dict) and item.get("item_id"):
                    sections[str(item["item_id"])] = str(
                        item.get("section") or item.get("category") or ""
                    )
        rows = matrix.rows
        self.consensus_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            status_text = "RESOLVED" if not row.unresolved else "UNRESOLVED"
            values = (
                row.item_id,
                sections.get(row.item_id, ""),
                str(row.agreement_count),
                str(row.disagreement_count),
                str(row.insufficient_count),
                "YES" if row.blocks_acceptance else "NO",
                status_text,
            )
            for c, value in enumerate(values):
                self.consensus_table.setItem(r, c, QTableWidgetItem(value))
        self.consensus_summary_label.setText(
            f"Unresolved disagreements: {matrix.unresolved_count}"
        )

    def _render_reviews(self, ws: Path, status: Any) -> None:
        it_dir = ws / "04_REVIEWS" / f"iteration_{status.latest_iteration:03d}"
        bundle = _read_json(it_dir / _BUNDLE_FILE)
        rows: list[tuple[str, str, str, str]] = []
        if bundle is not None:
            hash_text = str(bundle.get("proposal_hash") or "")[:12]
            for label, key in (
                ("SCIENTIFIC", "scientific_review"),
                ("PROPOSAL ENGINEER", "implementation_review"),
                ("RED TEAM", "red_team_review"),
            ):
                review = bundle.get(key) or {}
                verdict = str(review.get("verdict") or "—")
                findings = len(review.get("findings") or [])
                claims = len(review.get("unverified_claims") or [])
                rows.append(
                    (label, verdict, hash_text, f"{findings} findings / {claims} claims")
                )
            integration = _read_json(it_dir / "integration_result.json")
            if integration is not None:
                applied = len(integration.get("applied_items") or [])
                unresolved = len(integration.get("unresolved_items") or [])
                rows.append(
                    (
                        "ORCHESTRATOR INTEGRATION",
                        "DONE",
                        str(integration.get("input_proposal_hash") or "")[:12],
                        f"{applied} applied / {unresolved} unresolved",
                    )
                )
        else:
            for label, filename in _REVIEW_FILES:
                artifact = _read_json(it_dir / filename)
                if artifact is not None:
                    result = artifact.get("result") or {}
                    rows.append(
                        (
                            label,
                            str(result.get("verdict") or "—"),
                            str(artifact.get("proposal_hash") or "")[:12],
                            f"{len(result.get('findings') or [])} findings",
                        )
                    )
        self.review_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                self.review_table.setItem(r, c, QTableWidgetItem(value))

    def _render_gates(self, ws: Path, status: Any) -> None:
        it_dir = ws / "04_REVIEWS" / f"iteration_{status.latest_iteration:03d}"
        artifact = _read_json(it_dir / _HARD_GATES_FILE)
        if artifact is None:
            artifact = _read_json(ws / "05_CONTROL" / HARD_GATES_SNAPSHOT_FILENAME)
        results: dict[str, dict[str, Any]] = {}
        if artifact is not None:
            for entry in artifact.get("gate_results") or []:
                if isinstance(entry, dict) and entry.get("gate_id"):
                    results[str(entry["gate_id"])] = entry
        self.gate_table.setRowCount(len(HARD_GATE_IDS_TUPLE))
        for r, gate_id in enumerate(HARD_GATE_IDS_TUPLE):
            entry = results.get(gate_id)
            status_text = str(entry.get("status")) if entry else "NOT_RUN"
            message = str(entry.get("message") or "") if entry else ""
            self.gate_table.setItem(r, 0, QTableWidgetItem(gate_id))
            self.gate_table.setItem(r, 1, QTableWidgetItem(status_text))
            self.gate_table.setItem(r, 2, QTableWidgetItem(message))

    def _render_evidence(self, ws: Path) -> None:
        rows: list[tuple[str, str, str]] = []
        for filename, subdir in _EVIDENCE_FILES:
            path = ws / subdir / filename
            rows.append((filename, subdir, self._evidence_status(path)))
        self.evidence_table.setRowCount(len(rows))
        for r, (filename, subdir, status_text) in enumerate(rows):
            self.evidence_table.setItem(r, 0, QTableWidgetItem(filename))
            self.evidence_table.setItem(r, 1, QTableWidgetItem(subdir))
            self.evidence_table.setItem(r, 2, QTableWidgetItem(status_text))

    @staticmethod
    def _evidence_status(path: Path) -> str:
        """EXISTS / EMPTY / VALID JSON / INVALID JSON — no semantic claims."""
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return "MISSING"
        except OSError:
            return "UNREADABLE"
        if not raw.strip():
            return "EMPTY"
        try:
            json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "INVALID JSON"
        return "VALID JSON"

    # -- evidence skeleton (§12) ------------------------------------------------
    def _on_create_skeleton(self) -> None:
        ws = self._workspace()
        if ws is None:
            self._action_note = "Choose a workspace path first."
            self.refresh_status()
            return
        created = 0
        for filename, subdir in _EVIDENCE_FILES:
            path = ws / subdir / filename
            if path.exists():
                continue  # NEVER overwrite
            payload = self._skeleton_payload(filename)
            _atomic_write_json(path, payload)
            created += 1
        self._action_note = (
            f"Evidence skeletons created for {created} missing file(s); "
            "existing files untouched. Skeletons are NOT pass-ready evidence."
        )
        self.refresh_status()

    @staticmethod
    def _skeleton_payload(filename: str) -> dict[str, Any]:
        """Obvious operator-required placeholders — never PASS-ready."""
        required = "OPERATOR REQUIRED: replace this placeholder with real content"
        if filename == "HARD_GATE_EVIDENCE.json":
            return {
                "schema": "encomm-pcc.hard-gate-evidence/v1",
                "iteration_number": 0,
                "proposal_hash": "OPERATOR REQUIRED: exact current proposal hash",
                "applicability": {
                    "OPERATOR REQUIRED: gate id": {
                        "applicable": False,
                        "reason": required,
                    }
                },
                "_note": (
                    "Skeleton only: this file is NOT bound to an iteration or "
                    "hash and can never pass gates until the operator fills it."
                ),
            }
        if filename == "SOURCE_REGISTRY.json":
            return {
                "sources": [
                    {
                        "source_id": "OPERATOR REQUIRED",
                        "title": required,
                        "url_or_ref": required,
                    }
                ]
            }
        if filename == "CLAIM_LEDGER.json":
            return {
                "claims": [
                    {
                        "claim_id": "OPERATOR REQUIRED (canonical claim_id)",
                        "text": required,
                        "source_ids": ["OPERATOR REQUIRED"],
                    }
                ]
            }
        if filename == "UNVERIFIED_CLAIMS.json":
            return {"unresolved": ["OPERATOR REQUIRED: list unresolved claims"]}
        if filename == "PAGE_BUDGET.json":
            return {
                "measurement_method": "OPERATOR REQUIRED (exact measurement)",
                "measured_pages": None,
                "max_pages": None,
                "_note": required,
            }
        if filename == "CONTRADICTIONS.json":
            return {"unresolved": ["OPERATOR REQUIRED: list contradictions"]}
        return {"_note": required}

    def _sync_role_rows(self) -> None:
        """Push ``self._role_configs`` into the AGENTS widgets (no signals)."""
        for role in ProposalRole:
            config = self._role_configs[role]
            row = self._role_rows[role]
            index = row["engine"].findData(config.engine)
            row["engine"].setCurrentIndex(index if index >= 0 else 0)
            self._set_editable_text(row["profile"], config.project_profile)
            self._set_editable_text(row["provider"], config.provider)
            self._set_editable_text(row["model"], config.model)
            mode_index = (
                1 if config.session_mode == "RESUME_SELECTED_SESSION" else 0
            )
            row["session_mode"].setCurrentIndex(mode_index)
            # Session 022: keep the engine-honest selector state in sync.
            self._sync_reasoning_availability(role)
            self._apply_selector_gating(role)
            if config.session_id:
                if row["session"].findData(config.session_id) < 0:
                    row["session"].addItem(config.session_id, config.session_id)
                row["session"].setCurrentIndex(
                    row["session"].findData(config.session_id)
                )
            else:
                row["session"].setCurrentIndex(0)

    # -- test seam -----------------------------------------------------------------
    def machine(self) -> ProposalStateMachine:
        """The panel's proposal state machine (created lazily).

        Session 017A: the machine is BOUND to the currently selected
        workspace (``_machine_workspace``), so a test or handler that
        changed the workspace never receives the previous workspace's
        machine.
        """
        if self._machine is None or self._machine_workspace != self._workspace():
            ws = self._workspace()
            if ws is None:
                if self._machine is None:
                    self._machine = ProposalStateMachine()
                return self._machine
            status = load_workspace_status(ws)
            machine = self._recover_machine(ws, status)
            if machine is None:
                raise RuntimeError(
                    "Recovery requires operator confirmation"
                )
            self._machine = machine
        return self._machine
