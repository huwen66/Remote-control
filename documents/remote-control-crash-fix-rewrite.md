# 远程控制程序崩溃修复与重构计划

## 一、崩溃根因（基于系统日志确认，非推测）

### 崩溃证据（/Users/zhou/Library/Logs/DiagnosticReports/）

所有崩溃日志（8 份，时间跨度 12:50 - 13:12）呈现**完全相同**的崩溃模式：

```
Exception Type:  EXC_BAD_ACCESS (SIGSEGV)
Exception Codes: KERN_INVALID_ADDRESS at 0x0000000000000010

Crashed Thread 帧栈（triggered: true）:
  Frame 0: objc_msgSend              [libobjc.A.dylib]    ← 向 nil/freed 对象发 ObjC 消息
  Frame 1: object_dealloc            [objc-object.m:158]  ← PyObjC 对象析构
  Frame 2: subtype_dealloc           [Python 3.11]        ← Python 子类型析构
  Frame 3: _PyFrame_Clear            [Python 3.11]        ← 子线程帧清理
  Frame 4: _PyEval_EvalFrameDefault  [Python 3.11]
  ...
  Frame N: pythread_wrapper          [Python 3.11]        ← 子线程
```

### 根因分析

**崩溃链路**：
```
_recv_loop 子线程
  └─ recv_msg() → 返回 payload (bytes)
  └─ cb(payload) → 调用 _on_frame(data)
       └─ root.after(0, _render_frame_main, data)
            ↑ data 此时作为 after 的参数被引用
  └─ del payload → 子线程帧结束，_PyFrame_Clear 清理局部变量
       └─ 某个局部变量引用了 PyObjC 包装的 bytes/buffer 对象
            └─ PyObjC 的 __del__ / dealloc 在子线程触发
                 └─ 向 NSClassicMapTable (ObjC 对象) 发 release 消息
                      └─ EXC_BAD_ACCESS (访问 0x10 = NULL+16 偏移)
```

**核心矛盾**：PyObjC 的 Objective-C 对象生命周期管理要求**所有 ObjC 对象的创建与释放必须在同一线程**（通常是主线程）。`_recv_loop` 是守护子线程，它收到帧数据（在 macOS PyObjC 环境下 bytes 的底层可能涉及 ObjC buffer 对象），其析构触发了跨线程 ObjC dealloc，导致段错误。

### 次级问题（同样需要修复）

1. **`_quartz_grab` 缺少崩溃防护**：若屏幕录制权限被撤销或为首次请求，`CGWindowListCreateImage` 在 macOS 新版中不返回 `None` 而是返回部分初始化对象，后续的 `CGImageGetWidth` 访问 `0x10` 偏移崩溃。
2. **`_send_frames` 子线程中 PIL Image 对象**：`_grab_screen()` 返回 PIL Image，`img.save()` 等操作在子线程中处理包含 PyObjC 引用的图像对象，存在同样的线程安全风险。
3. **大量帧数据通过 `root.after` 排队**：如果主线程处理来不及，after 队列积压导致内存增长。

---

## 二、重构方案

### 核心原则

> **子线程只处理纯 C/Python 原生数据（int, bytes, bytearray），绝不创建或持有任何 PyObjC/PIL/tkinter 对象。**

### 2.1 帧接收管线重构（最关键）

**改前（有问题）**：
```
子线程 _recv_loop
  → cb(payload)               ← payload 是 bytes，子线程持有
  → root.after(0, render, payload)
  → del payload               ← 子线程 _PyFrame_Clear 触发 PyObjC dealloc
```

**改后（安全）**：

引入 `queue.Queue`（线程安全的纯 Python 队列），子线程只把 bytes **放入队列**，不持有引用；主线程通过 `root.after` 轮询队列取出数据并渲染。

