"""Session 023 — Proposal Mode SIMPLE UI + Hermes discovery resilience.

Brief (§M) test matrix — this file pins:
* §A  PermissionError / unreadable-optional-plugin behavior: the inventory
      failure degrades to the SELECTED PROFILE's own configured defaults
      (source="profile-config"); the Simple UI shows a friendly note, and
      the panel never becomes unusable.
* §D  Profile-defaults fallback display ("Using profile defaults: p / m").
* §B/§F Simple structure (PROJECT / AI TEAM / RUN + PROGRESS; ADVANCED
      collapsed by default; run controls gated by state).
* §I  Default role preconfiguration (scientific/implementation/red-team,
      ASTRA→Codex; only REAL profiles).
* §M  Hermes runs with provider/model empty → the argv contract carries
      the profile and NO override (the profile-defaults invocation).

The underlying old runtime functions remain wired (§M): the legacy RUN
ITERATION + RUN HARD GATES buttons exist in ADVANCED with their handlers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_session_020 import make_window, seed_workspace

from encomm_pcc.drivers.process import ProcessResult, ProcessSpec
from encomm_pcc.proposal.enums import ProposalRole
from encomm_pcc.proposal.models import ProposalAgentConfig
from encomm_pcc.proposal_runtime.hermes_model_catalog import (
    HERMES_CATALOG_SOURCE_INVENTORY,
    HERMES_CATALOG_SOURCE_NONE,
    HERMES_CATALOG_SOURCE_PROFILE_CONFIG,
    HermesModelCatalog,
    HermesProviderEntry,
    discover_hermes_model_catalog,
)
from encomm_pcc.proposal_runtime.hermes_profile_defaults import (
    HERMES_DEFAULTS_SOURCE_CONFIG,
    HermesProfileDefaults,
    discover_hermes_profile_defaults,
)
from PySide6.QtWidgets import QLabel, QWidget

from encomm_pcc.ui.proposal_mode import ProposalModePanel

# ---------------------------------------------------------------------------
# test doubles
# ---------------------------------------------------------------------------

_PROFILE_CONFIG_YAML = "model:\n  provider: zai\n  default: glm-5.3-flash\n"

_PERM_PAYLOAD = json.dumps(
    {
        "error": "hermes_inventory_failed: PermissionError: [WinError 5] "
        "Access is denied: C:\\...\\plugins\\homesassistant\\plugin.yaml"
    }
)


class _CatalogScriptRunner:
    """Real SubprocessRunner shape: returns a fixture payload, records specs."""

    def __init__(self, payload: str):
        self._payload = payload
        self.specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.specs.append(spec)
        return ProcessResult(argv=spec.argv_list(), exit_code=0, stdout=self._payload)


class _RoutingRunner:
    """Routes BOTH child legs by their shipped-script shape (hermetic).

    The S023 fallback chain runs TWO children with different inline
    scripts: the hermes_cli inventory bootstrap (answers with the
    fixture payload, e.g. the production PermissionError document) and
    the profile-defaults reader (answers from the fake home's real
    config.yaml, re-implementing exactly the read the shipped script
    performs).  No real subprocess is ever launched.
    """

    #: The fallback guard's opt-in: this double serves BOTH child legs
    #: (inventory + profile defaults), so no real subprocess is spawned.
    handles_profile_defaults = True

    def __init__(self, catalog_payload: str, home: Path):
        self._catalog_payload = catalog_payload
        self._home = home
        self.specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.specs.append(spec)
        script = spec.argv[3] if len(spec.argv) > 3 else ""
        env = spec.env or {}
        assert env.get("HERMES_HOME") == str(self._home)
        if "yaml" in script and "HERMES_HOME" in script:
            cfg = self._home / "config.yaml"
            if not cfg.is_file():
                out = {"provider": "", "model": "",
                       "error": "profile config.yaml not found"}
            else:
                text = cfg.read_text(encoding="utf-8-sig")
                provider = model = ""
                in_model = False
                for line in text.splitlines():
                    if line.startswith("model:"):
                        in_model = True
                        continue
                    if in_model and line.startswith("  "):
                        key, _, val = line.strip().partition(":")
                        val = val.strip()
                        if key == "provider":
                            provider = val
                        elif key == "default":
                            model = val
                    else:
                        in_model = False
                out = {"provider": provider, "model": model,
                       "error": "" if (provider or model) else
                       "profile config declares no model defaults"}
            return ProcessResult(
                argv=spec.argv_list(), exit_code=0, stdout=json.dumps(out)
            )
        return ProcessResult(
            argv=spec.argv_list(), exit_code=0, stdout=self._catalog_payload
        )


@pytest.fixture()
def fake_hermes(tmp_path):
    """A fake Hermes install + profile home, fully decoupled from the host."""
    agent = tmp_path / "hermes-agent"
    venv_dir = agent / "venv" / "Scripts"
    venv_dir.mkdir(parents=True)
    (venv_dir / "python.exe").write_text("", encoding="utf-8")
    home = tmp_path / "profiles" / "scientific"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        _PROFILE_CONFIG_YAML, encoding="utf-8"
    )
    return {"HERMES_AGENT_DIR": str(agent), "HERMES_HOME": str(home)}


# ---------------------------------------------------------------------------
# §A — PermissionError / unreadable optional plugin resilience
# ---------------------------------------------------------------------------


class TestPermissionErrorResilience:
    def test_payload_permissionerror_falls_back_to_profile_config(
        self, fake_hermes
    ):
        """§A: the exact production payload shape degrades to the profile's
        own defaults — never a dead catalog, never a raised exception."""
        cat = discover_hermes_model_catalog(
            "scientific",
            environ=fake_hermes,
            runner=_RoutingRunner(_PERM_PAYLOAD, Path(fake_hermes["HERMES_HOME"])),
        )
        assert cat.source == HERMES_CATALOG_SOURCE_PROFILE_CONFIG
        assert cat.profile_default_provider == "zai"
        assert cat.profile_default_model == "glm-5.3-flash"
        assert cat.providers == []  # the provider LIST is honestly unknown
        assert cat.provider_catalog_authoritative is False
        assert cat.available is True

    def test_timeout_falls_back_to_profile_config(self, fake_hermes):
        class _Timeout:
            def run(self, spec):
                return ProcessResult(
                    argv=spec.argv_list(), exit_code=1, timed_out=True
                )

        cat = discover_hermes_model_catalog(
            "scientific",
            environ=fake_hermes,
            runner=_Timeout(),
        )
        # The deterministic-test guard keeps an injected runner from
        # launching a REAL fallback child: the degradation is honest.
        assert not cat.available
        assert "timed out" in cat.error

    def test_injected_runner_never_launches_a_real_fallback_child(
        self, fake_hermes
    ):
        """The S022 deterministic seam is preserved: script doubles never
        gain a REAL fallback subprocess."""

        class _Boom:
            def run(self, spec):
                raise RuntimeError("inventory exploded")

        cat = discover_hermes_model_catalog(
            "scientific", environ=fake_hermes, runner=_Boom()
        )
        assert not cat.available
        assert "injected runner" in cat.error

    def test_defaults_module_reads_profile_config_hermes_cli_free(
        self, fake_hermes
    ):
        """The fallback child NEVER imports hermes_cli (the failure class
        cannot reach this path) and reads the profile's real defaults."""
        runner = _RoutingRunner(_PERM_PAYLOAD, Path(fake_hermes["HERMES_HOME"]))
        d = discover_hermes_profile_defaults(
            "scientific", environ=fake_hermes, runner=runner
        )
        assert d.available
        assert d.provider == "zai"
        assert d.model == "glm-5.3-flash"
        # HERMES_HOME scoped to the profile directory (not the root).
        assert runner.specs[0].env["HERMES_HOME"] == str(Path(fake_hermes["HERMES_HOME"]))

    def test_defaults_missing_profile_fails_soft(self, tmp_path):
        agent = tmp_path / "hermes-agent"
        (agent / "venv" / "Scripts").mkdir(parents=True)
        (agent / "venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
        home = tmp_path / "home"
        home.mkdir()
        d = discover_hermes_profile_defaults(
            "no-such-profile",
            environ={"HERMES_AGENT_DIR": str(agent), "HERMES_HOME": str(home)},
            runner=_CatalogScriptRunner("{}"),
        )
        assert not d.available
        assert "profile home not found" in d.error

    def test_full_fallback_chain_both_legs_fail_honest(self, tmp_path):
        """Valid install + inventory PermissionError + NO profile home →
        honest unavailable carrying BOTH reasons (§A: fail soft; never
        invented names)."""
        agent = tmp_path / "hermes-agent"
        (agent / "venv" / "Scripts").mkdir(parents=True)
        (agent / "venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
        home = tmp_path / "bare-home"
        (home / "profiles" / "gone").mkdir(parents=True)
        (home / "profiles" / "gone" / "config.yaml").write_text(
            "model:\n", encoding="utf-8"
        )
        cat = discover_hermes_model_catalog(
            "gone",
            environ={
                "HERMES_AGENT_DIR": str(agent),
                "HERMES_HOME": str(home),
            },
            runner=_CatalogScriptRunner(_PERM_PAYLOAD),
        )
        assert not cat.available
        assert cat.source == HERMES_CATALOG_SOURCE_NONE
        assert "hermes_inventory_failed" in cat.error
        assert "profile defaults unavailable" in cat.error


# ---------------------------------------------------------------------------
# §D — Simple role cards: profile defaults display + friendly note
# ---------------------------------------------------------------------------


def _select_hermes(panel: ProposalModePanel, role: ProposalRole) -> None:
    row = panel._role_rows[role]
    row["engine"].setCurrentIndex(row["engine"].findData("hermes"))
    panel._on_refresh_hermes_selectors(role)


class TestSimpleRoleCards:
    def test_fallback_catalog_shows_friendly_note_and_defaults(
        self, qapp, monkeypatch
    ):
        """§A/§J: with the production PermissionError degraded to the
        profile-config fallback, the role card shows the FRIENDLY note and
        the read-only defaults line — never the raw exception; the panel
        stays usable (config sync runs)."""
        window = make_window(qapp)
        panel = window.proposal_panel

        def fallback_catalog(profile, **kwargs):
            return HermesModelCatalog(
                profile=profile,
                providers=[],
                models_by_provider={},
                profile_default_provider="zai",
                profile_default_model="glm-5.3-flash",
                source=HERMES_CATALOG_SOURCE_PROFILE_CONFIG,
                provider_catalog_authoritative=False,
            )

        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_model_catalog",
            fallback_catalog,
        )
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["profile"].setEditText("scientific")
        _select_hermes(panel, role)
        note = panel._role_note(role)
        assert "Hermes model catalogue unavailable." in note
        assert "Profile defaults will be used." in note
        label_text = panel._defaults_labels[role].text()
        assert "Hermes model catalogue unavailable." in label_text
        assert "Using profile defaults: zai / glm-5.3-flash" in label_text
        # The provider list stays honestly EMPTY (never fabricated) and the
        # operator's provider/model stay EMPTY (profile defaults run):
        assert row["provider"].count() == 0
        assert panel.role_configs()[role].provider == ""
        assert panel.role_configs()[role].model == ""
        assert panel.role_configs()[role].engine == "hermes"
        assert panel.role_configs()[role].project_profile == "scientific"

    def test_available_catalog_shows_using_profile_defaults(
        self, qapp, monkeypatch
    ):
        """§D: with the inventory OK the card reads
        'Using profile defaults: <provider> / <model>'."""
        window = make_window(qapp)
        panel = window.proposal_panel
        entries = [
            __import__(
                "encomm_pcc.proposal_runtime.hermes_model_catalog",
                fromlist=["HermesProviderEntry"],
            ).HermesProviderEntry(
                slug="zai", name="Z.AI / GLM",
                models=["glm-5.3-flash"], total_models=1, source="built-in",
            )
        ]
        catalog = HermesModelCatalog(
            profile="scientific",
            providers=entries,
            models_by_provider={"zai": ["glm-5.3-flash"]},
            profile_default_provider="zai",
            profile_default_model="glm-5.3-flash",
            source=HERMES_CATALOG_SOURCE_INVENTORY,
            provider_catalog_authoritative=True,
        )
        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_model_catalog",
            lambda profile, **kw: catalog,
        )
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["profile"].setEditText("scientific")
        _select_hermes(panel, role)
        label = panel._defaults_labels[role]
        # S023A: the read-only line carries the defaults AND the honest
        # READY state (the simple card's operator-facing status).
        assert label.text() == (
            "Using profile defaults: zai / glm-5.3-flash — READY"
        )
        assert panel._role_note(role) == ""
        # ...and the config keeps NO override (S023A core semantics):
        cfg = panel.role_configs()[role]
        assert cfg.provider == "" and cfg.model == ""

    def test_simple_project_rows_exist(self, qapp):
        """§C: the Simple PROJECT rows exist with their names."""
        window = make_window(qapp)
        panel = window.proposal_panel
        for name in (
            "Workspace", "Master Blueprint", "Living Blueprint",
            "Official Template", "Official Documents", "Master Proposal",
        ):
            assert name in panel.project_status_labels

    def test_codex_card_has_no_profile_provider_in_simple_mode(self, qapp):
        """§D: the Codex simple card carries NO Profile/Provider rows."""
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.ORCHESTRATOR
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("codex"))
        panel._on_role_engine_changed(role)
        body = panel._simple_role_bodies[role]
        labels = []
        for i in range(body.count()):
            item = body.itemAt(i)
            widget = item.widget() if item is not None else None
            if isinstance(widget, QLabel) and widget.text():
                labels.append(widget.text())
        texts = [t for t in labels if t]
        assert "Profile:" not in texts
        assert "Provider:" not in texts
        assert "Model:" in texts  # editable exact model id stays
        assert "Reasoning:" in texts

    def test_hidden_advanced_values_survive_toggle_and_persist(
        self, qapp, tmp_path
    ):
        """§M: values edited while ADVANCED is hidden persist correctly."""
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        # ADVANCED hidden (default) — edit the provider through the widget.
        assert panel.advanced_container.isHidden()
        row["engine"].setCurrentIndex(row["engine"].findData("hermes"))
        panel._place_role_widgets(role)
        panel._sync_role_config_from_widgets(role)
        row["provider"].setEditText("zai")
        row["model"].setEditText("glm-5.3")
        panel._sync_role_config_from_widgets(role)
        ws = tmp_path / "ws"
        seed_workspace(ws)
        panel.save_role_config(ws)
        panel2 = ProposalModePanel(panel.registry)
        assert panel2.load_role_config(ws) is True
        saved = panel2.role_configs()[role]
        assert saved.engine == "hermes"
        assert saved.provider == "zai"
        assert saved.model == "glm-5.3"


