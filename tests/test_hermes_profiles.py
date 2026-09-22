"""Read-only Hermes profile discovery.

Discovery is a guard: a profile that does not exist makes ``hermes -p <name>``
fail, and a typo must be caught before a dispatch is attempted.  Nothing here
reads a secret, and no test spawns a real CLI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FakeProcessRunner
from encomm_pcc.core import (
    parse_profile_list,
    profile_roots,
)
from encomm_pcc.core.hermes_profiles import IDENTITY_MARKERS, discover_profiles
from encomm_pcc.drivers import ProcessResult

REAL_TABLE = (
    "\n"
    " Profile          Model                        Gateway      Alias        Distribution\n"
    " ───────────────    ───────────────────────────    ───────────    ───────────    ────────────────────\n"
    "  default         glm-5.3-flash                running      —            —\n"
    "  encomm-accounting-intelligence mimo-v2.5:free               stopped      encomm-accounting-intelligence —\n"
    " ◆encomm-pipeline-control-center glm-5.3-flash                stopped      encomm-pipeline-control-center —\n"
    "  erp-project     deepseek/deepseek-v4-flash   stopped      erp-project  —\n"
    "\n"
    "⚠ Profile 'x' shares its telegram credential with default: the bot can only belong to one profile.\n"
)


def test_parse_the_real_cli_table_including_the_active_marker() -> None:
    names = parse_profile_list(REAL_TABLE)
    assert names == [
        "default",
        "encomm-accounting-intelligence",
        "encomm-pipeline-control-center",
        "erp-project",
    ]


def test_parse_tolerates_ansi_and_carriage_returns() -> None:
    text = (
        "\x1b[1m Profile   Model   Gateway   Distribution\x1b[0m\r\n"
        "\x1b[1m ───────\x1b[0m\r\n"
        "\x1b[32m  alpha   m   stopped   —\x1b[0m\r\n"
    )
    assert parse_profile_list(text) == ["alpha"]


def test_parse_of_garbage_is_empty_not_a_guess() -> None:
    assert parse_profile_list("") == []
    assert parse_profile_list("not a table at all") == []
    # A header without the separator row is not a table either.
    assert parse_profile_list(" Profile  Model  Gateway  Distribution\n") == []


# -- directory scan -------------------------------------------------------------
def test_directory_scan_uses_the_identity_marker_rule(tmp_path: Path) -> None:
    root = tmp_path / "hermes"
    (root / "profiles" / "real-profile").mkdir(parents=True)
    (root / "profiles" / "real-profile" / "config.yaml").write_text("x", encoding="utf-8")
    # A marker-less dir (cron/log side-effect shell) must never be listed.
    (root / "profiles" / "ghost-shell").mkdir(parents=True)
    # Staging dirs are hidden and never profiles.
    (root / "profiles" / ".real-profile.staging-1").mkdir(parents=True)
    (root / "config.yaml").write_text("x", encoding="utf-8")

    result = discover_profiles(runner=None, roots=[str(root)])
    assert result.ok is True
    assert result.method == "directory-scan"
    assert set(result.profiles) == {"default", "real-profile"}


def test_directory_scan_finds_nothing_in_an_empty_root(tmp_path: Path) -> None:
    result = discover_profiles(runner=None, roots=[str(tmp_path / "nope")])
    assert result.ok is False
    assert result.profiles == ()


def test_every_marker_file_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "hermes"
    for index, marker in enumerate(IDENTITY_MARKERS):
        profile = root / "profiles" / f"p{index}"
        profile.mkdir(parents=True)
        (profile / marker).write_text("x", encoding="utf-8")
    result = discover_profiles(runner=None, roots=[str(root)])
    assert result.ok
    assert len(result.profiles) == len(IDENTITY_MARKERS)


# -- CLI method -----------------------------------------------------------------
def test_cli_method_is_preferred_and_parsed(tmp_path: Path) -> None:
    runner = FakeProcessRunner(
        results=[ProcessResult(argv=[], exit_code=0, stdout=REAL_TABLE, stderr="")]
    )
    result = discover_profiles(runner=runner, executable="hermes.exe", roots=[str(tmp_path)])

    assert result.ok is True
    assert result.method == "cli"
    assert "encomm-pipeline-control-center" in result.profiles
    assert runner.specs[0].argv_list()[1:] == ["profile", "list"]
    assert not [k for k in runner.specs[0].env if k.startswith("HERMES_")]


def test_cli_failure_falls_back_to_the_directory_scan(tmp_path: Path) -> None:
    root = tmp_path / "hermes"
    (root / "profiles" / "scanned").mkdir(parents=True)
    (root / "profiles" / "scanned" / ".env").write_text("x", encoding="utf-8")
    runner = FakeProcessRunner(
        results=[ProcessResult(argv=[], exit_code=1, stdout="", stderr="boom")]
    )
    result = discover_profiles(runner=runner, executable="hermes.exe", roots=[str(root)])

    assert result.ok is True
    assert result.method == "directory-scan"
    assert result.profiles == ("scanned",)
    assert result.error and "boom" in result.error


def test_both_methods_failing_is_reported_honestly(tmp_path: Path) -> None:
    runner = FakeProcessRunner(
        results=[ProcessResult(argv=[], exit_code=1, stdout="", stderr="no cli")]
    )
    result = discover_profiles(
        runner=runner, executable="hermes.exe", roots=[str(tmp_path / "empty")]
    )
    assert result.ok is False
    assert result.method == "none"
    assert result.error


def test_a_raising_runner_never_escapes(tmp_path: Path) -> None:
    class Exploding(FakeProcessRunner):
        def run(self, spec):  # noqa: ANN001, ANN201
            raise OSError("runner blew up")

    result = discover_profiles(
        runner=Exploding(), executable="hermes.exe", roots=[str(tmp_path / "empty")]
    )
    assert result.ok is False
    assert result.error and "runner blew up" in result.error


# -- roots ----------------------------------------------------------------------
def test_profile_roots_derives_from_a_profile_shaped_hermes_home(tmp_path: Path) -> None:
    home = tmp_path / "hermes" / "profiles" / "encomm-pipeline-control-center"
    roots = profile_roots({"HERMES_HOME": str(home)}, extra=[str(tmp_path / "extra")])
    assert (tmp_path / "hermes") in roots
    assert (tmp_path / "extra") in roots


def test_profile_roots_are_deduplicated() -> None:
    roots = profile_roots({"LOCALAPPDATA": "C:/LA"}, extra=["C:/LA/hermes"])
    assert len(roots) == len(set(roots))


def test_discovery_never_writes_anything(tmp_path: Path) -> None:
    root = tmp_path / "hermes"
    (root / "profiles" / "p1").mkdir(parents=True)
    (root / "profiles" / "p1" / "config.yaml").write_text("x", encoding="utf-8")
    before = sorted(p.name for p in (root / "profiles").iterdir())
    discover_profiles(runner=None, roots=[str(root)])
    after = sorted(p.name for p in (root / "profiles").iterdir())
    assert before == after
    assert not list(root.rglob("*.new"))