```python
import queue

# RemoteClient 增加帧队列
class RemoteClient:
    def __init__(self):
        self._frame_queue = queue.Queue(maxsize=2)  # 限制队列深度，防积压
        ...

    def _recv_loop(self):
        while self.connected:
            msg_type, payload = recv_msg(self.sock)
            if msg_type is None or msg_type == MSG_DISCONNECT:
                break
            if msg_type == MSG_FRAME:
                try:
                    self._frame_queue.put_nowait(payload)  # 满了就丢弃旧帧
                except queue.Full:
                    try:
                        self._frame_queue.get_nowait()     # 丢弃最旧帧
                        self._frame_queue.put_nowait(payload)
                    except Exception:
                        pass
                payload = None   # 立即解除子线程对 payload 的引用
        self.connected = False
        ...
```

**App 主线程轮询**：
```python
def _start_frame_poll(self):
    self._poll_frames()

def _poll_frames(self):
    if self._mode != "view" or not self._client:
        return
    try:
        data = self._client._frame_queue.get_nowait()
        self._render_frame_main(data)
    except queue.Empty:
        pass
    self.root.after(16, self._poll_frames)   # 约 60fps 轮询

def _render_frame_main(self, data):
    if self._mode != "view" or not hasattr(self, "canvas"):
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

**关键改动**：移除 `_on_frame` 回调机制，移除 `_frame_lock` 和 `_frame_pending` 标志，改为队列 + 主线程定时轮询。子线程 `_recv_loop` 只做 put，立即 `payload = None`。

### 2.2 屏幕捕获安全防护

**`_quartz_grab` 增加完整防护**：

```python
def _quartz_grab():
    try:
        img_ref = Quartz.CGWindowListCreateImage(...)
        if img_ref is None or img_ref == 0:
            return None
        # 验证图像有效性
        w = Quartz.CGImageGetWidth(img_ref)
        h = Quartz.CGImageGetHeight(img_ref)
        if w == 0 or h == 0:
            Quartz.CGImageRelease(img_ref)
            return None
        bpp = Quartz.CGImageGetBitsPerPixel(img_ref)
        bpr = Quartz.CGImageGetBytesPerRow(img_ref)
        dp  = Quartz.CGImageGetDataProvider(img_ref)
        if dp is None:
            Quartz.CGImageRelease(img_ref)
            return None
        raw = Quartz.CGDataProviderCopyData(dp)
        if raw is None:
            Quartz.CGImageRelease(img_ref)
            return None
        # 在 try 块内完成所有 PIL 操作
        mode = "RGBA" if bpp == 32 else "RGB"
        raw_bytes = bytes(raw)          # 立即转为纯 Python bytes
        del raw, dp                     # 释放 PyObjC 对象
        Quartz.CGImageRelease(img_ref)  # 在 del raw 之后释放
        img_ref = None
        img = PIL_Image.frombytes(mode, (w, h), raw_bytes, "raw", mode, bpr)
        result = img.convert("RGB")
        img.close()
        return result
    except Exception:
        return None
```

**关键改动**：
- 每一步 Quartz 调用都验证返回值非 None/0
- 在 PIL 对象创建前，先将所有 PyObjC 对象（raw, dp, img_ref）显式 del
- 整体包裹在 `try/except` 中，任何步骤失败都返回 None

### 2.3 `_send_frames` 子线程安全

`_send_frames` 在被控端子线程中调用 `_grab_screen()`，`_quartz_grab` 会创建 PIL Image 对象。这个操作本身在子线程，但 `_quartz_grab` 内 PIL 的 PyObjC 依赖已经过改良（2.2 节），保证 PyObjC 对象在子线程内即时释放，不跨线程传递。

额外改动：
- `img.save(buf, ...)` 之后立即 `img.close(); del img`（现有代码已做）
- 在帧发送循环增加整体 `try/except` 保证任何异常都不让子线程持有 PyObjC 对象

### 2.4 断开连接清理

停止帧轮询：
```python
def _disconnect_and_home(self):
    cli = self._client
    self._client = None
    self._mode = "home"    # 先改 mode，_poll_frames 检测到 mode != view 会自停
    if cli:
        cli.on_frame = None
        cli.on_disconnect = None
        cli.disconnect()
    self._build_home()