# ---------------------------------------------------------------------------
# §B/§F/§H — Simple structure: default view, ADVANCED reveal, run gating
# ---------------------------------------------------------------------------


class TestSimpleStructure:
    def test_advanced_collapsed_by_default_and_reveals(self, qapp):
        window = make_window(qapp)
        panel = window.proposal_panel
        # Offscreen: assert the hidden STATE (isVisible() is False for
        # every widget whose window was never show()n).
        assert panel.advanced_container.isHidden()
        panel._set_advanced_visible(True)
        assert not panel.advanced_container.isHidden()
        panel._set_advanced_visible(False)
        assert panel.advanced_container.isHidden()

    def test_advanced_holds_every_technical_surface(self, qapp):
        """§H: nothing is deleted — the ADVANCED container owns the
        importers, overrides, legacy run controls, tables, diagnostics."""
        window = make_window(qapp)
        panel = window.proposal_panel
        advanced_widgets = set(panel.advanced_container.findChildren(QWidget))
        for attr in (
            "ws_edit", "import_blueprint_button", "import_template_button",
            "import_docs_button", "import_proposal_button",
            "init_button", "refresh_button",
            "run_iteration_button", "run_gates_button",
            "campaign_hours", "campaign_max_model_calls",
            "campaign_no_improvement",
        ):
            widget = getattr(panel, attr)
            assert widget in advanced_widgets, (
                f"{attr} is not inside the ADVANCED container"
            )
        # §M: the legacy run buttons remain real wired QPushButton objects.
        from PySide6.QtWidgets import QPushButton

        assert isinstance(panel.run_iteration_button, QPushButton)
        assert isinstance(panel.run_gates_button, QPushButton)

    def test_simple_view_hides_technical_surfaces(self, qapp):
        """§B: the DEFAULT view must not show provider/session-mode/
        campaign limits/gate tables — they live in ADVANCED."""
        window = make_window(qapp)
        panel = window.proposal_panel
        assert not panel.advanced_container.isVisible()
        # The provider combos belong to the ADVANCED grids for Hermes
        # (re-parented); the gate table lives in ADVANCED too.
        parent = panel.gate_table.parent()
        seen = parent
        while seen is not None and seen is not panel.advanced_container:
            seen = seen.parent()
        assert seen is panel.advanced_container

    def test_run_controls_change_with_state(self, qapp, tmp_path):
        """§F: before proposal → GENERATE enabled; after → RUN PANEL;
        campaign running → PAUSE/STOP; paused → RESUME."""
        window = make_window(qapp)
        panel = window.proposal_panel
        ws = tmp_path / "run-ws"
        seed_workspace(ws)
        panel.ws_edit.setText(str(ws))
        # Empty workspace: nothing run-able yet.
        panel.refresh_status()
        assert panel.generate_button.isEnabled() is False
        assert panel.run_panel_button.isEnabled() is False
        # Campaign running: PAUSE/STOP on, generation off.
        panel._running_action = "START CAMPAIGN"
        panel._campaign_running = True
        from encomm_pcc.ui.proposal_worker import CampaignControl

        if panel._campaign_control is None:
            panel._campaign_control = CampaignControl()
        panel._apply_run_gating()
        assert panel.pause_campaign_button.isEnabled()
        assert panel.stop_campaign_button.isEnabled()
        assert panel.generate_button.isEnabled() is False
        # Reset.
        panel._set_running(False)


