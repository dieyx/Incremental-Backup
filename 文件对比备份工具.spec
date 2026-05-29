# -*- mode: python ; coding: utf-8 -*-

import os
import sys

PY_BASE = sys.base_prefix
TCL_ROOT = os.path.join(PY_BASE, 'tcl')
DLL_ROOT = os.path.join(PY_BASE, 'DLLs')


a = Analysis(
    ['compare_backup.py'],
    pathex=[],
    binaries=[
        (os.path.join(DLL_ROOT, '_tkinter.pyd'), '.'),
        (os.path.join(DLL_ROOT, 'tcl86t.dll'), '.'),
        (os.path.join(DLL_ROOT, 'tk86t.dll'), '.'),
    ],
    datas=[
        (os.path.join(TCL_ROOT, 'tcl8.6'), '_tcl_data'),
        (os.path.join(TCL_ROOT, 'tk8.6'), '_tk_data'),
        (os.path.join(TCL_ROOT, 'tcl8'), 'tcl8'),
    ],
    hiddenimports=['tkinter', 'tkinter.ttk', '_tkinter'],
    hookspath=['pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=[os.path.join('pyinstaller_hooks', 'rthook_tkinter_manual.py')],
    excludes=[],
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
    name='文件对比备份工具',
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
)
