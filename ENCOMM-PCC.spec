# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — ENCOMM Pipeline Control Center (Windows release candidate).

One-folder build (dist/ENCOMM-PCC/ENCOMM-PCC.exe + runtime files), driven by
``scripts/build_windows.ps1``.  Build with:

    pyinstaller --noconfirm --clean ENCOMM-PCC.spec

Notes:
- ``schema.sql`` is loaded via ``Path(__file__).with_name(...)`` by
  ``persistence/database.py``, so it must be shipped as DATA next to the
  compiled persistence package, not inside the PYZ archive.
- No user data, credentials, Codex/Hermes auth state or databases are ever
  bundled: the spec touches nothing outside the source tree.
"""

a = Analysis(
    ["main.py"],
    pathex=["src"],
    binaries=[],
    datas=[("src/encomm_pcc/persistence/schema.sql", "encomm_pcc/persistence")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ENCOMM-PCC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ENCOMM-PCC",
)
