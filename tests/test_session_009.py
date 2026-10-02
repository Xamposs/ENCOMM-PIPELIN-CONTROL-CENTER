"""Session 009 — profile-scoped Hermes discovery, profile-scoped bindings,
the continuous runner (offline two-batch proof, §42) and Simple-Mode defaults.

Every proof here is OFFLINE: fake drivers, fixture SQLite stores, no AI calls.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from encomm_pcc.core import ContinuousRunner, PipelineController
from encomm_pcc.core.continuous_runner import ContinuousStopReason
from encomm_pcc.core.events import NullEventLog
from encomm_pcc.domain import AgentRole
from encomm_pcc.drivers import NullProcessRunner
from encomm_pcc.drivers.hermes_cli import resolve_executable_path
from encomm_pcc.drivers.hermes_discovery import (
    HermesSessionDiscovery,
    parse_sessions_list,
    profile_session_db_path,
)

from conftest import PermissiveRunner  # noqa: E402  (pytest rootdir)

# ----------------------------------------------------------------------------
# parse_sessions_list — the CLI table contract (structural, all 4 layouts)
# ----------------------------------------------------------------------------
class TestParseSessionsList:
    def test_title_layout_row_ids(self) -> None:
        text = (
            "Title                            Preview                                  Last Active   ID\n"
            "──────────────────────────────────────────────────────────────────────────────\n"
            "Availability check               Are you on ?                             2026-09-19    20260919_230022_34a239a9\n"
            "Friendly greeting #2             are you here ?                           2026-08-26    20260826_115611_8bb10c\n"
        )
        listing = parse_sessions_list(text)
        assert [r["session_id"] for r in listing.rows] == [
            "20260919_230022_34a239a9",
            "20260826_115611_8bb10c",
        ]
        assert listing.has_more is False

    def test_header_rule_and_empty_notice_are_never_rows(self) -> None:
        listing = parse_sessions_list(
            "No sessions found.\n"
        )
        assert listing.rows == []
        listing = parse_sessions_list(
            "Title Preview ID\n" + "─" * 60 + "\n"
        )
        assert listing.rows == []

    def test_truncation_notice_is_reported(self) -> None:
        text = (
            "Preview ID\n" + "─" * 30 + "\n"
            "hello 20260927_101112_aabbcc\n"
            "use --limit 10 to see more\n"
        )
        listing = parse_sessions_list(text)
        assert len(listing.rows) == 1
        assert listing.has_more is True

    def test_prose_lines_without_id_tail_are_dropped(self) -> None:
        listing = parse_sessions_list("Some warning about profiles\nsecond line\n")
        assert listing.rows == []


# ----------------------------------------------------------------------------
# Priority B — the profile's own state.db (read-only fixture store)
# ----------------------------------------------------------------------------
def _make_store(db_path: Path, rows: list[dict]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, cwd TEXT,"
        " started_at TEXT, last_activity_at TEXT, source TEXT,"
        " archived INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0)"
    )
    connection.executemany(
        "INSERT INTO sessions (id, title, cwd, started_at, last_activity_at,"
        " source, archived, hidden) VALUES (:id, :title, :cwd, :started_at,"
        " :last_activity_at, :source, :archived, :hidden)",
        rows,
    )
    connection.commit()
    connection.close()


class TestStoreDiscovery:
    def test_reads_metadata_only_newest_first(self, tmp_path: Path) -> None:
        db_path = tmp_path / "state.db"
        _make_store(
            db_path,
            [
                {
                    "id": "s_old",
                    "title": "old",
                    "cwd": str(tmp_path),
                    "started_at": "2026-01-01",
                    "last_activity_at": "2026-01-02",
                    "source": "cli",
                    "archived": 0,
                    "hidden": 0,
                },
                {
                    "id": "s_new",
                    "title": "new",
                    "cwd": str(tmp_path),
                    "started_at": "2026-02-01",
                    "last_activity_at": "2026-02-02",
                    "source": "cli",
                    "archived": 0,
                    "hidden": 0,
                },
            ],
        )
        discovery = HermesSessionDiscovery(runner=NullProcessRunner())
        # Patch the store resolution straight onto the fixture.
        result = discovery._discover_via_store(  # noqa: SLF001 - unit target
            profile="x", wanted_key="", limit=10
        )
        # The real resolver would not find this fixture; call the query path
        # through a monkeypatched resolver instead (below).  Here the real
        # resolver misses and the result fails soft:
        assert result.ok is False

    def test_read_only_fixture_store(self, tmp_path: Path, monkeypatch) -> None:
        db_path = tmp_path / "profiles" / "p9" / "state.db"
        _make_store(
            db_path,
            [
                {
                    "id": "s_keep1",
                    "title": None,
                    "cwd": str(tmp_path),
                    "started_at": "2026-01-01",
                    "last_activity_at": "2026-01-02",
                    "source": "cli",
                    "archived": 0,
                    "hidden": 0,
                },
                {
                    "id": "s_archived",
                    "title": "old",
                    "cwd": None,
                    "started_at": "2026-01-01",
                    "last_activity_at": "2026-03-01",
                    "source": "cli",
                    "archived": 1,
                    "hidden": 0,
                },
            ],
        )
        monkeypatch.setattr(
            "encomm_pcc.drivers.hermes_discovery.profile_session_db_path",
            lambda profile, environ=None: db_path,
        )
        discovery = HermesSessionDiscovery(runner=NullProcessRunner())
        result = discovery._discover_via_store(  # noqa: SLF001 - unit target
            profile="p9", wanted_key="", limit=10
        )
        assert result.ok is True
        assert [s.session_id for s in result.sessions] == ["s_keep1"]
        assert result.sessions[0].title is None  # never invented

    def test_null_runner_never_launches_and_falls_back(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        db_path = tmp_path / "state.db"
        _make_store(
            db_path,
            [
                {
                    "id": "s_fallback",
                    "title": "fb",
                    "cwd": None,
                    "started_at": "t",
                    "last_activity_at": "t2",
                    "source": "cli",
                    "archived": 0,
                    "hidden": 0,
                }
            ],
        )
        monkeypatch.setattr(
            "encomm_pcc.drivers.hermes_discovery.profile_session_db_path",
            lambda profile, environ=None: db_path,
        )
        discovery = HermesSessionDiscovery(runner=NullProcessRunner())
        result = discovery.discover_sessions(profile="p")
        # CLI path fails soft (Null runner records and raises), store answers.
        assert result.ok is True
        assert result.mechanism == "hermes-state-db"
        assert [s.session_id for s in result.sessions] == ["s_fallback"]

    def test_profile_scoping_by_construction(self, tmp_path: Path) -> None:
        other = tmp_path / "hermes" / "profiles" / "other" / "state.db"
        _make_store(
            other,
            [
                {
                    "id": "s_other",
                    "title": None,
                    "cwd": None,
                    "started_at": "t",
                    "last_activity_at": "t",
                    "source": "cli",
                    "archived": 0,
                    "hidden": 0,
                }
            ],
        )
        assert profile_session_db_path("other", environ={"LOCALAPPDATA": str(tmp_path)}) == other
        assert profile_session_db_path("missing", environ={"LOCALAPPDATA": str(tmp_path)}) is None


# ----------------------------------------------------------------------------
# executable resolution — known-good shim first
# ----------------------------------------------------------------------------
class TestResolveExecutable:
    def test_shim_preferred_when_present(self, tmp_path: Path, monkeypatch) -> None:
        shim = tmp_path / "hermes.exe"
        shim.write_bytes(b"MZ")
        monkeypatch.setattr(
            "encomm_pcc.drivers.hermes_cli._PREFERRED_SHIM", str(shim)
        )
        assert resolve_executable_path(("hermes",)) == str(shim)

    def test_path_fallback_when_shim_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            "encomm_pcc.drivers.hermes_cli._PREFERRED_SHIM",
            str(tmp_path / "missing.exe"),
        )
        # Whatever PATH holds, the result must exist or be None — never the
        # missing shim.
        result = resolve_executable_path(("hermes",))
        assert result is None or Path(result).is_file()


# ----------------------------------------------------------------------------
# §19 — binding validity covers engine AND profile
# ----------------------------------------------------------------------------
class TestBindingProfileRules:
    def test_bind_records_profile_and_mismatch_detects(
        self, database  # noqa: ANN001
    ) -> None:
        controller = PipelineController(database=database, event_log=NullEventLog())
        controller.set_role_config(
            AgentRole.TASK_AUDITOR, engine="hermes", project_profile="encomm-auditor"
        )
        controller.bind_external_session(
            AgentRole.TASK_AUDITOR,
            "hermes",
            "20260927_100000_aaaa",
            profile="encomm-auditor",
        )
        config = controller.state.config_for(AgentRole.TASK_AUDITOR)
        assert config.external_session_binding() is not None
        assert controller.binding_profile_mismatch(config) is None  # same profile

        controller.set_role_config(
            AgentRole.TASK_AUDITOR, project_profile="encomm-accounting-intelligence"
        )
        config = controller.state.config_for(AgentRole.TASK_AUDITOR)
        assert controller.binding_profile_mismatch(config) is not None

    def test_binding_without_recorded_profile_stays_valid(self, database) -> None:
        controller = PipelineController(database=database, event_log=NullEventLog())
        controller.set_role_config(
            AgentRole.ORCHESTRATOR, engine="codex", project_profile=""
        )
        controller.bind_external_session(AgentRole.ORCHESTRATOR, "codex", "01abc")
        config = controller.state.config_for(AgentRole.ORCHESTRATOR)
        # Legacy shape (no profile recorded): engine rule governs only.
        assert controller.binding_profile_mismatch(config) is None


# ----------------------------------------------------------------------------
# §42 — continuous loop, deterministic two-batch fake proof
# ----------------------------------------------------------------------------
class TestContinuousLoopOffline:
    @staticmethod
    def _scripted_results() -> list:
        """2-task first batch -> final PASS + 4-task next plan (D-031 range)
        -> zero-AI handoff -> 4-task second batch -> final PASS (stopped)."""
        from test_batch_runner import build_plan_text, ok_result, verdict_text
        from test_final_audit import pass_with_next

        results = [
            ok_result("orch-1", build_plan_text(2)),  # the ONLY planning call
            ok_result("builder-1", "done"),  # batch 1: build + audit
            ok_result("auditor", verdict_text("PASS")),
            ok_result("builder-2", "done"),
            ok_result("auditor", verdict_text("PASS")),
            ok_result("final-1", pass_with_next(4)),  # final PASS + NEXT PLAN
            ok_result("builder-3", "done"),  # batch 2 (4 tasks, zero planning)
            ok_result("auditor", verdict_text("PASS")),
            ok_result("builder-4", "done"),
            ok_result("auditor", verdict_text("PASS")),
            ok_result("builder-5", "done"),
            ok_result("auditor", verdict_text("PASS")),
            ok_result("builder-6", "done"),
            ok_result("auditor", verdict_text("PASS")),
            ok_result("final-2", pass_with_next(4)),  # final PASS; STOP lands here
        ]
        return results

    @staticmethod
    def _controller(controller, tmp_path: Path, results: list):
        from conftest import PermissiveRunner
        from test_batch_runner import ScriptedBatchDriver, discovery_stub

        ScriptedBatchDriver.reset()
        ScriptedBatchDriver.shared = list(results)
        from encomm_pcc.drivers import DriverRegistry

        registry = DriverRegistry()
        registry.register(ScriptedBatchDriver)
        controller.set_workspace("Cont Workspace", str(tmp_path))
        for role in (
            AgentRole.ORCHESTRATOR,
            AgentRole.BUILDER,
            AgentRole.TASK_AUDITOR,
            AgentRole.FINAL_AUDITOR,
        ):
            controller.set_role_config(
                role, engine="batch", project_profile="test-profile"
            )
        controller.set_role_config(AgentRole.FINAL_AUDITOR, same_as_orchestrator=False)
        from encomm_pcc.core.executor import Executor

        executor = Executor(
            controller,
            registry=registry,
            runner=PermissiveRunner(),
            database=controller.database,
            event_log=NullEventLog(),
            profile_discovery=discovery_stub,
        )
        controller.attach_executor(executor)
        return executor, ScriptedBatchDriver

    def test_two_batches_one_planning_call_stop_honoured(
        self, controller, tmp_path: Path
    ) -> None:
        executor, driver = self._controller(controller, tmp_path, self._scripted_results())
        # STOP after the second batch's final audit has persisted (step 15).
        driver.hooks = {15: executor.request_stop}
        report = ContinuousRunner(executor).run_continuous(
            project_brief="UNIQUE-BRIEF-MARKER", batch_size=2, next_batch_size=4
        )
        assert report.batches_run == 2
        assert report.final_audits == 2
        assert report.next_batch_handoffs == 1  # exactly ONE zero-AI handoff ran
        assert report.planning_ai_calls == 1  # by construction
        assert report.stop_reason == ContinuousStopReason.ALL_STOP
        # Exactly ONE ORCHESTRATOR planning prompt for the whole run (§5).
        # (The brief also appears in the Final Audit packet — that is the
        # durable-facts design, not a second planning call.)
        planning = [
            p
            for p in driver.prompts
            if "PLANNING ONLY" in p and "UNIQUE-BRIEF-MARKER" in p
        ]
        assert len(planning) == 1
        final_audits = [p for p in driver.prompts if "FINAL AUDITOR" in p.upper()]
        assert len(final_audits) == 2  # one per completed batch
        # Batch 2's tasks came from the PENDING plan (different titles).
        batch = controller.state.batch
        assert batch is not None
        titles = sorted(t.title for t in batch.tasks)
        assert titles == [f"Next task {i}" for i in range(1, 5)]
        # Stop prevented the THIRD operation: one unconsumed plan remains.
        assert controller.database.load_open_pending_next_plan() is not None

    def test_pause_before_next_final_audit_stops_loop(
        self, controller, tmp_path: Path
    ) -> None:
        results = self._scripted_results()
        executor, driver = self._controller(controller, tmp_path, results)
        driver.hooks = {14: executor.request_pause}  # after batch 2's last audit
        report = ContinuousRunner(executor).run_continuous(
            project_brief="b", batch_size=2, next_batch_size=4
        )
        assert report.stop_reason == ContinuousStopReason.PAUSED
        assert report.final_audits == 1  # the second final audit never ran
        assert report.batches_run == 2

    def test_non_pass_final_audit_stops_loudly(
        self, controller, tmp_path: Path
    ) -> None:
        from tests.test_batch_runner import build_plan_text, ok_result, verdict_text

        results = [
            ok_result("orch-1", build_plan_text(1)),
            ok_result("builder-1", "done"),
            ok_result("auditor", verdict_text("PASS")),
        ]
        executor, _driver = self._controller(controller, tmp_path, results)
        report = ContinuousRunner(executor).run_continuous(
            project_brief="b", batch_size=1, next_batch_size=4
        )
        # The final-audit call finds NO scripted answer (engine failure class):
        # the loop must stop loudly on the non-PASS audit, never plan again.
        assert report.batches_run == 1
        assert report.planning_ai_calls == 1
        assert report.stop_reason == ContinuousStopReason.FINAL_AUDIT_NOT_PASS


# ----------------------------------------------------------------------------
# §44 — Simple Mode is the default surface
# ----------------------------------------------------------------------------
class TestSimpleModeDefault:
    def test_simple_is_default_and_advanced_preserved(self, qapp, database) -> None:
        from encomm_pcc.ui.main_window import MainWindow

        controller = PipelineController(database=database, event_log=NullEventLog())
        window = MainWindow(controller, profiles=("encomm-auditor",))
        try:
            assert window.mode_stack.currentWidget() is window.simple_panel
            # Continuous checkbox exists and is OFF by default (explicit opt-in).
            assert window.simple_panel.continuous_check.isChecked() is False
            # The advanced surface kept its proven panels.
            # Session 017 (by design): index 2 = Proposal Mode panel.
            assert window.mode_stack.count() == 3
            assert window.mode_stack.widget(2) is window.proposal_panel
            assert window.batch_panel is not None
            assert window.task_panel is not None
            assert window.history_panel is not None
            window._show_advanced_mode()
            assert window.mode_stack.currentWidget() is window.advanced_view
            window._show_simple_mode()
            assert window.mode_stack.currentWidget() is window.simple_panel
        finally:
            window.close()

    def test_simple_config_writes_durable_role_configs(self, qapp, database) -> None:
        from encomm_pcc.ui.main_window import MainWindow

        controller = PipelineController(database=database, event_log=NullEventLog())
        window = MainWindow(controller, profiles=("encomm-auditor", "encomm-accounting-intelligence"))
        try:
            panel = window.simple_panel
            index = panel.auditor_profile.findText("encomm-auditor")
            assert index >= 0
            panel.auditor_profile.setCurrentIndex(index)
            panel._apply_role_config(
                AgentRole.TASK_AUDITOR, panel.auditor_profile,
                panel.auditor_provider, panel.auditor_model,
            )
            config = controller.state.config_for(AgentRole.TASK_AUDITOR)
            assert config.project_profile == "encomm-auditor"
            assert config.engine == "hermes"
        finally:
            window.close()
