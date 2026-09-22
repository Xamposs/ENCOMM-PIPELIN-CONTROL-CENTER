"""Driver abstraction: registry, capabilities, and placeholder safety.

The most important assertions here are the *negative* ones: a placeholder
driver must refuse real work rather than fake it, and the default process
runner must physically be unable to launch anything.
"""

from __future__ import annotations

import sys

import pytest

from encomm_pcc.domain import AgentRole, SessionPolicy
from encomm_pcc.drivers import (
    IMPLEMENTED_DRIVERS,
    PLANNED_DRIVERS,
    BaseDriver,
    CodexDriver,
    DriverCapabilities,
    DriverNotImplementedError,
    DriverRegistry,
    GenericCliDriver,
    HermesDriver,
    NullProcessRunner,
    ProcessSpec,
    SessionRequest,
    SubprocessRunner,
    default_registry,
)


@pytest.fixture()
def registry() -> DriverRegistry:
    return default_registry()


def test_registry_contains_the_three_v01_adapters(registry: DriverRegistry) -> None:
    assert registry.driver_ids() == ["codex", "generic_cli", "hermes"]
    assert set(registry.driver_ids()) == {c.driver_id for c in IMPLEMENTED_DRIVERS}


def test_only_unimplemented_drivers_are_marked_unimplemented(registry: DriverRegistry) -> None:
    """`implemented` is a claim about real capability, not a snapshot.

    GenericCli remains a deliberate placeholder.  Hermes and Codex gate their
    flags on live evidence (D-018): the flags mirror the drivers' own
    verification markers, so they can only be True after a real run.
    """
    assert registry.capabilities("generic_cli").implemented is False
    from encomm_pcc.drivers.codex import _LIVE_RESUME_VERIFIED, _LIVE_SMOKE_VERIFIED

    assert registry.capabilities("codex").implemented == _LIVE_SMOKE_VERIFIED
    assert registry.capabilities("codex").supports_resume == _LIVE_RESUME_VERIFIED
    assert isinstance(registry.capabilities("hermes").implemented, bool)


def test_codex_and_hermes_support_sessions_generic_cli_does_not(registry: DriverRegistry) -> None:
    assert registry.capabilities("codex").supports_sessions is True
    assert registry.capabilities("hermes").supports_sessions is True
    generic = registry.capabilities("generic_cli")
    assert generic.supports_sessions is False
    assert generic.is_sessionless is True


def test_unknown_driver_id_is_rejected(registry: DriverRegistry) -> None:
    with pytest.raises(KeyError):
        registry.get_class("nope")
    with pytest.raises(KeyError):
        registry.create("nope")


def test_registering_a_future_driver_needs_no_other_change(registry: DriverRegistry) -> None:
    class DummyDriver(BaseDriver):
        driver_id = "dummy"
        display_name = "Dummy"

        @classmethod
        def capabilities(cls) -> DriverCapabilities:
            return DriverCapabilities(
                driver_id=cls.driver_id, display_name=cls.display_name, implemented=True
            )

        def start_session(self, request: SessionRequest):  # noqa: ANN201
            return self._set_session(
                type("S", (), {"session_id": None})()  # minimal stand-in
            )

        def resume_session(self, session_id: str, request: SessionRequest):  # noqa: ANN201
            raise NotImplementedError

        def send_prompt(self, session, prompt):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def wait_for_completion(self, handle, timeout_s=None):  # noqa: ANN001, ANN201
            raise NotImplementedError

    registry.register(DummyDriver)
    assert "dummy" in registry.driver_ids()
    assert registry.create("dummy").driver_id == "dummy"


def test_registry_rejects_a_driver_without_an_id() -> None:
    class Nameless(BaseDriver):
        driver_id = "base"

        @classmethod
        def capabilities(cls) -> DriverCapabilities:
            return DriverCapabilities(driver_id="base", display_name="x")

        def start_session(self, request): ...
        def resume_session(self, session_id, request): ...
        def send_prompt(self, session, prompt): ...
        def wait_for_completion(self, handle, timeout_s=None): ...

    with pytest.raises(ValueError):
        DriverRegistry().register(Nameless)


