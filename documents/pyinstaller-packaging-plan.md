# 远程桌面控制程序打包计划（PyInstaller）

## 摘要

使用 **PyInstaller** 将 `remote_control/` 下的：
- `client.py`（控制端）→ 打包为 **`RemoteClient.app`**（macOS 可双击运行）
- `server.py`（被控端）→ 打包为 **`RemoteServer.app`**（macOS 可双击运行）

打包后的客户端具有完整 GUI 界面（输入 IP/端口/密码 → 点击连接 → 实时查看远端桌面 → 鼠标键盘交互控制），无需安装 Python 环境。

---

## 当前状态分析

### 项目结构

```
/Users/zhou/Desktop/AI_tool/
└── remote_control/
    ├── common.py     # 通信协议公共模块
    ├── client.py     # 控制端 GUI（tkinter + PIL + socket）
    └── server.py     # 被控端 GUI（tkinter + PIL + pyautogui）
```

### 客户端 GUI 功能（打包后需保留完整交互）

| 界面状态 | 交互操作 |
|----------|----------|
| 连接界面 | 输入目标 IP、端口、密码；点击「连接」按钮 |
| 控制界面 | 实时查看远端桌面；鼠标移动/点击/滚轮；键盘输入；点击「断开连接」 |

### 关键依赖（已安装）

| 库 | 用途 | 打包注意 |
|----|------|---------|
| `pillow 12.1.0` | JPEG 帧解码、tkinter 图像渲染 | 需 `hiddenimports` |
| `tkinter` | GUI 框架 | PyInstaller 内建支持 |
| `socket/threading` | 标准库 | 无需处理 |

### ⚠️ 必须修复的源码兼容问题

`client.py` 第 11 行：
```python
sys.path.insert(0, os.path.dirname(__file__))
```
**在 PyInstaller 打包后，`__file__` 指向临时解压目录，`common` 模块找不到会导致启动崩溃。**

修复方案：改为使用 `sys._MEIPASS`（PyInstaller 运行时根目录）兼容写法：
```python
if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
else:
    _base = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _base)
```
`server.py` 同样需要此修复。

### PyInstaller 状态
- **当前未安装**，需先执行 `pip3 install pyinstaller`

---

## 变更详情（执行步骤）

### Step 1：安装 PyInstaller

```bash
pip3 install pyinstaller
```

### Step 2：修复 `client.py` 路径兼容问题

修改 `remote_control/client.py` 第 9-11 行：

**原始代码：**
```python
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
```

**修改为：**
```python
import sys
import os

if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
else:
    _base = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _base)
```

### Step 3：修复 `server.py` 路径兼容问题

修改 `remote_control/server.py` 第 10-12 行，与 Step 2 完全相同的改法。

### Step 4：创建 `remote_control/client.spec`

```python
# -*- mode: python ; coding: utf-8 -*-
import os
block_cipher = None
src_dir = os.path.abspath('.')

a = Analysis(
    ['client.py'],
    pathex=[src_dir],
    binaries=[],
    datas=[('common.py', '.')],
    hiddenimports=[
        'PIL._imaging',
        'PIL.Image',
        'PIL.ImageTk',
        'PIL.ImageFile',
        'common',
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='RemoteClient',
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

app = BUNDLE(
    exe,
    name='RemoteClient.app',
    icon=None,
    bundle_identifier='com.demo.remoteclient',
)
```

**关键点：**
- `datas=[('common.py', '.')]`：将 `common.py` 显式打入包内，放在运行时根目录
- `hiddenimports` 包含 `'common'`：确保 PyInstaller 分析阶段收录该模块
- `console=False`：不弹终端，纯 GUI 运行

### Step 5：创建 `remote_control/server.spec`

```python
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
        'PIL.ImageGrab',
        'PIL.ImageFile',
        'pyautogui',
        'pyscreeze',
        'pytweening',
        'pygetwindow',
        'rubicon.objc',
        'common',
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='RemoteServer',
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

app = BUNDLE(
    exe,
    name='RemoteServer.app',
    icon=None,
    bundle_identifier='com.demo.remoteserver',
)
```

### Step 6：执行打包

在 `remote_control/` 目录下：

```bash
cd /Users/zhou/Desktop/AI_tool/remote_control

pyinstaller client.spec --distpath dist --workpath build/client --noconfirm
pyinstaller server.spec --distpath dist --workpath build/server --noconfirm
```

### Step 7：验证

```bash
# 验证可执行文件存在
ls dist/RemoteClient dist/RemoteClient.app dist/RemoteServer dist/RemoteServer.app

# 启动测试（macOS）
open dist/RemoteClient.app
open dist/RemoteServer.app
```

端到端验证流程：
1. 启动 `RemoteServer.app` → 点击「启动服务」→ 显示「等待连接...」
2. 启动 `RemoteClient.app` → 输入 `127.0.0.1 / 5900 / demo1234` → 点击「连接」
3. 客户端显示服务端桌面画面，鼠标和键盘操作正常控制服务端

---

## 产物位置

```
remote_control/
├── dist/
│   ├── RemoteClient        ← 单文件可执行（备用）
│   ├── RemoteClient.app/   ← macOS .app 包（主要分发）
│   ├── RemoteServer        ← 单文件可执行（备用）
│   └── RemoteServer.app/   ← macOS .app 包（主要分发）
└── build/                  ← 临时缓存（可删除）
```

---

## 假设与决策

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 打包工具 | PyInstaller | 用户指定 |
| 打包范围 | client + server 均打包 | 用户需要完整演示 |
| `common.py` 处理 | `datas` + `sys._MEIPASS` 修复 | 确保打包后 import 不崩溃 |
| `console` | `False` | GUI 程序不需要终端 |
| 输出格式 | 单文件 EXE + `.app` | 双击运行最简便 |
| macOS 系统权限 | 运行时需手动授权 | 辅助功能 + 屏幕录制，无法通过打包绕过 |
