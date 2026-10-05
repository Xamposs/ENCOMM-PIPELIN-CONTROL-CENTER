"""Session 022 — Proposal Mode agent-selector / provider-model discovery.

Covers the brief's test matrix:
* HERMES   — catalog source, dedup, provider→models mapping, signal wiring,
             defaults, editable combos, zero model calls, fail-soft.
* CODEX    — profile/provider N/A + persisted empty, editable model,
             reasoning enabled, real session discovery intact.
* REASONING— N/A on Hermes, never persisted for non-Codex engines.
* OTHER    — capability-gated controls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_session_020 import make_window, seed_workspace

from encomm_pcc.drivers.base import BaseDriver, DriverCapabilities
from encomm_pcc.drivers.process import ProcessResult, ProcessSpec
from encomm_pcc.drivers.session_discovery import (
    ExternalSessionDescriptor,
    SessionDiscoveryResult,
)
from encomm_pcc.proposal.enums import ProposalRole
from encomm_pcc.proposal_runtime.hermes_model_catalog import (
    HERMES_CATALOG_SOURCE_INVENTORY,
    HERMES_CATALOG_SOURCE_NONE,
    HermesModelCatalog,
    HermesProviderEntry,
    discover_hermes_model_catalog,
)
from encomm_pcc.ui.proposal_mode import (
    _CODEX_MODEL_PLACEHOLDER,
    ProposalModePanel,
)

# ---------------------------------------------------------------------------
# test doubles
# ---------------------------------------------------------------------------

_CATALOG_JSON = {
    "profile_default_provider": "zai",
    "profile_default_model": "glm-5.3-flash",
    "providers": [
        {"slug": "openrouter", "name": "OpenRouter", "models": ["or/a", "or/b"],
         "total_models": 2, "source": "built-in", "is_current": False,
         "is_user_defined": False},
        {"slug": "zai", "name": "Z.AI / GLM",
         "models": ["glm-5.3-flash", "glm-5.3", "glm-5.2"],
         "total_models": 3, "source": "built-in", "is_current": True,
         "is_user_defined": False},
        {"slug": "deepseek", "name": "DeepSeek",
         "models": ["deepseek-flash", "deepseek-v4-pro"],
         "total_models": 2, "source": "built-in", "is_current": False,
         "is_user_defined": False},
        # deliberate duplicate: first occurrence wins
        {"slug": "zai", "name": "Z.AI / GLM", "models": ["glm-4.5"],
         "total_models": 1, "source": "built-in",
         "is_current": False, "is_user_defined": False},
    ],
}


class _CatalogScriptRunner:
    """Real SubprocessRunner shape: returns a fixture payload, records specs."""

    def __init__(self, payload: str):
        self._payload = payload
        self.specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.specs.append(spec)
        return ProcessResult(
            argv=spec.argv_list(), exit_code=0, stdout=self._payload
        )


def _catalog_runner() -> _CatalogScriptRunner:
    return _CatalogScriptRunner(json.dumps(_CATALOG_JSON))


class _TimeoutRunner:
    def run(self, spec: ProcessSpec) -> ProcessResult:
        return ProcessResult(
            argv=spec.argv_list(), exit_code=1, timed_out=True
        )


class _ExitCodeRunner:
    def __init__(self, code: int):
        self._code = code

    def run(self, spec: ProcessSpec) -> ProcessResult:
        return ProcessResult(argv=spec.argv_list(), exit_code=self._code)


class _ScriptedDriver(BaseDriver):
    """Capability-fixture driver for the §5 gating matrix."""

    driver_id = "scripted-s022"

    def __init__(self, runner=None):
        super().__init__(runner)

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name="Scripted S022",
            supports_sessions=False,
            supports_resume=False,
            supports_model_selection=False,
            requires_profile=False,
            implemented=True,
        )

    @classmethod
    def probe_availability(cls) -> bool:
        return False

    @classmethod
    def describe(cls):
        return {"driver": cls.driver_id, "display_name": "Scripted S022"}

    def start_session(self, request):
        raise NotImplementedError("test double")

    def send_prompt(self, session, prompt):
        raise NotImplementedError("test double")

    def close_session(self, session=None) -> None:
        raise NotImplementedError("test double")


# ---------------------------------------------------------------------------
# HERMES catalog unit tests (no Qt)
# ---------------------------------------------------------------------------


class TestHermesModelCatalogUnit:
    @pytest.fixture()
    def fake_hermes(self, tmp_path):
        """A fake Hermes install + profile home, fully decoupled from the host.

        ``environ`` carries HERMES_AGENT_DIR (fake venv python) and
        HERMES_HOME (``<tmp>/profiles/scientific`` with a config.yaml), so
        ``profile_roots`` resolves the fake home FIRST (env root wins) and
        the injected runner means the child never really executes.
        """
        agent = tmp_path / "hermes-agent"
        venv_dir = agent / "venv" / "Scripts"
        venv_dir.mkdir(parents=True)
        python = venv_dir / "python.exe"
        python.write_text("", encoding="utf-8")
        home = tmp_path / "profiles" / "scientific"
        home.mkdir(parents=True)
        (home / "config.yaml").write_text(
            "model:\n  provider: zai\n  default: glm-5.3-flash\n",
            encoding="utf-8",
        )
        return {
            "HERMES_AGENT_DIR": str(agent),
            "HERMES_HOME": str(home),
        }

    def test_catalog_parses_providers_and_models(self, fake_hermes):
        runner = _catalog_runner()
        cat = discover_hermes_model_catalog(
            "scientific", environ=fake_hermes, runner=runner
        )
        assert cat.available
        assert cat.source == HERMES_CATALOG_SOURCE_INVENTORY
        assert cat.provider_names() == ["openrouter", "zai", "deepseek"]
        assert cat.profile_default_provider == "zai"
        assert cat.profile_default_model == "glm-5.3-flash"
        assert cat.models_for_provider("zai") == [
            "glm-5.3-flash", "glm-5.3", "glm-5.2",
        ]
        assert cat.models_for_provider("deepseek") == [
            "deepseek-flash", "deepseek-v4-pro",
        ]
        assert cat.exhaustive is True

    def test_provider_dedup_is_deterministic(self, fake_hermes):
        cat = discover_hermes_model_catalog(
            "scientific", environ=fake_hermes, runner=_catalog_runner()
        )
        assert cat.provider_names().count("zai") == 1
        assert cat.models_for_provider("zai")[0] == "glm-5.3-flash"

    def test_provider_a_gives_models_a_provider_b_gives_models_b(
        self, fake_hermes
    ):
        cat = discover_hermes_model_catalog(
            "scientific", environ=fake_hermes, runner=_catalog_runner()
        )
        assert "glm-5.3-flash" in cat.models_for_provider("zai")
        assert "deepseek-flash" in cat.models_for_provider("deepseek")
        assert "glm-5.3-flash" not in cat.models_for_provider("deepseek")
        assert "deepseek-flash" not in cat.models_for_provider("zai")
        assert cat.models_for_provider("nonexistent") == []

    def test_child_env_scopes_profile_and_bootstrap_argv(self, fake_hermes):
        runner = _catalog_runner()
        discover_hermes_model_catalog(
            "scientific", environ=fake_hermes, runner=runner
        )
        assert len(runner.specs) == 1
        spec = runner.specs[0]
        assert spec.env["HERMES_PROFILE"] == "scientific"
        assert spec.env["HERMES_HOME"].replace("\\", "/").endswith(
            "profiles/scientific"
        )
        argv = spec.argv_list()
        assert argv[0].endswith("python.exe")
        assert argv[1] == "-I"
        assert argv[2] == "-c"
        assert len(argv) == 4  # python, -I, -c, bootstrap — nothing else runs

    def test_unreadable_discovery_fails_soft(self, fake_hermes):
        for payload in (
            "",
            "prose, not json",
            json.dumps({"error": "hermes_inventory_failed: X"}),
            json.dumps({"providers": "not-a-list"}),
            json.dumps({"providers": []}),
            json.dumps([1, 2, 3]),
        ):
            cat = discover_hermes_model_catalog(
                "scientific",
                environ=fake_hermes,
                runner=_CatalogScriptRunner(payload),
            )
            assert not cat.available
            assert cat.source == HERMES_CATALOG_SOURCE_NONE
            assert cat.error
            assert cat.providers == []

    def test_timeout_and_exit_failures_fail_soft(self, fake_hermes):
        cat = discover_hermes_model_catalog(
            "scientific", environ=fake_hermes, runner=_TimeoutRunner()
        )
        assert not cat.available and "timed out" in cat.error
        cat2 = discover_hermes_model_catalog(
            "scientific", environ=fake_hermes, runner=_ExitCodeRunner(3)
        )
        assert not cat2.available and "exited 3" in cat2.error

    def test_missing_profile_fails_fast_without_runner(self, fake_hermes):
        cat = discover_hermes_model_catalog(
            "", environ=fake_hermes, runner=_catalog_runner()
        )
        assert not cat.available and "no profile" in cat.error

    def test_unknown_profile_home_fails_soft(self, fake_hermes):
        cat = discover_hermes_model_catalog(
            "no-such-profile", environ=fake_hermes, runner=_catalog_runner()
        )
        assert not cat.available
        assert "profile home not found" in cat.error


# ---------------------------------------------------------------------------
# HERMES UI behavior
# ---------------------------------------------------------------------------

_ENTRIES = [
    HermesProviderEntry(slug="openrouter", name="OpenRouter",
                        models=["or/a", "or/b"], total_models=2,
                        source="built-in"),
    HermesProviderEntry(slug="zai", name="Z.AI / GLM",
                        models=["glm-5.3-flash", "glm-5.3"],
                        total_models=2, source="built-in"),
    HermesProviderEntry(slug="deepseek", name="DeepSeek",
                        models=["deepseek-flash", "deepseek-v4-pro"],
                        total_models=2, source="built-in"),
]


@pytest.fixture()
def catalog_panel(qapp, monkeypatch):
    """A panel whose Hermes catalog discovery is scripted (deterministic)."""

    def fake_catalog(profile, **kwargs):
        return HermesModelCatalog(
            profile=profile,
            providers=_ENTRIES,
            models_by_provider={e.slug: list(e.models) for e in _ENTRIES},
            profile_default_provider="zai",
            profile_default_model="glm-5.3-flash",
            source=HERMES_CATALOG_SOURCE_INVENTORY,
            exhaustive=True,
        )

    window = make_window(qapp)
    panel = window.proposal_panel
    monkeypatch.setattr(
        "encomm_pcc.ui.proposal_mode.discover_hermes_model_catalog",
        fake_catalog,
    )
    return panel


def _select_hermes(panel: ProposalModePanel, role: ProposalRole) -> None:
    row = panel._role_rows[role]
    row["engine"].setCurrentIndex(row["engine"].findData("hermes"))
    panel._on_refresh_hermes_selectors(role)


class TestHermesSelectorUI:
    def test_refresh_populates_full_catalog_with_defaults(self, catalog_panel):
        panel = catalog_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        _select_hermes(panel, role)
        provider = row["provider"]
        assert [provider.itemText(i) for i in range(provider.count())] == [
            "openrouter", "zai", "deepseek",
        ]
        assert provider.currentText() == "zai"  # profile default selected
        model = row["model"]
        assert [model.itemText(i) for i in range(model.count())] == [
            "glm-5.3-flash", "glm-5.3",
        ]
        assert model.currentText() == "glm-5.3-flash"  # zai's default model
        assert "hermes-inventory" in panel._action_note

    def test_provider_change_repopulates_models_without_refresh(
        self, catalog_panel
    ):
        panel = catalog_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        _select_hermes(panel, role)
        provider = row["provider"]
        # The operator picks deepseek: models repopulate IMMEDIATELY with
        # deepseek's OWN models; an editable combo auto-selects its first
        # item, and that item now legitimately BELONGS to deepseek.
        provider.setCurrentIndex(provider.findText("deepseek"))
        panel._on_role_provider_changed(role)
        model = row["model"]
        assert [model.itemText(i) for i in range(model.count())] == [
            "deepseek-flash", "deepseek-v4-pro",
        ]
        assert model.currentText() == "deepseek-flash"

    def test_provider_change_never_keeps_an_unrelated_model(
        self, catalog_panel
    ):
        panel = catalog_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        _select_hermes(panel, role)
        provider = row["provider"]
        # zai's model under openrouter: dropped, never substituted. The
        # editable combo auto-selects openrouter's OWN first model instead.
        provider.setCurrentIndex(provider.findText("openrouter"))
        panel._on_role_provider_changed(role)
        assert row["model"].currentText() == "or/a"
        assert [row["model"].itemText(i) for i in range(row["model"].count())] == [
            "or/a", "or/b",
        ]
        assert "glm-5.3" not in [
            row["model"].itemText(i) for i in range(row["model"].count())
        ]

    def test_combos_remain_editable(self, catalog_panel):
        panel = catalog_panel
        for role in ProposalRole:
            row = panel._role_rows[role]
            assert row["provider"].isEditable()
            assert row["model"].isEditable()
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        _select_hermes(panel, role)
        row["provider"].setEditText("custom-endpoint")
        row["model"].setEditText("custom-model")
        panel._sync_role_config_from_widgets(role)
        config = panel.role_configs()[role]
        assert config.provider == "custom-endpoint"
        assert config.model == "custom-model"

    def test_unavailable_catalog_leaves_combos_empty_not_fabricated(
        self, qapp, monkeypatch
    ):
        window = make_window(qapp)
        panel = window.proposal_panel
        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_model_catalog",
            lambda profile, **kw: HermesModelCatalog(
                profile=profile, error="hermes venv python not found"
            ),
        )
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        _select_hermes(panel, role)
        assert row["provider"].count() == 0
        assert row["model"].count() == 0
        assert "unavailable" in panel._action_note

    def test_discovery_never_starts_a_worker_or_driver(self, catalog_panel):
        """REFRESH touches ONLY discovery — no AI worker thread is started."""
        panel = catalog_panel
        _select_hermes(panel, ProposalRole.SCIENTIFIC_REVIEWER)
        assert panel._thread is None

    def test_profile_change_refreshes_catalog_and_resets_sessions(
        self, catalog_panel, monkeypatch
    ):
        panel = catalog_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        _select_hermes(panel, role)
        row["session"].addItem("sess-a", "sess-a")
        row["session"].setCurrentIndex(row["session"].findData("sess-a"))
        row["session_mode"].setCurrentIndex(
            row["session_mode"].findData("RESUME_SELECTED_SESSION")
        )
        seen: list[str] = []

        def fake_sessions(r, profile):
            seen.append(profile)
            row["session"].clear()
            row["session"].addItem("NEW SESSION", "")

        monkeypatch.setattr(panel, "_populate_sessions", fake_sessions)
        row["profile"].setEditText("other-profile")
        panel._on_role_profile_changed(role)
        assert seen == ["other-profile"]
        assert row["session"].currentData() == ""
        assert panel.role_configs()[role].session_id == ""

    def test_config_persistence_round_trip(self, catalog_panel, tmp_path):
        panel = catalog_panel
        role = ProposalRole.ORCHESTRATOR
        row = panel._role_rows[role]
        _select_hermes(panel, role)
        row["profile"].setEditText("scientific")
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
        assert saved.project_profile == "scientific"
        assert saved.provider == "zai"
        assert saved.model == "glm-5.3"
        assert saved.reasoning_effort == ""


# ---------------------------------------------------------------------------
# CODEX + reasoning + capability gating
# ---------------------------------------------------------------------------


class TestCodexAndGating:
    def test_codex_profile_provider_na_and_disabled(self, qapp):
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("codex"))
        panel._on_role_engine_changed(role)
        assert not row["profile"].isEnabled()
        assert not row["provider"].isEnabled()
        assert row["profile"].currentText() == "N/A"
        assert row["provider"].currentText() == "N/A"
        assert row["model"].isEnabled()

    def test_codex_persists_profile_provider_empty_and_model(
        self, qapp, tmp_path
    ):
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.ORCHESTRATOR
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("codex"))
        panel._on_role_engine_changed(role)
        row["model"].setEditText("gpt-5.3-codex")
        panel._sync_role_config_from_widgets(role)
        config = panel.role_configs()[role]
        assert config.project_profile == ""
        assert config.provider == ""
        assert config.model == "gpt-5.3-codex"
        ws = tmp_path / "ws"
        seed_workspace(ws)
        panel.save_role_config(ws)
        panel2 = ProposalModePanel(panel.registry)
        assert panel2.load_role_config(ws) is True
        saved = panel2.role_configs()[role]
        assert saved.project_profile == ""
        assert saved.provider == ""
        assert saved.model == "gpt-5.3-codex"

    def test_codex_placeholder_is_display_only(self, qapp):
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.PROPOSAL_ENGINEER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("codex"))
        panel._on_role_engine_changed(role)
        assert row["model"].currentText() == _CODEX_MODEL_PLACEHOLDER
        panel._sync_role_config_from_widgets(role)
        assert panel.role_configs()[role].model == ""  # never persisted

    def test_codex_placeholder_never_overwrites_operator_model(self, qapp):
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.PROPOSAL_ENGINEER
        row = panel._role_rows[role]
        row["model"].setEditText("gpt-5.3-codex")
        row["engine"].setCurrentIndex(row["engine"].findData("codex"))
        panel._on_role_engine_changed(role)
        assert row["model"].currentText() == "gpt-5.3-codex"

    def test_codex_reasoning_enabled_hermes_na(self, qapp, monkeypatch):
        window = make_window(qapp)
        panel = window.proposal_panel
        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_model_catalog",
            lambda profile, **kw: HermesModelCatalog(
                profile=profile, error="unavailable"
            ),
        )
        monkeypatch.setattr(
            "encomm_pcc.ui.proposal_mode.discover_hermes_profile_names",
            lambda: type(
                "P", (), {"profiles": [], "source": "cli", "ok": True,
                          "error": ""}
            )(),
        )
        role = ProposalRole.RED_TEAM_REVIEWER
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("codex"))
        panel._on_role_engine_changed(role)
        assert row["reasoning"].isEnabled()
        assert row["reasoning"].itemText(0) == "DEFAULT"
        row["reasoning"].setCurrentIndex(row["reasoning"].findData("xhigh"))
        panel._sync_role_config_from_widgets(role)
        assert panel.role_configs()[role].reasoning_effort == "xhigh"
        # Switch to Hermes: reasoning becomes N/A and the effort is gone.
        _select_hermes(panel, role)
        assert not row["reasoning"].isEnabled()
        assert row["reasoning"].itemText(0) == "N/A"
        assert panel.role_configs()[role].reasoning_effort == ""

    def test_generic_engine_controls_follow_capabilities(self, qapp):
        window = make_window(qapp)
        panel = window.proposal_panel
        panel.registry.register(_ScriptedDriver)
        role = ProposalRole.SCIENTIFIC_REVIEWER
        row = panel._role_rows[role]
        engine = row["engine"]
        # The engine combo was built at construction; register-then-select
        # needs the new driver's item added the way a late registration
        # surfaces (append + select it).
        if engine.findData("scripted-s022") < 0:
            engine.addItem("scripted-s022", "scripted-s022")
        engine.setCurrentIndex(engine.findData("scripted-s022"))
        panel._on_role_engine_changed(role)
        # scripted: requires_profile=False, supports_model_selection=False
        assert not row["profile"].isEnabled()
        assert not row["model"].isEnabled()
        assert not row["provider"].isEnabled()  # provider is Hermes-only
        config = panel.role_configs()[role]
        assert config.project_profile == ""
        assert config.model == ""

    def test_codex_sessions_still_discover(self, qapp, monkeypatch):
        window = make_window(qapp)
        panel = window.proposal_panel

        class _FakeCodexDriver:
            def discover_sessions(self, workspace_path=None, limit=25):
                return SessionDiscoveryResult(
                    ok=True,
                    driver_id="codex",
                    sessions=[
                        ExternalSessionDescriptor(
                            session_id="codex-sess-1",
                            driver_id="codex",
                            title="t",
                            matches_workspace=True,
                        )
                    ],
                    mechanism="test",
                )

        monkeypatch.setattr(
            panel.registry, "create",
            lambda engine, runner=None: _FakeCodexDriver(),
        )
        role = ProposalRole.ORCHESTRATOR
        row = panel._role_rows[role]
        row["engine"].setCurrentIndex(row["engine"].findData("codex"))
        panel._on_role_engine_changed(role)
        labels = [
            row["session"].itemText(i) for i in range(row["session"].count())
        ]
        assert any("[WS]" in t for t in labels)

    def test_no_engine_shows_neutral_disabled_state(self, qapp):
        window = make_window(qapp)
        panel = window.proposal_panel
        role = ProposalRole.RED_TEAM_REVIEWER
        row = panel._role_rows[role]
        # No engine selected: nothing misleading, nothing enabled-but-dead.
        panel._apply_selector_gating(role)
        panel._sync_reasoning_availability(role)
        assert not row["provider"].isEnabled()
        assert not row["reasoning"].isEnabled()


# ---------------------------------------------------------------------------
# structural: the config-derived discovery is gone
# ---------------------------------------------------------------------------


def test_config_derived_provider_discovery_removed():
    src = Path(
        "src/encomm_pcc/proposal_runtime/hermes_selector_discovery.py"
    ).read_text(encoding="utf-8")
    assert "discover_hermes_provider_model" not in src
    assert "ProviderModelOptions" not in src
    assert "discover_hermes_model_catalog(" not in src
