# -*- mode: python ; coding: utf-8 -*-
import os
block_cipher = None
src_dir = os.path.abspath('.')

a = Analysis(
    ['server.py'],
    pathex=[src_dir],
    binaries=[],
    datas=[('common.py', '.')],
    hiddenimports=[
        'PIL._imaging',
        'PIL.Image',
        'PIL.ImageFile',
        'pyautogui',
        'pyscreeze',
        'pytweening',
        'pygetwindow',
        'rubicon.objc',
        'common',
        'Quartz',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RemoteServer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RemoteServer',
)

app = BUNDLE(
    coll,
    name='RemoteServer.app',
    icon=None,
    bundle_identifier='com.demo.remoteserver',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSAppleScriptEnabled': False,
        'CFBundleDisplayName': '远程控制服务端',
        'CFBundleName': 'RemoteServer',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13.0',
    },
)