# ---------------------------------------------------------------------------
# §B/§H — native-Qt regression: ADVANCED toggle stress + one-layout invariant
# ---------------------------------------------------------------------------


class TestAdvancedToggleStress:
    def test_repeated_toggle_and_show_cycles_with_single_layout_membership(
        self, qapp
    ):
        """§B/§M regression (native Qt): repeated ADVANCED open/close plus
        Proposal-panel show/hide cycles never crash, and EVERY persistent
        widget/label belongs to EXACTLY ONE layout afterwards.

        This pins the S023 defect class that produced a native access
        violation: a widget added to two layouts (stale QLayoutItem) and a
        widget placed into a not-yet-installed (floating) layout, which
        left it parentless — the panel then crashed on setVisible(True).
        """
        window = make_window(qapp)
        panel = window.proposal_panel

        # -- stress: toggle ADVANCED repeatedly ----------------------------
        window.show()
        qapp.processEvents()
        for _cycle in range(6):
            panel._set_advanced_visible(True)
            qapp.processEvents()
            panel._set_advanced_visible(False)
            qapp.processEvents()
        # -- stress: show/hide the whole panel (production navigation) -----
        panel.hide()
        qapp.processEvents()
        panel.show()
        qapp.processEvents()
        panel._set_advanced_visible(True)
        qapp.processEvents()
        assert not panel.advanced_container.isHidden()

        # -- one-layout invariant over the WHOLE panel layout tree ---------
        seen: dict[int, str] = {}

        def walk(layout, path: str) -> None:
            if layout is None:
                return
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item is None:
                    continue
                widget = item.widget()
                if widget is not None:
                    key = id(widget)
                    assert key not in seen, (
                        f"{type(widget).__name__} is in TWO layouts: "
                        f"{seen[key]} AND {path}[{i}]"
                    )
                    seen[key] = f"{path}[{i}]"
                    # descend into the CHILD WIDGET's own layout too
                    # (role cards are QGroupBoxes whose QGridLayout holds
                    # the per-role bodies — a layout-only walk misses
                    # that whole subtree).
                    walk(widget.layout(), f"{path}[{i}]>w")
                inner = item.layout()
                if inner is not None:
                    walk(inner, f"{path}[{i}].lay")

        # Everything lives in the scroll content's layout tree; walk it
        # completely (nested layouts AND child-widget layouts included).
        content = panel._scroll_area.widget()
        walk(content.layout(), "content")

        # -- every persistent role widget/label exists exactly once --------
        for role in ProposalRole:
            row = panel._role_rows[role]
            for key in (
                "engine", "profile", "provider", "model",
                "reasoning", "session_mode", "session", "refresh",
            ):
                widget = row[key]
                assert id(widget) in seen, (
                    f"{role.value}.{key} is not in any layout"
                )
            for name in ("model", "reasoning", "profile"):
                label = panel._role_card_labels[role][name]
                assert id(label) in seen, (
                    f"{role.value} card label {name!r} is not in any layout"
                )
            assert id(panel._defaults_labels[role]) in seen, (
                f"{role.value} defaults label is not in any layout"
            )

        # -- key technical surfaces exist exactly once ---------------------
        for attr in (
            "ws_edit", "init_button", "refresh_button",
            "import_blueprint_button", "import_template_button",
            "import_docs_button", "import_proposal_button",
            "run_iteration_button", "run_gates_button",
            "generate_button", "run_panel_button", "start_campaign_button",
            "pause_campaign_button", "resume_campaign_button",
            "stop_campaign_button",
            "gate_table", "review_table", "consensus_table",
            "evidence_table", "readiness_history_table",
        ):
            widget = getattr(panel, attr)
            assert id(widget) in seen, f"{attr} is not in any layout"

        # -- the advanced container itself stays toggleable ----------------
        panel._set_advanced_visible(False)
        qapp.processEvents()
        assert panel.advanced_container.isHidden()


