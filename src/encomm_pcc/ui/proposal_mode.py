"""Proposal Mode — the operator-facing production surface (Session 017).

A THIRD MainWindow surface (mode_stack index 2).  It composes the existing
Proposal Mode backend (``encomm_pcc.proposal`` + ``encomm_pcc.proposal_runtime``)
into operator controls; it implements NO orchestration logic of its own:

* WORKSPACE — path + Initialise/Refresh over the idempotent
  :class:`~encomm_pcc.proposal.ProposalWorkspace` contract (never overwrites
  MASTER_PROPOSAL.md or any existing file);
* AGENTS — the four proposal roles, engine dropdowns fed from the REAL
  DriverRegistry (no provider/model ever hardcoded), configs persisted to an
  isolated ``05_CONTROL/PROPOSAL_CONFIG.json`` (atomic writes; Coding Mode
  role config is never touched);
* RUN — INITIALISE / RUN ITERATION / RUN HARD GATES / REFRESH, every AI
  operation on a :class:`~encomm_pcc.ui.proposal_worker.ProposalWorker`
  thread, conflicting buttons disabled while a worker runs;
* CURRENT STATE — the real :class:`~encomm_pcc.proposal.ProposalPhase`;
* REVIEW RESULTS / HARD GATES / EVIDENCE — renderers over durable artifacts
  (``04_REVIEWS/iteration_NNN/``); the UI never recomputes gate logic.

Honesty rule: every status rendered traces to a real artifact; WARN renders
INCOMPLETE, BLOCKED renders BLOCKED, nothing is ever faked green.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
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
from ..proposal.state_machine import ProposalStateMachine
from ..proposal.workspace import ProposalWorkspace
from ..proposal_runtime import (
    HARD_GATES_SNAPSHOT_FILENAME,
    load_workspace_status,
)
from .proposal_worker import ProposalRunSpec, ProposalWorkerAction, start_proposal_worker

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
        self._role_configs: dict[ProposalRole, ProposalAgentConfig] = {
            role: ProposalAgentConfig(role=role, engine="")
            for role in ProposalRole
        }
        self._thread = None
        self._worker = None
        self._running_action: str = ""
        self._init_note: str = ""

        layout = QVBoxLayout(self)

        # -- navigation ------------------------------------------------------
        nav_row = QHBoxLayout()
        self.back_button = QPushButton("BACK TO CODING MODE")
        self.back_button.clicked.connect(self._on_back)
        nav_row.addWidget(self.back_button)
        nav_row.addStretch(1)
        layout.addLayout(nav_row)

        # -- PROPOSAL WORKSPACE (§4) ------------------------------------------
        ws_box = QGroupBox("PROPOSAL WORKSPACE")
        ws_form = QGridLayout(ws_box)
        self.ws_edit = QLineEdit()
        if self.workspace_root is not None:
            self.ws_edit.setText(str(self.workspace_root))
        self.ws_edit.setPlaceholderText(r"C:\proposals\my-proposal-workspace")
        browse = QPushButton("Browse")
        browse.clicked.connect(self._on_browse)
        self.init_button = QPushButton("INITIALIZE WORKSPACE")
        self.init_button.clicked.connect(self._on_initialize)
        self.refresh_button = QPushButton("REFRESH")
        self.refresh_button.clicked.connect(self._on_refresh)
        ws_form.addWidget(QLabel("Path:"), 0, 0)
        ws_form.addWidget(self.ws_edit, 0, 1)
        ws_form.addWidget(browse, 0, 2)
        ws_form.addWidget(self.init_button, 0, 3)
        ws_form.addWidget(self.refresh_button, 0, 4)
        self.iteration_spin = QSpinBox()
        self.iteration_spin.setRange(1, 999)
        self.iteration_spin.setValue(1)
        ws_form.addWidget(QLabel("Current iteration:"), 1, 0)
        ws_form.addWidget(self.iteration_spin, 1, 1)
        self.revision_edit = QLineEdit("rev-1")
        ws_form.addWidget(QLabel("Proposal revision:"), 2, 0)
        ws_form.addWidget(self.revision_edit, 2, 1)
        self.hash_label = QLabel("MASTER_PROPOSAL hash: —")
        self.hash_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        ws_form.addWidget(self.hash_label, 3, 0, 1, 3)
        self.ws_status_label = QLabel("Workspace not initialised.")
        self.ws_status_label.setWordWrap(True)
        ws_form.addWidget(self.ws_status_label, 4, 0, 1, 5)
        layout.addWidget(ws_box)

        # -- AGENTS (§5) --------------------------------------------------------
        agents_box = QGroupBox("AGENTS")
        agents_form = QGridLayout(agents_box)
        engine_ids = self.registry.driver_ids()
        self._role_rows: dict[ProposalRole, dict[str, Any]] = {}
        for row, role in enumerate(ProposalRole):
            label = QLabel(role.value)
            engine = QComboBox()
            engine.addItem("(select engine)", "")
            for driver_id in engine_ids:
                engine.addItem(driver_id, driver_id)
            profile = QLineEdit()
            profile.setPlaceholderText("profile (optional)")
            provider = QLineEdit()
            provider.setPlaceholderText("provider (optional)")
            model = QLineEdit()
            model.setPlaceholderText("model (optional)")
            session = QLineEdit()
            session.setPlaceholderText("session id (when supported)")
            agents_form.addWidget(label, row, 0)
            agents_form.addWidget(QLabel("Engine:"), row, 1)
            agents_form.addWidget(engine, row, 2)
            agents_form.addWidget(QLabel("Profile:"), row, 3)
            agents_form.addWidget(profile, row, 4)
            agents_form.addWidget(QLabel("Provider:"), row, 5)
            agents_form.addWidget(provider, row, 6)
            agents_form.addWidget(QLabel("Model:"), row, 7)
            agents_form.addWidget(model, row, 8)
            agents_form.addWidget(QLabel("Session:"), row, 9)
            agents_form.addWidget(session, row, 10)
            engine.activated.connect(
                lambda _idx, r=role: self._on_role_changed(r)
            )
            profile.editingFinished.connect(lambda r=role: self._on_role_changed(r))
            provider.editingFinished.connect(lambda r=role: self._on_role_changed(r))
            model.editingFinished.connect(lambda r=role: self._on_role_changed(r))
            session.editingFinished.connect(lambda r=role: self._on_role_changed(r))
            self._role_rows[role] = {
                "engine": engine,
                "profile": profile,
                "provider": provider,
                "model": model,
                "session": session,
            }
        layout.addWidget(agents_box)

        # -- RUN (§8) ------------------------------------------------------------
        run_box = QGroupBox("RUN")
        run_form = QHBoxLayout(run_box)
        self.run_iteration_button = QPushButton("RUN ITERATION")
        self.run_iteration_button.clicked.connect(self._on_run_iteration)
        self.run_gates_button = QPushButton("RUN HARD GATES")
        self.run_gates_button.clicked.connect(self._on_run_hard_gates)
        run_form.addWidget(self.run_iteration_button)
        run_form.addWidget(self.run_gates_button)
        layout.addWidget(run_box)

        # -- CURRENT STATE (§9) ----------------------------------------------------
        state_box = QGroupBox("CURRENT STATE")
        state_layout = QVBoxLayout(state_box)
        self.state_label = QLabel("Phase: IDLE")
        self.state_label.setWordWrap(True)
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        state_layout.addWidget(self.state_label)
        state_layout.addWidget(self.detail_label)
        layout.addWidget(state_box)

        # -- REVIEW RESULTS (§10) ----------------------------------------------------
        reviews_box = QGroupBox("REVIEW RESULTS")
        reviews_layout = QVBoxLayout(reviews_box)
        self.review_table = QTableWidget(0, 4)
        self.review_table.setHorizontalHeaderLabels(
            ["Reviewer", "Verdict", "Proposal hash", "Findings / claims"]
        )
        self.review_table.verticalHeader().setVisible(False)
        self.review_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        reviews_layout.addWidget(self.review_table)
        layout.addWidget(reviews_box)

        # -- HARD GATES (§11) -----------------------------------------------------------
        gates_box = QGroupBox("HARD GATES")
        gates_layout = QVBoxLayout(gates_box)
        self.gate_table = QTableWidget(0, 3)
        self.gate_table.setHorizontalHeaderLabels(["Gate", "Status", "Message"])
        self.gate_table.verticalHeader().setVisible(False)
        self.gate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        gates_layout.addWidget(self.gate_table)
        layout.addWidget(gates_box)

        # -- EVIDENCE (§12) -----------------------------------------------------------------
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
        self._set_running(False)
        self.refresh_status()

    # -- workspace helpers ----------------------------------------------------
    def _workspace(self) -> Path | None:
        text = self.ws_edit.text().strip()
        return Path(text) if text else None

    def _on_browse(self) -> None:
        start = self.ws_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Proposal workspace", start)
        if chosen:
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
        self.refresh_status()

    # -- role config (file-scoped, §5) ---------------------------------------------
    def _on_role_changed(self, role: ProposalRole) -> None:
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
        session_text = row["session"].text().strip()
        if not supports_sessions:
            # Capability gate: a session id is kept ONLY when the engine
            # actually supports sessions — never sent to a stateless driver.
            session_text = ""
            row["session"].setText("")
        self._role_configs[role] = ProposalAgentConfig(
            role=role,
            engine=engine,
            project_profile=row["profile"].text().strip(),
            provider=row["provider"].text().strip(),
            model=row["model"].text().strip(),
            session_policy="persistent_optional",
            session_id=session_text,
        )
        # Persist ONLY into a chosen proposal workspace — never into the
        # process working directory (which may be the application repo).
        ws = self._workspace()
        if ws is not None:
            self.save_role_config(ws)

    def apply_role_configs(
        self, configs: dict[ProposalRole, ProposalAgentConfig]
    ) -> None:
        """Programmatic config load (tests); refreshes the widgets."""
        for role, config in configs.items():
            self._role_configs[role] = config
            row = self._role_rows[role]
            index = row["engine"].findData(config.engine)
            row["engine"].setCurrentIndex(index if index >= 0 else 0)
            row["profile"].setText(config.project_profile)
            row["provider"].setText(config.provider)
            row["model"].setText(config.model)
            row["session"].setText(config.session_id)

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
                loaded[role] = ProposalAgentConfig.from_dict(entry)
        if loaded:
            self.apply_role_configs(loaded)
        return True

    # -- run controls (§8) ---------------------------------------------------------
    def _set_running(self, running: bool, action: str = "") -> None:
        self._running_action = action if running else ""
        self.run_iteration_button.setEnabled(not running)
        self.run_gates_button.setEnabled(not running)
        self.init_button.setEnabled(not running)
        self.back_button.setEnabled(not running)

    def _require_ready(self) -> tuple[Path, ProposalStateMachine] | None:
        ws = self._workspace()
        if ws is None:
            self.detail_label.setText("Choose a workspace path first.")
            return None
        if self._machine is None:
            self._machine = ProposalStateMachine()
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

    def _on_run_iteration(self) -> None:
        ready = self._require_ready()
        if ready is None or self._running_action:
            return
        ws, machine = ready
        try:
            reviewers, orchestrator = self._build_drivers()
        except ValueError as exc:
            self.detail_label.setText(str(exc))
            return
        spec = ProposalRunSpec(
            action=ProposalWorkerAction.RUN_ITERATION,
            workspace=ws,
            iteration_number=self.iteration_spin.value(),
            proposal_revision=self.revision_edit.text().strip() or "rev-1",
            reviewer_drivers=reviewers,
            orchestrator_driver=orchestrator,
            reviewer_session_policies={
                role: SessionPolicy.ALWAYS_NEW for role in reviewers
            },
            orchestrator_session_policy=SessionPolicy.ALWAYS_NEW,
            state_machine=machine,
        )
        self._start_worker(spec, "RUN ITERATION")

    def _on_run_hard_gates(self) -> None:
        ready = self._require_ready()
        if ready is None or self._running_action:
            return
        ws, machine = ready
        spec = ProposalRunSpec(
            action=ProposalWorkerAction.RUN_HARD_GATES,
            workspace=ws,
            iteration_number=self.iteration_spin.value(),
            proposal_revision=self.revision_edit.text().strip() or "rev-1",
            state_machine=machine,
        )
        self._start_worker(spec, "RUN HARD GATES")

    def _start_worker(self, spec: ProposalRunSpec, label: str) -> None:
        self._set_running(True, label)
        self.detail_label.setText(f"Running… ({label})")
        self.state_label.setText("Phase: RUNNING")
        self._thread, self._worker = start_proposal_worker(spec, parent=self)
        self._worker.finished.connect(self._on_worker_finished)

    def _on_worker_finished(self, report: object) -> None:
        self._set_running(False)
        self._last_report = report
        self.refresh_status()

    #: Last worker report (test/diagnostic surface).
    _last_report: object = None

    # -- status rendering (§9/§10/§11/§12) --------------------------------------
    def refresh_status(self) -> None:
        """Re-read durable artifacts; honest renderer, no recomputation."""
        ws = self._workspace()
        if ws is None or not ws.is_dir():
            self.ws_status_label.setText("Workspace directory does not exist yet.")
            self.state_label.setText("Phase: IDLE")
            return
        status = load_workspace_status(ws)
        phase = status.phase
        extra = ""
        if status.has_master_proposal:
            if status.master_proposal_empty:
                extra = " — MASTER_PROPOSAL.md is EMPTY"
            elif status.current_proposal_hash:
                self.hash_label.setText(
                    f"MASTER_PROPOSAL hash: {status.current_proposal_hash[:16]}…"
                )
        else:
            extra = " — MASTER_PROPOSAL.md is MISSING"
        if status.ambiguous:
            extra += " — Recovery requires operator confirmation"
        init_note = f"{self._init_note} " if self._init_note else ""
        self.ws_status_label.setText(
            f"{init_note}{status.summary}{extra} "
            f"(latest iteration: {status.latest_iteration})"
        )
        self.state_label.setText(f"Phase: {phase.value}")
        report = self._last_report
        if isinstance(report, dict):
            outcome = str(
                report.get("outcome") or report.get("worker_error") or ""
            )
            self.detail_label.setText(f"Last run: {outcome or '—'}")
        else:
            self.detail_label.setText("")
        self._render_reviews(ws, status)
        self._render_gates(ws, status)
        self._render_evidence(ws)

    def _render_reviews(self, ws: Path, status: Any) -> None:
        it_dir = ws / "04_REVIEWS" / f"iteration_{status.latest_iteration:03d}"
        bundle = self._read_json(it_dir / _BUNDLE_FILE)
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
            integration = self._read_json(it_dir / "integration_result.json")
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
                artifact = self._read_json(it_dir / filename)
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
        artifact = self._read_json(it_dir / _HARD_GATES_FILE)
        if artifact is None:
            artifact = self._read_json(ws / "05_CONTROL" / HARD_GATES_SNAPSHOT_FILENAME)
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
            self.detail_label.setText("Choose a workspace path first.")
            return
        created = 0
        for filename, subdir in _EVIDENCE_FILES:
            path = ws / subdir / filename
            if path.exists():
                continue  # NEVER overwrite
            payload = self._skeleton_payload(filename)
            _atomic_write_json(path, payload)
            created += 1
        self.detail_label.setText(
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

    # -- test seam -----------------------------------------------------------------
    def machine(self) -> ProposalStateMachine:
        """The panel's proposal state machine (created lazily)."""
        if self._machine is None:
            self._machine = ProposalStateMachine()
        return self._machine

    @staticmethod
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
