# 远程控制程序重构计划（ToDesk 风格 + 崩溃修复）

## 一、目标

1. **彻底修复崩溃**：基于系统崩溃日志确认的根因（PyObjC 对象在子线程析构），使用 `queue.Queue` 替代回调彻底隔离线程与对象生命周期
2. **重构交互设计**：参考 ToDesk，改为左右分栏、自动启动被控服务的新交互模式
3. **双平台打包**：同时生成 macOS `.app` 和 Windows `.exe` 安装包

---

## 二、崩溃根因（系统日志确认）

```
Exception: EXC_BAD_ACCESS (SIGSEGV) at 0x0000000000000010

Crashed Thread 帧栈：
  objc_msgSend              ← 向 nil ObjC 对象发消息
  object_dealloc            ← PyObjC 对象在子线程析构
  subtype_dealloc           ← Python 子类型析构
  _PyFrame_Clear            ← 子线程帧清理触发析构
  pythread_wrapper          ← 子线程结束
```

**根因**：`_recv_loop` 子线程持有 `payload`（bytes），子线程结束时 `_PyFrame_Clear` 触发 PyObjC 对象 dealloc，在子线程释放 ObjC 对象 → SIGSEGV。Objective-C 对象必须在主线程释放。

---

## 三、新交互设计（ToDesk 风格）

### 3.1 主界面（左右分栏，600×400）

```
┌─────────────────────────────────────────────────────┐
│  🖥 远程控制                              [最小化] [✕] │
├──────────────────────────┬──────────────────────────┤
│       本机信息            │      连接到远程            │
│                          │                          │
│  设备码                   │  对方设备码               │
│  ┌──────────────────┐    │  ┌──────────────────┐    │
│  │   192.168.1.100  │    │  │   输入 IP 地址    │    │
│  └──────────────────┘    │  └──────────────────┘    │
│                          │                          │
│  连接密码                 │  连接密码                 │
│  ┌───────────┐  [修改]   │  ┌──────────────────┐    │
│  │  A3K9FZ   │          │  │   输入密码        │    │
│  └───────────┘          │  └──────────────────┘    │
│                          │                          │
│  状态: ● 等待连接         │      [  连  接  ]        │
│                          │                          │
│  ───────────────────      │                          │
│  他人可通过以上信息连接您  │  [连接状态提示文字]       │
└──────────────────────────┴──────────────────────────┘
```

### 3.2 交互变化对比

| 功能 | 现有设计 | 新设计（ToDesk 风格） |
|------|----------|----------------------|
| 被控服务 | 手动点击"开启被控" | **程序启动自动运行** |
| 主界面布局 | 上下两个卡片 | **左右分栏** |
| 设备码/IP | 显示本机 IP | **显示本机 IP（自动获取）** |
| 密码 | 随机生成，可修改 | **随机生成，可修改（保留）** |
| 开关按钮 | 开启/停止被控按钮 | **无按钮，后台自动运行** |
| 连接区域 | 输入 IP + 固定码 | **输入 IP + 密码（同一区域）** |

### 3.3 远控视图（不变，仅优化）

顶部工具栏增加：
- 左侧：连接信息（显示被控端 IP）
- 右侧：断开按钮

---

## 四、代码重构方案

### 4.1 崩溃修复：帧接收管线重构（最关键）

**原理**：子线程只做 `queue.put_nowait(payload); payload = None`，立即放弃对 payload 的引用。主线程通过 `root.after` 轮询队列消费帧数据并渲染。

```python
import queue

# RemoteClient 改造
class RemoteClient:
    def __init__(self):
        self._frame_queue = queue.Queue(maxsize=2)
        self.connected = False
        self.server_w = 1920
        self.server_h = 1080
        self.on_disconnect = None  # 保留断开回调

    def _recv_loop(self):
        while self.connected:
            try:
                msg_type, payload = recv_msg(self.sock)
                if msg_type is None or msg_type == MSG_DISCONNECT:
                    break
                if msg_type == MSG_FRAME:
                    try:
                        self._frame_queue.put_nowait(payload)
                    except queue.Full:
                        try:
                            self._frame_queue.get_nowait()   # 丢弃最旧帧
                            self._frame_queue.put_nowait(payload)
                        except Exception:
                            pass
                    payload = None  # 立即解除子线程对 payload 的引用！
            except Exception:
                break
        self.connected = False
        cb = self.on_disconnect
        if cb:
            cb()
```

**App 主线程帧轮询**：
```python
def _start_frame_poll(self):
    self.root.after(16, self._poll_frames)  # 约 60fps

def _poll_frames(self):
    if self._mode != "view" or not self._client:
        return  # 自动停止轮询
    try:
        data = self._client._frame_queue.get_nowait()
        self._render_frame(data)
    except queue.Empty:
        pass
    self.root.after(16, self._poll_frames)

def _render_frame(self, data):
    if not hasattr(self, "canvas") or self._mode != "view":
        return
    try:
        cw = self._view_w if self._view_w > 0 else DISPLAY_W
        ch = self._view_h if self._view_h > 0 else DISPLAY_H
        img = Image.open(io.BytesIO(data))
        img = img.resize((cw, ch), Image.BILINEAR)
        photo = ImageTk.PhotoImage(img)
        img.close()
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=photo)
        self._current_frame_photo = photo
    except Exception:
        pass
```