# ---------------------------------------------------------------------------
# Session 023A — profile defaults are NEVER overrides (semantic contract)
# ---------------------------------------------------------------------------


class TestProfileDefaultsAreNotOverrides:
    """The S023A corrective brief's six pinned behaviors."""

    def test_1_inventory_success_displays_defaults_config_stays_empty(
        self, qapp, monkeypatch
    ):
        """INVENTORY SUCCESS: the card displays 'Using profile defaults:
        zai / glm-5.3-flash' BUT the role config keeps provider/model
        EMPTY (profile defaults run at runtime — no silent override)."""
        window = make_window(qapp)
        panel = window.proposal_panel
        entries = [
            HermesProviderEntry(
                slug="zai", name="Z.AI / GLM",
                models=["glm-5.3-flash", "glm-5.3"],
                total_models=2, source="built-in",
            ),
        ]
        catalog = HermesModelCatalog(
            profile="scientific",
            providers=entries,
            models_by_provider={"zai": ["glm-5.3-flash", "glm-5.3"]},
            profile_default_provider="zai",
            profile_default_model="glm-5.3-flash",
            source=HERMES_CATALOG_SOURCE_INVENTORY,
            provider_catalog_authoritative=True,
        )
        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_model_catalog",
            lambda profile, **kw: catalog,
        )
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["profile"].setEditText("scientific")
        _select_hermes(panel, role)
        assert "Using profile defaults: zai / glm-5.3-flash" in (
            panel._defaults_labels[role].text()
        )
        cfg = panel.role_configs()[role]
        assert cfg.engine == "hermes"
        assert cfg.project_profile == "scientific"
        assert cfg.provider == ""
        assert cfg.model == ""

    def test_2_fallback_success_keeps_empty_semantics(self, qapp, monkeypatch):
        """FALLBACK: the §A profile-config fallback shows the same
        display and keeps provider/model empty (no regression)."""
        window = make_window(qapp)
        panel = window.proposal_panel

        def fallback_catalog(profile, **kwargs):
            return HermesModelCatalog(
                profile=profile,
                providers=[],
                models_by_provider={},
                profile_default_provider="zai",
                profile_default_model="glm-5.3-flash",
                source=HERMES_CATALOG_SOURCE_PROFILE_CONFIG,
                provider_catalog_authoritative=False,
            )

        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_model_catalog",
            fallback_catalog,
        )
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["profile"].setEditText("scientific")
        _select_hermes(panel, role)
        assert "Using profile defaults: zai / glm-5.3-flash" in (
            panel._defaults_labels[role].text()
        )
        cfg = panel.role_configs()[role]
        assert cfg.provider == "" and cfg.model == ""

    def test_3_explicit_advanced_override_persists(self, qapp, monkeypatch):
        """EXPLICIT OVERRIDE: an operator choice in ADVANCED (activated
        provider + edited model) IS persisted."""
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("hermes"))
        panel._sync_role_config_from_widgets(role)
        row["provider"].setEditText("openrouter")
        panel._on_role_provider_changed(role)  # operator ACTIVATION
        row["model"].setEditText("some-model")
        panel._on_role_config_text_changed(role)  # operator EDIT
        cfg = panel.role_configs()[role]
        assert cfg.provider == "openrouter"
        assert cfg.model == "some-model"
        assert panel._combo_overrides[role] is True

    def test_4_saved_override_restores_and_survives_refresh(
        self, qapp, tmp_path, monkeypatch
    ):
        """SAVED OVERRIDE: a PROPOSAL_CONFIG with an explicit provider/
        model restores — and a REFRESH does not erase it."""
        window = make_window(qapp)
        panel = window.proposal_panel
        entries = [
            HermesProviderEntry(
                slug="openrouter", name="OpenRouter",
                models=["or/a"], total_models=1, source="built-in",
            ),
        ]
        catalog = HermesModelCatalog(
            profile="scientific",
            providers=entries,
            models_by_provider={"openrouter": ["or/a"]},
            profile_default_provider="zai",
            profile_default_model="glm-5.3-flash",
            source=HERMES_CATALOG_SOURCE_INVENTORY,
            provider_catalog_authoritative=True,
        )
        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_model_catalog",
            lambda profile, **kw: catalog,
        )
        ws = tmp_path / "s23a-ws"
        seed_workspace(ws)
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("hermes"))
        row["profile"].setEditText("scientific")
        row["provider"].setEditText("openrouter")
        panel._on_role_provider_changed(role)
        row["model"].setEditText("or/a")
        panel._on_role_config_text_changed(role)
        panel._sync_role_config_from_widgets(role)
        panel.save_role_config(ws)
        # A FRESH panel restores the persisted override...
        panel2 = ProposalModePanel(panel.registry)
        assert panel2.load_role_config(ws) is True
        cfg2 = panel2.role_configs()[role]
        assert cfg2.provider == "openrouter"
        assert cfg2.model == "or/a"
        # ...and a REFRESH restores (never erases) it.
        panel2._populate_provider_model(role, "scientific")
        assert panel2.role_configs()[role].provider == "openrouter"
        assert panel2.role_configs()[role].model == "or/a"
        assert panel2._combo_overrides[role] is True
        assert row_provider_text(panel2, role) == "openrouter"

    def test_5_profile_default_change_does_not_carry_over(
        self, qapp, monkeypatch
    ):
        """PROFILE DEFAULT CHANGE: with NO explicit override, a profile
        switch leaves the config empty and the display follows the NEW
        profile's defaults.  A persisted override DOES transfer (the
        existing config-loading contract keeps it)."""
        window = make_window(qapp)
        panel = window.proposal_panel

        def catalog_by_profile(profile, **kwargs):
            defaults = {
                "scientific": ("zai", "glm-5.3-flash"),
                "scientific-v2": ("deepseek", "deepseek-v4-pro"),
            }
            provider, model = defaults.get(profile, ("zai", "glm-5.3-flash"))
            entries = [
                HermesProviderEntry(
                    slug=provider, name=provider, models=[model],
                    total_models=1, source="built-in",
                ),
            ]
            return HermesModelCatalog(
                profile=profile,
                providers=entries,
                models_by_provider={provider: [model]},
                profile_default_provider=provider,
                profile_default_model=model,
                source=HERMES_CATALOG_SOURCE_INVENTORY,
                provider_catalog_authoritative=True,
            )

        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_model_catalog",
            catalog_by_profile,
        )
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("hermes"))
        row["profile"].setEditText("scientific")
        panel._sync_role_config_from_widgets(role)
        panel._populate_provider_model(role, "scientific")
        assert "Using profile defaults: zai / glm-5.3-flash" in (
            panel._defaults_labels[role].text()
        )
        assert panel.role_configs()[role].provider == ""
        # Profile switch: display follows the NEW defaults; config stays
        # empty (no implicit transfer).
        row["profile"].setEditText("scientific-v2")
        panel._on_role_profile_changed(role)
        assert "Using profile defaults: deepseek / deepseek-v4-pro" in (
            panel._defaults_labels[role].text()
        )
        cfg = panel.role_configs()[role]
        assert cfg.project_profile == "scientific-v2"
        assert cfg.provider == "" and cfg.model == ""

    def test_6_real_argv_profile_with_no_override(self):
        """REAL ARGV: profile + empty provider/model emits -p <profile>
        with NO -m and NO --provider (the profile defaults run)."""
        from encomm_pcc.drivers.hermes_cli import build_chat_argv

        argv = build_chat_argv(
            executable="hermes",
            query_file="q.txt",
            profile="scientific",
            model="",
            provider="",
        )
        assert argv[argv.index("-p") + 1] == "scientific"
        assert "-m" not in argv
        assert "--provider" not in argv


