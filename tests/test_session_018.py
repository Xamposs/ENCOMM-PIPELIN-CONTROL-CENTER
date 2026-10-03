"""Session 018 — GLOBAL product mode tabs (final UI polish).

Brief §7 test matrix, offline + offscreen, zero AI calls:

* ONE persistent top-level ``QTabBar`` product selector with EXACTLY TWO
  production tabs — ``CODING MODE`` (mode_stack index 0) and
  ``PROPOSAL MODE`` (mode_stack index 2).
* Advanced / Details stays mode_stack index 1, remains a ``--debug-ui``
  development surface, and deliberately has NO product tab.
* Internal navigation (``_show_simple_mode`` / ``_show_proposal_mode`` /
  Proposal BACK) and the global tabs are ONE navigation state — never
  conflicting.
* Navigation never alters Coding role configuration or Proposal agent
  configuration, and startup (plus every navigation edge) makes ZERO
  model calls.

Brief items 14/15 (existing Session 017/017A UI tests and all Coding Mode
tests remain green) are proven by the full-suite run, not by a single
test here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QTabBar  # noqa: E402

from encomm_pcc.core import PipelineController  # noqa: E402
from encomm_pcc.core.events import NullEventLog  # noqa: E402
from encomm_pcc.ui.main_window import MainWindow  # noqa: E402

from conftest import FakeProcessRunner, qapp  # noqa: F401,E402

CODING_TAB = 0
PROPOSAL_TAB = 1


def make_window(qapp) -> MainWindow:
    controller = PipelineController(database=None, event_log=NullEventLog())
    return MainWindow(controller)


class TestGlobalModeTabs:
    def test_1_startup_selects_coding_mode_with_stack_index_0(self, qapp):
        window = make_window(qapp)
        assert window.mode_tabs.currentIndex() == CODING_TAB
        assert window.mode_tabs.tabText(CODING_TAB) == "CODING MODE"
        assert window.mode_stack.currentIndex() == 0
        assert window.mode_stack.currentWidget() is window.simple_panel

    def test_2_coding_tab_maps_to_stack_0(self, qapp):
        window = make_window(qapp)
        window.mode_tabs.setCurrentIndex(PROPOSAL_TAB)  # move away first
        assert window.mode_stack.currentIndex() == 2
        window.mode_tabs.setCurrentIndex(CODING_TAB)
        assert window.mode_stack.currentIndex() == 0
        assert window.mode_stack.currentWidget() is window.simple_panel

    def test_3_proposal_tab_maps_to_stack_2(self, qapp):
        window = make_window(qapp)
        window.mode_tabs.setCurrentIndex(PROPOSAL_TAB)
        assert window.mode_stack.currentIndex() == 2
        assert window.mode_stack.currentWidget() is window.proposal_panel

    def test_4_stack_index_1_remains_advanced_details(self, qapp):
        window = make_window(qapp)
        assert window.mode_stack.widget(1) is window.advanced_view
        window._show_advanced_mode()
        assert window.mode_stack.currentWidget() is window.advanced_view
        assert window.mode_stack.count() == 3
        # The debug surface has no product tab: the selector keeps the
        # current product selection (startup: CODING MODE).
        assert window.mode_tabs.currentIndex() == CODING_TAB

    def test_5_exactly_two_production_product_tabs(self, qapp):
        window = make_window(qapp)
        assert isinstance(window.mode_tabs, QTabBar)
        assert window.mode_tabs.count() == 2

    def test_6_advanced_does_not_appear_in_product_tabs(self, qapp):
        window = make_window(qapp)
        texts = [window.mode_tabs.tabText(i) for i in range(window.mode_tabs.count())]
        assert texts == ["CODING MODE", "PROPOSAL MODE"]
        assert not any("ADVANCED" in text.upper() for text in texts)

    def test_7_show_proposal_mode_synchronizes_the_product_tab(self, qapp):
        window = make_window(qapp)
        window._show_proposal_mode()
        assert window.mode_stack.currentIndex() == 2
        assert window.mode_tabs.currentIndex() == PROPOSAL_TAB

    def test_8_show_simple_mode_synchronizes_the_product_tab(self, qapp):
        window = make_window(qapp)
        window._show_proposal_mode()
        window._show_simple_mode()
        assert window.mode_stack.currentIndex() == 0
        assert window.mode_tabs.currentIndex() == CODING_TAB

    def test_9_proposal_back_to_coding_synchronizes_both(self, qapp):
        window = make_window(qapp)
        window._show_proposal_mode()
        assert window.mode_tabs.currentIndex() == PROPOSAL_TAB
        window.proposal_panel._on_back()
        assert window.mode_stack.currentIndex() == 0
        assert window.mode_tabs.currentIndex() == CODING_TAB

    def test_10_simple_mode_proposal_button_synchronizes_both(self, qapp):
        window = make_window(qapp)
        window.simple_panel._on_proposal()
        assert window.mode_stack.currentIndex() == 2
        assert window.mode_tabs.currentIndex() == PROPOSAL_TAB
        window.proposal_panel._on_back()
        assert window.mode_stack.currentIndex() == 0
        assert window.mode_tabs.currentIndex() == CODING_TAB

    # -- configuration neutrality ------------------------------------------
    def _coding_config_snapshot(self, window: MainWindow) -> dict:
        return {
            role.value: window.controller.state.config_for(role).to_dict()
            if hasattr(window.controller.state.config_for(role), "to_dict")
            else vars(window.controller.state.config_for(role)).copy()
            for role in window.controller.state.role_configs
        }

    def test_11_navigation_does_not_alter_coding_role_configuration(self, qapp):
        window = make_window(qapp)
        before = self._coding_config_snapshot(window)
        window._show_proposal_mode()
        window.proposal_panel._on_back()
        window.mode_tabs.setCurrentIndex(PROPOSAL_TAB)
        window.mode_tabs.setCurrentIndex(CODING_TAB)
        assert self._coding_config_snapshot(window) == before

    def test_12_navigation_does_not_alter_proposal_agent_configuration(self, qapp):
        window = make_window(qapp)
        before = window.proposal_panel.role_configs()
        window.simple_panel._on_proposal()
        window.proposal_panel._on_back()
        window.mode_tabs.setCurrentIndex(PROPOSAL_TAB)
        window.mode_tabs.setCurrentIndex(CODING_TAB)
        assert window.proposal_panel.role_configs() == before

    def test_13_startup_and_all_navigation_edges_make_zero_model_calls(
        self, qapp
    ):
        from encomm_pcc.app import attach_default_executor

        controller = PipelineController(database=None, event_log=NullEventLog())
        runner = FakeProcessRunner()
        attach_default_executor(controller, runner=runner)
        window = MainWindow(controller)
        # Construction plus EVERY navigation edge: zero process spawns.
        window._show_proposal_mode()
        window.proposal_panel._on_back()
        window.mode_tabs.setCurrentIndex(PROPOSAL_TAB)
        window.mode_tabs.setCurrentIndex(CODING_TAB)
        window._show_advanced_mode()
        window._show_simple_mode()
        assert runner.call_count == 0
