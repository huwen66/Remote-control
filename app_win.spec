# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None
src_dir = os.path.abspath('.')

pil_hiddenimports = collect_submodules('PIL')
pil_datas = collect_data_files('PIL')

a = Analysis(
    ['app.py'],
    pathex=[src_dir],
    binaries=[],
    datas=pil_datas,
    hiddenimports=pil_hiddenimports + [
        'pyautogui',
        'pyscreeze',
        'pytweening',
        'pygetwindow',
        'pynput',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'matplotlib', 'scipy', 'Quartz', 'rubicon'],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='RemoteControl',
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
    icon=None,
    version=None,
)