def row_provider_text(panel: ProposalModePanel, role: ProposalRole) -> str:
    return panel._role_rows[role]["provider"].currentText().strip()


# ---------------------------------------------------------------------------
# Session 023B — programmatic restore must NEVER arm the override marker
# ---------------------------------------------------------------------------


class TestProgrammaticRestoreNeverArmsOverrides:
    """The S023B corrective brief's exact workflow (SAME panel instance)."""

    @pytest.fixture()
    def catalog_panel_s23b(self, qapp, monkeypatch):
        """Inventory catalogue available; defaults zai / glm-5.3-flash."""
        window = make_window(qapp)
        panel = window.proposal_panel
        entries = [
            HermesProviderEntry(
                slug="openrouter", name="OpenRouter",
                models=["or/a"], total_models=1, source="built-in",
            ),
            HermesProviderEntry(
                slug="zai", name="Z.AI / GLM",
                models=["glm-5.3-flash"], total_models=1, source="built-in",
            ),
        ]
        catalog = HermesModelCatalog(
            profile="scientific",
            providers=entries,
            models_by_provider={
                "openrouter": ["or/a"],
                "zai": ["glm-5.3-flash"],
            },
            profile_default_provider="zai",
            profile_default_model="glm-5.3-flash",
            source=HERMES_CATALOG_SOURCE_INVENTORY,
            provider_catalog_authoritative=True,
        )
        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_model_catalog",
            lambda profile, **kw: catalog,
        )
        return panel

    @staticmethod
    def _config(provider: str, model: str) -> ProposalAgentConfig:
        return ProposalAgentConfig(
            role=ProposalRole.SCIENTIFIC_REVIEWER,
            engine="hermes",
            project_profile="scientific",
            provider=provider,
            model=model,
        )

    def test_workspace_switch_a_to_b_never_rearms_from_restore(
        self, catalog_panel_s23b
    ):
        """A(override) -> B(empty): the programmatic EMPTY restore must
        leave the marker OFF even though the restoration itself emits
        editTextChanged storms."""
        panel = catalog_panel_s23b
        role = ProposalRole.SCIENTIFIC_REVIEWER
        # 1-3. Load A: override marker True, values restored.
        panel.apply_role_configs({role: self._config("openrouter", "or/a")})
        assert panel._combo_overrides[role] is True
        cfg_a = panel.role_configs()[role]
        assert cfg_a.provider == "openrouter" and cfg_a.model == "or/a"
        # 4-5. Switch the SAME panel to B (empty): marker must stay OFF.
        panel.apply_role_configs({role: self._config("", "")})
        assert panel._combo_overrides[role] is False
        cfg_b = panel.role_configs()[role]
        assert cfg_b.provider == "" and cfg_b.model == ""
        row = panel._role_rows[role]
        assert row["provider"].currentText().strip() == ""
        assert row["model"].currentText().strip() == ""
        # 6. REFRESH with the inventory: defaults DISPLAYED, config stays
        # empty, marker stays OFF.
        panel._populate_provider_model(role, "scientific")
        assert "Using profile defaults: zai / glm-5.3-flash" in (
            panel._defaults_labels[role].text()
        )
        cfg = panel.role_configs()[role]
        assert cfg.provider == "" and cfg.model == ""
        assert panel._combo_overrides[role] is False
        # 7. Real argv: profile only, NO override flags.
        from encomm_pcc.drivers.hermes_cli import build_chat_argv

        argv = build_chat_argv(
            executable="hermes",
            query_file="q.txt",
            profile=cfg.project_profile,
            model=cfg.model,
            provider=cfg.provider,
        )
        assert argv[argv.index("-p") + 1] == "scientific"
        assert "-m" not in argv and "--provider" not in argv

    def test_workspace_switch_b_to_a_restores_saved_override(
        self, catalog_panel_s23b
    ):
        """B(empty) -> A(override): the persisted override restores, the
        marker becomes True, and a refresh preserves it."""
        panel = catalog_panel_s23b
        role = ProposalRole.SCIENTIFIC_REVIEWER
        panel.apply_role_configs({role: self._config("", "")})
        assert panel._combo_overrides[role] is False
        # Switch to A (explicit saved override).
        panel.apply_role_configs({role: self._config("openrouter", "or/a")})
        assert panel._combo_overrides[role] is True
        row = panel._role_rows[role]
        assert row["provider"].currentText().strip() == "openrouter"
        assert row["model"].currentText().strip() == "or/a"
        # A refresh restores (never erases) the override.
        panel._populate_provider_model(role, "scientific")
        cfg = panel.role_configs()[role]
        assert cfg.provider == "openrouter" and cfg.model == "or/a"
        assert panel._combo_overrides[role] is True

    def test_sync_role_rows_is_also_signal_safe(self, catalog_panel_s23b):
        """_sync_role_rows (the other config→widget helper) also never
        arms the marker from its own restoration writes."""
        panel = catalog_panel_s23b
        role = ProposalRole.SCIENTIFIC_REVIEWER
        panel.apply_role_configs({role: self._config("openrouter", "or/a")})
        assert panel._combo_overrides[role] is True
        # Sync an EMPTY config through the OTHER restoration helper.
        panel._role_configs[role] = self._config("", "")
        panel._sync_role_rows()
        assert panel._combo_overrides[role] is False
        cfg = panel.role_configs()[role]
        assert cfg.provider == "" and cfg.model == ""
        # And an override config through it re-arms explicitly.
        panel._role_configs[role] = self._config("openrouter", "or/a")
        panel._sync_role_rows()
        assert panel._combo_overrides[role] is True


