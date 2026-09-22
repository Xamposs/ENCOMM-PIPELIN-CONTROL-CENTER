"""Shared pytest fixtures.

Qt runs in **offscreen** mode: the platform plugin is selected before any
``QApplication`` is created, so the UI smoke test never opens a window.

Nothing in this suite touches a network or a real engine: the fake process
runner and the fake driver below return canned results, and the real Hermes run
is a separate, explicitly invoked script (``scripts/session_002_smoke.py``).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

# Qt must be told to use the offscreen platform before QtWidgets is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402

from encomm_pcc.core import EventLog, PipelineController  # noqa: E402
from encomm_pcc.drivers import (  # noqa: E402
    BaseDriver,
    DriverCapabilities,
    DriverSession,
    ProcessResult,
    ProcessSpec,
    PromptHandle,
    PromptResult,
    SessionRequest,
)
from encomm_pcc.persistence import Database  # noqa: E402


@dataclass
class FakeProcessRunner:
    """Canned-results runner.  Records every spec, spawns nothing."""

    results: list[ProcessResult] = field(default_factory=list)
    specs: list[ProcessSpec] = field(default_factory=list)

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.specs.append(spec)
        if not self.results:
            raise AssertionError("FakeProcessRunner: no canned result was queued")
        canned = self.results.pop(0)
        return replace(canned, argv=spec.argv_list())

    @property
    def call_count(self) -> int:
        return len(self.specs)


def _stream_json_text(
    *,
    session_id: str = "20260922_150000_abcdef",
    text: str = "ENCOMM_PCC_HERMES_SMOKE_OK",
    exit_code: int = 0,
    model: str = "deepseek/deepseek-v4.1-flash",
    extra_records: list[dict] | None = None,
) -> str:
    """A realistic ``--format stream-json`` stdout payload."""
    records: list[dict] = [
        {"type": "system", "subtype": "init", "model": model, "session_id": session_id},
        {"type": "text", "text": text},
    ]
    records.extend(extra_records or [])
    records.append(
        {
            "type": "result",
            "session_id": session_id,
            "exit_code": exit_code,
            "text": text,
            "tokens": {"input": 10, "output": 5, "total": 15},
            "duration_ms": 1234,
        }
    )
    return "\n".join(json.dumps(r) for r in records) + "\n"


def _process_result(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    duration_s: float = 1.0,
    timed_out: bool = False,
) -> ProcessResult:
    return ProcessResult(
        argv=[],
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_s=duration_s,
        timed_out=timed_out,
    )


@dataclass
class PermissiveRunner:
    """Runner that records specs and always succeeds — for executors under test."""

    specs: list[ProcessSpec] = field(default_factory=list)

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.specs.append(spec)
        return ProcessResult(
            argv=spec.argv_list(), exit_code=0, stdout="", stderr="", duration_s=0.05
        )


class FakeDriver(BaseDriver):
    """A real-shaped driver with scripted results — no engine involved.

    ``wait_for_completion`` runs its prompt through the injected
    :class:`ProcessRunner` before returning the scripted result, so the executor's
    "a process really started" recorder is exercised exactly as with a real
    driver.
    """

    driver_id = "fake"
    display_name = "Fake Engine"
    executables = ("python",)

    scripted: list[PromptResult] = []
    sessions_started: int = 0
    #: Optional artificial latency, used to prove the UI thread stays responsive.
    delay_s: float = 0.0

    def __init__(self, runner=None) -> None:  # noqa: ANN001
        super().__init__(runner=runner)
        self.results = list(type(self).scripted)
        self.prompts: list[str] = []

    @classmethod
    def capabilities(cls) -> DriverCapabilities:
        return DriverCapabilities(
            driver_id=cls.driver_id,
            display_name=cls.display_name,
            supports_sessions=True,
            supports_resume=False,
            implemented=True,
            notes="Test double.",
        )

    def start_session(self, request: SessionRequest) -> DriverSession:
        type(self).sessions_started += 1
        return self._set_session(
            DriverSession(
                driver_id=self.driver_id,
                role=request.role,
                session_id=None,
                metadata={"profile": request.project_profile},
            )
        )

    def resume_session(self, session_id: str, request: SessionRequest) -> DriverSession:
        raise NotImplementedError

    def send_prompt(self, session: DriverSession, prompt: str) -> PromptHandle:
        self.prompts.append(prompt)
        return PromptHandle(session=session, prompt=prompt)

    def wait_for_completion(self, handle: PromptHandle, timeout_s: float | None = None):
        import time

        if type(self).delay_s:
            time.sleep(type(self).delay_s)
        self._runner.run(
            ProcessSpec(
                argv=["fake-engine", "--prompt-file", "prompt.txt"], timeout_s=timeout_s
            )
        )
        if not self.results:
            raise AssertionError("FakeDriver: no scripted PromptResult")
        result = self.results.pop(0)
        if result.session_id:
            handle.session.session_id = result.session_id
            handle.session.external = True
        return self._record_result(result)


@pytest.fixture()
def stream_json_text():
    """Factory: build a realistic ``--format stream-json`` stdout payload."""
    return _stream_json_text


@pytest.fixture()
def process_result():
    """Factory: build a canned ``ProcessResult``."""
    return _process_result


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
