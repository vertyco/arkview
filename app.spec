# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

_arkparser_submodules = collect_submodules("arkparser")
_arkparser_metadata = copy_metadata("arkparser")

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=[*_arkparser_metadata],
    hiddenimports=[
        "ipaddress",
        "aiosqlite",
        "watchdog.observers",
        "watchdog.observers.polling",
        "watchdog.events",
        "arkparser",
        *_arkparser_submodules,
    ],
    hookspath=["extra-hooks"],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="ArkViewer", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, upx_exclude=[], runtime_tmpdir=None,
    console=True, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
    uac_admin=True, icon=["giga.ico"],
)
