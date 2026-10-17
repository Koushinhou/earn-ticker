# -*- mode: python ; coding: utf-8 -*-
# Single source of truth for the exe build (used by build.ps1 and CI).
# Bundled data files are resolved at runtime via earnticker.resources.resource_path.


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('config/holidays.example.json', 'config')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='EarnTicker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/earnticker.ico'],
)