```

`_poll_frames` 自带 mode 检查，`mode != "view"` 时不再调度下一次 after，自然停止。

---

## 三、具体文件修改

### 文件：`/Users/zhou/Desktop/AI_tool/remote_control/app.py`

#### 修改 1：顶部增加 `import queue`

**位置**：第 1-13 行 import 区域
**改动**：增加 `import queue`

#### 修改 2：`RemoteClient` 类重构 `_recv_loop`

**位置**：第 311-377 行
**改动**：
- `__init__` 增加 `self._frame_queue = queue.Queue(maxsize=2)`
- 移除 `self.on_frame = None`（不再使用回调）
- `_recv_loop` 改为：收帧 → `_frame_queue.put_nowait` → `payload = None` → 继续
- 断开时 `on_disconnect` 回调保留（可以继续用）

#### 修改 3：`_build_grab_fn` → `_quartz_grab` 防护加固

**位置**：第 113-170 行
**改动**：如 2.2 节所述，每步验证 + 提前 del PyObjC 对象 + 整体 try/except

#### 修改 4：`App` 类移除 `_on_frame`/`_frame_lock`/`_frame_pending`，改为队列轮询

**位置**：第 380-390 行（`__init__`）及 719-742 行（帧处理）
**改动**：
- `__init__` 移除 `_frame_pending`, `_frame_lock`
- 移除 `_on_frame` 方法
- 新增 `_start_frame_poll` 和 `_poll_frames` 方法
- `_render_frame_main` 保留，内容不变（已在主线程）
- `_on_connect_ok` 中：建完 UI 后调用 `self._start_frame_poll()`，不再设置 `cli.on_frame`

#### 修改 5：`_on_connect_ok` 调整

**位置**：第 601-605 行
**改动**：
```python
def _on_connect_ok(self, cli):
    self._client = cli
    self._build_view_ui()
    # 不再设置 cli.on_frame，改用队列轮询
    cli.on_disconnect = self._on_remote_disconnect
    self._start_frame_poll()
```

#### 修改 6：断开连接时清理

**位置**：`_disconnect_and_home`、`_handle_remote_disconnect`、`_on_close_view`
**改动**：先设 `self._mode = "home"` 再调 `_build_home()`，确保 `_poll_frames` 自停

---

## 四、验证步骤

1. **语法验证**：`python3 -m py_compile app.py`
2. **直接运行测试**：`python3 app.py`，开启被控端，连接，验证连接后 10 秒内不崩溃
3. **持续运行测试**：连接后保持 60 秒，期间移动鼠标、点击，验证稳定性
4. **断开重连测试**：连接 → 断开 → 再连接，验证不崩溃
5. **检查崩溃日志**：`ls -t ~/Library/Logs/DiagnosticReports/ | head -5` 确认无新 RemoteControl 崩溃
6. **打包验证**：`PYINSTALLER_CONFIG_DIR=/tmp/pyinstaller_config python3 -m PyInstaller app.spec --distpath dist --workpath /tmp/build_app --noconfirm`

---

## 五、假设与决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 帧传递机制 | `queue.Queue` 替代 `root.after` 回调 | 彻底隔离子线程与主线程对象生命周期 |
| 队列深度 | maxsize=2 | 满队丢旧帧，防止内存积压；视频帧实时性优先 |
| 轮询间隔 | 16ms（约 60fps） | 匹配 tkinter 刷新率，不过度占用主线程 |
| Quartz 对象释放 | 每个 PyObjC 对象在使用后立即 del | 防止 PyObjC 对象跨出当前子线程帧 |
| 回调 `on_disconnect` | 保留 | 仍通过 `root.after(0, ...)` 调度到主线程，安全 |
