"""Shared pytest fixtures.

Qt runs in **offscreen** mode: the platform plugin is selected before any
``QApplication`` is created, so the UI smoke test never opens a window.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Qt must be told to use the offscreen platform before QtWidgets is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402

from encomm_pcc.core import EventLog, PipelineController  # noqa: E402
from encomm_pcc.persistence import Database  # noqa: E402


@pytest.fixture()
def database() -> Database:
    """An isolated in-memory database per test."""
    db = Database(":memory:").open()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def event_log(database: Database) -> EventLog:
    return EventLog(database)


@pytest.fixture()
def controller(database: Database, event_log: EventLog) -> PipelineController:
    return PipelineController(database=database, event_log=event_log)


@pytest.fixture(scope="session")
def qapp():
    """A single offscreen QApplication for the whole test session."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
