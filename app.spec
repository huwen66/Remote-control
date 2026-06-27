# -*- mode: python ; coding: utf-8 -*-
import os
block_cipher = None
src_dir = os.path.abspath('.')

a = Analysis(
    ['app.py'],
    pathex=[src_dir],
    binaries=[],
    datas=[],
    hiddenimports=[
        'PIL._imaging',
        'PIL._imagingtk',
        'PIL.Image',
        'PIL.ImageTk',
        'PIL.ImageFile',
        'PIL.JpegImagePlugin',
        'pyautogui',
        'pyscreeze',
        'pytweening',
        'pygetwindow',
        'mss',
        'mss.darwin',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'matplotlib', 'scipy'],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RemoteControl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity='-',
    entitlements_file='entitlements.plist',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RemoteControl',
)

app = BUNDLE(
    coll,
    name='RemoteControl.app',
    icon=None,
    bundle_identifier='com.remotecontrol.app',
    codesign_identity='-',
    entitlements_file='entitlements.plist',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSAppleScriptEnabled': False,
        'CFBundleDisplayName': '远程控制',
        'CFBundleName': 'RemoteControl',
        'CFBundleVersion': '3.0.0',
        'CFBundleShortVersionString': '3.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13.0',
        'NSScreenCaptureUsageDescription': '远程控制需要录制屏幕以将画面传输给控制端',
        'NSAccessibilityUsageDescription': '远程控制需要辅助功能权限以控制鼠标和键盘',
    },
)
