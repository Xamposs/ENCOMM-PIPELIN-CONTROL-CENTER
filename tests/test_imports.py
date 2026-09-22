"""Every module must compile and import cleanly.

This is the cheapest possible guard against a broken foundation: a syntax error
or a bad import anywhere in the package fails here first.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import encomm_pcc

PACKAGE_ROOT = Path(encomm_pcc.__file__).resolve().parent


def _module_names() -> list[str]:
    names = [encomm_pcc.__name__]
    for info in pkgutil.walk_packages([str(PACKAGE_ROOT)], prefix="encomm_pcc."):
        names.append(info.name)
    return sorted(set(names))


def test_package_has_version() -> None:
    assert encomm_pcc.__version__ == "0.5.0"


def test_every_module_imports() -> None:
    failures: list[str] = []
    for name in _module_names():
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - report every failure at once
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failures, "Import failures:\n" + "\n".join(failures)


def test_every_source_file_compiles() -> None:
    failures: list[str] = []
    files = sorted(PACKAGE_ROOT.rglob("*.py"))
    assert files, "No Python sources found — packaging layout is wrong."
    for path in files:
        source = path.read_text(encoding="utf-8")
        try:
            compile(source, str(path), "exec")
        except SyntaxError as exc:
            failures.append(f"{path}: {exc}")
    assert not failures, "Compile failures:\n" + "\n".join(failures)


def test_domain_and_core_layers_are_qt_free() -> None:
    """Domain, persistence and core must import in a process with no Qt loaded.

    The UI is a consumer of the core, never a dependency of it.
    """
    code = (
        "import sys;"
        "import encomm_pcc.domain, encomm_pcc.persistence, encomm_pcc.core;"
        "leaked = [m for m in sys.modules if m.startswith('PySide6')];"
        "assert not leaked, f'Qt leaked into core: {leaked}';"
        "print('ok')"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PACKAGE_ROOT.parent)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "ok" in result.stdout