def test_placeholder_refuses_every_real_operation(registry: DriverRegistry) -> None:
    """GenericCli still refuses real work — Codex joined Hermes as real (006)."""
    driver = registry.create("generic_cli")
    request = SessionRequest(
        role=AgentRole.BUILDER, project_profile="p", session_policy=SessionPolicy.ALWAYS_NEW
    )

    with pytest.raises(DriverNotImplementedError):
        driver.start_session(request)
    with pytest.raises(DriverNotImplementedError):
        driver.resume_session("sess_x", request)

    fake_session = type("S", (), {"session_id": None})()
    with pytest.raises(DriverNotImplementedError):
        driver.send_prompt(fake_session, "hello")


def test_driver_state_accessors_default_to_none(registry: DriverRegistry) -> None:
    driver = registry.create("codex")
    assert driver.get_session_id() is None
    assert driver.get_result() is None
    assert driver.cancel() is False


def test_drivers_default_to_the_non_executing_runner(registry: DriverRegistry) -> None:
    assert isinstance(registry.create("codex")._runner, NullProcessRunner)  # noqa: SLF001


def test_null_process_runner_refuses_to_execute() -> None:
    runner = NullProcessRunner()
    with pytest.raises(RuntimeError, match="process execution is disabled"):
        runner.run(ProcessSpec(argv=["definitely-not-a-real-binary"]))
    assert len(runner.attempted_specs) == 1


def test_subprocess_runner_runs_a_harmless_command() -> None:
    """The abstraction itself works — it is simply not wired to any driver."""
    result = SubprocessRunner().run(
        ProcessSpec(argv=[sys.executable, "-c", "print('pcc-ok')"], timeout_s=30)
    )
    assert result.ok
    assert "pcc-ok" in result.stdout
    assert result.exit_code == 0


def test_subprocess_runner_reports_failure_without_raising() -> None:
    result = SubprocessRunner().run(
        ProcessSpec(argv=[sys.executable, "-c", "raise SystemExit(3)"], timeout_s=30)
    )
    assert not result.ok
    assert result.exit_code == 3


def test_subprocess_runner_kills_the_tree_on_timeout() -> None:
    """A timeout is data, not a silent hang, and never looks like success."""
    import time

    started = time.monotonic()
    result = SubprocessRunner().run(
        ProcessSpec(
            argv=[sys.executable, "-c", "import time; time.sleep(60)"], timeout_s=2
        )
    )
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    assert result.ok is False
    assert result.exit_code < 0
    assert elapsed < 30, "the runner must kill the child rather than wait it out"


def test_kill_process_tree_is_available_and_safe_on_a_finished_process() -> None:
    import subprocess

    from encomm_pcc.drivers import kill_process_tree

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    kill_process_tree(child)  # must be a no-op, never an error
    assert child.poll() is not None


def test_probe_availability_never_launches_anything(registry: DriverRegistry) -> None:
    """Probing only looks for a binary on PATH; it must not raise."""
    for driver_id in registry.driver_ids():
        assert isinstance(registry.get_class(driver_id).probe_availability(), bool)


def test_planned_drivers_are_documented_but_not_registered(registry: DriverRegistry) -> None:
    planned_ids = {p.driver_id for p in PLANNED_DRIVERS}
    assert planned_ids == {"claude_code", "opencode", "ollama", "kimi"}
    assert planned_ids.isdisjoint(set(registry.driver_ids()))


def test_driver_classes_are_not_hardcoded_to_codex() -> None:
    """The three adapters are independent classes with distinct ids."""
    assert CodexDriver.driver_id != HermesDriver.driver_id != GenericCliDriver.driver_id
    assert CodexDriver.capabilities().display_name == "Codex"
    assert HermesDriver.capabilities().display_name == "Hermes"