### 4.2 `_quartz_grab` 防护加固

每一步 Quartz 调用验证返回值，所有 PyObjC 对象在使用后立即 del，整体包裹 try/except：

```python
def _quartz_grab():
    try:
        img_ref = Quartz.CGWindowListCreateImage(...)
        if not img_ref:
            return None
        w = Quartz.CGImageGetWidth(img_ref)
        h = Quartz.CGImageGetHeight(img_ref)
        if w == 0 or h == 0:
            Quartz.CGImageRelease(img_ref)
            return None
        bpp = Quartz.CGImageGetBitsPerPixel(img_ref)
        bpr = Quartz.CGImageGetBytesPerRow(img_ref)
        dp  = Quartz.CGImageGetDataProvider(img_ref)
        if not dp:
            Quartz.CGImageRelease(img_ref)
            return None
        raw = Quartz.CGDataProviderCopyData(dp)
        if not raw:
            Quartz.CGImageRelease(img_ref)
            return None
        raw_bytes = bytes(raw)   # 转为纯 Python bytes
        del raw, dp              # 立即释放 PyObjC 对象
        Quartz.CGImageRelease(img_ref)
        img_ref = None
        mode = "RGBA" if bpp == 32 else "RGB"
        img = PIL_Image.frombytes(mode, (w, h), raw_bytes, "raw", mode, bpr)
        result = img.convert("RGB")
        img.close()
        return result
    except Exception:
        return None
```

### 4.3 App 类重构：新主界面

**`__init__` 改动**：
- 移除 `_frame_pending`, `_frame_lock`（改为队列）
- 启动时自动调用 `_auto_start_host()`

**新增 `_auto_start_host()`**：
```python
def _auto_start_host(self):
    def _do():
        srv = HostServer(DEFAULT_PORT, self._code, self._on_server_status)
        try:
            srv.start()
            self._server = srv
            self.root.after(0, lambda: self._server_status.set("等待连接..."))
        except Exception as ex:
            self.root.after(0, lambda: self._server_status.set(f"启动失败"))
    threading.Thread(target=_do, daemon=True).start()
```

**`_build_home()` 全部重写**：左右分栏布局（见 3.1），窗口尺寸 600×400。

**移除的方法**：`_start_host`, `_stop_host`（被 `_auto_start_host` 替代）

**新增方法**：`_start_frame_poll`, `_poll_frames`, `_render_frame`

**`_on_connect_ok` 改动**：
```python
def _on_connect_ok(self, cli):
    self._client = cli
    self._build_view_ui()
    cli.on_disconnect = self._on_remote_disconnect
    self._start_frame_poll()  # 启动轮询，不再设置 on_frame 回调
```

---

## 五、打包方案

### 5.1 macOS（.app）

使用现有 `app.spec`（onedir 模式），命令：
```bash
PYINSTALLER_CONFIG_DIR=/tmp/pyinstaller_config \
python3 -m PyInstaller app.spec \
  --distpath dist --workpath /tmp/build_app --noconfirm
```
输出：`dist/RemoteControl.app`

### 5.2 Windows（.exe）

新建 `app_win.spec`，使用 `--windowed` 模式（无命令行窗口）：
```python
# app_win.spec
a = Analysis(['app.py'], ...)
exe = EXE(pyz, a.scripts, ..., console=False, name='RemoteControl')
```

命令（在 Windows 环境执行）：
```bash
python -m PyInstaller app_win.spec --noconfirm
```
输出：`dist/RemoteControl.exe`（单文件）或 `dist/RemoteControl/` 目录

> **注意**：Windows 打包需要在 Windows 机器（或 Windows 虚拟机/CI）上执行，macOS 无法交叉编译 Windows 可执行文件。需确认您是否有 Windows 环境。

### 5.3 Windows 功能适配

`_build_grab_fn` 在非 Darwin 系统走 `PIL.ImageGrab` 分支（已有），Windows 无 pyobjc 问题。
`pyautogui` 在 Windows 上可正常控制鼠标键盘，无需额外适配。

---

## 六、修改文件清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `app.py` | **重写** | 全部修改，见第四节 |
| `app.spec` | 微调 | 更新 `name`、`info_plist` |
| `app_win.spec` | **新建** | Windows 打包配置 |

---

## 七、验证步骤

1. `python3 -m py_compile app.py` → 语法验证
2. `python3 app.py` → 直接运行：验证启动自动开启被控，左右分栏 UI 正常
3. 连接测试：输入本机 IP + 密码，点连接，验证 **连接后不崩溃**，画面正常显示
4. 持续运行 60 秒，移动鼠标，验证无崩溃
5. `ls -t ~/Library/Logs/DiagnosticReports/ | head -5` 确认无新 RemoteControl 崩溃日志
6. 执行 macOS 打包命令，验证 `dist/RemoteControl.app` 生成
7. （Windows 环境）执行 Windows 打包命令，验证 `dist/RemoteControl.exe` 生成
