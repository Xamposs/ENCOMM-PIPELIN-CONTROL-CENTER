"""Session 008 — the production app-data path contract.

The packaged application and ``python main.py`` must resolve the SAME per-user
production location, tests must be able to redirect it deterministically, and
no test may ever write into the real production directory.
"""

from __future__ import annotations

from pathlib import Path

from encomm_pcc.app import build_controller
from encomm_pcc.core import APP_NAME, DATA_DIR_ENV, AppPaths


def test_default_production_root_is_per_user_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    paths = AppPaths.resolve()
    assert paths.data_dir == tmp_path / "appdata" / APP_NAME
    assert paths.database.parent == paths.data_dir
    assert paths.logs_dir == paths.data_dir / "logs"


def test_data_dir_override_env_wins(monkeypatch, tmp_path):
    override = tmp_path / "redirected" / "pcc home"
    monkeypatch.setenv(DATA_DIR_ENV, str(override))
    paths = AppPaths.resolve()
    assert paths.data_dir == override
    assert paths.database == override / "pipeline_control_center.db"


def test_fallback_without_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    paths = AppPaths.resolve()
    assert paths.data_dir == tmp_path / "home" / ".local" / "share" / APP_NAME


def test_ensure_creates_the_writable_tree(tmp_path):
    paths = AppPaths.resolve(tmp_path / "prod").ensure()
    assert paths.data_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.database == paths.data_dir / "pipeline_control_center.db"


def test_explicit_base_is_respected_verbatim(tmp_path):
    paths = AppPaths.resolve(tmp_path / "explicit")
    assert paths.data_dir == tmp_path / "explicit"
    assert paths.logs_dir == tmp_path / "explicit" / "logs"


def test_packaged_and_development_use_the_same_resolver(monkeypatch, tmp_path):
    """One path resolver for both launch modes (brief §6)."""
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "shared"))
    assert AppPaths.resolve().data_dir == tmp_path / "shared"


def test_test_controllers_never_touch_the_production_directory(monkeypatch, tmp_path):
    """A controller built for tests writes only inside its redirected home."""
    prod = tmp_path / "production"
    redirected = tmp_path / "test-home"
    monkeypatch.setenv("LOCALAPPDATA", str(prod))
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)

    paths = AppPaths.resolve(redirected).ensure()
    controller = build_controller(paths=paths)
    controller.events.info("probe", source="test")
    controller.database.close()

    assert redirected in paths.database.parents or paths.database.parent == redirected
    assert not (prod / APP_NAME).exists()
    assert Path(str(paths.database)).is_relative_to(redirected)