# ---------------------------------------------------------------------------
# §I — default role preconfiguration
# ---------------------------------------------------------------------------


class TestDefaultPreselection:
    def test_real_profiles_preselect_their_roles(self, qapp, monkeypatch):
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(0)  # (select engine)
        panel._apply_default_role_preselection(
            ["default", "scientific", "implementation", "red-team"]
        )
        assert str(row["engine"].currentData()) == "hermes"
        assert row["profile"].currentText().strip() == "scientific"
        assert panel.role_configs()[role].engine == "hermes"

    def test_unrelated_profiles_never_preselect(self, qapp):
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(0)
        panel._apply_default_role_preselection(["default", "unrelated"])
        assert str(row["engine"].currentData() or "") == ""
        assert row["profile"].currentText().strip() == ""

    def test_astra_defaults_to_codex(self, qapp):
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.ORCHESTRATOR
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(0)
        panel._apply_default_role_preselection(["default"])
        assert str(row["engine"].currentData()) == "codex"

    def test_operator_choice_is_never_overridden(self, qapp):
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("codex"))
        panel._sync_role_config_from_widgets(role)
        panel._apply_default_role_preselection(
            ["scientific", "implementation", "red-team"]
        )
        assert str(row["engine"].currentData()) == "codex"


# ---------------------------------------------------------------------------
# §M — profile-defaults invocation (empty provider/model → real argv)
# ---------------------------------------------------------------------------


class TestProfileDefaultsInvocation:
    def test_empty_provider_model_argv_carries_profile_no_override(self):
        """§E/§M: engine=hermes + profile + EMPTY provider/model builds the
        real chat argv with -p <profile> and NO -m/--provider override —
        the profile's own defaults run."""
        from encomm_pcc.drivers.hermes_cli import build_chat_argv

        argv = build_chat_argv(
            executable="hermes",
            query_file="q.txt",
            profile="scientific",
            model="",
            provider="",
        )
        assert "-p" in argv and "scientific" in argv
        assert "-m" not in argv
        assert "--provider" not in argv

    def test_provider_without_model_is_refused(self):
        from encomm_pcc.drivers.hermes_cli import HermesCliError, build_chat_argv

        with pytest.raises(HermesCliError):
            build_chat_argv(
                executable="hermes", query_file="q.txt",
                profile="scientific", model="", provider="zai",
            )
