import socket
import threading
import queue
import io
import time
import random
import string
import struct
import json
import sys
import os
import tkinter as tk
from tkinter import messagebox, simpledialog
from PIL import Image, ImageTk

if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
else:
    _base = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _base)

DEFAULT_PORT   = 5900
JPEG_QUALITY   = 55
FRAME_INTERVAL = 0.05
DISPLAY_W      = 1280
DISPLAY_H      = 800

MSG_AUTH        = "AUTH"
MSG_AUTH_OK     = "AUTH_OK"
MSG_AUTH_FAIL   = "AUTH_FAIL"
MSG_FRAME       = "FRAME"
MSG_EVENT       = "EVENT"
MSG_DISCONNECT  = "DISCONNECT"
MSG_SCREEN_SIZE = "SCREEN_SIZE"


def _gen_code(n=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))


def send_msg(sock, msg_type: str, payload: bytes = b""):
    header = msg_type.encode("utf-8").ljust(16)[:16]
    length = struct.pack("!I", len(payload))
    sock.sendall(header + length + payload)


def recv_msg(sock):
    header = _recv_exact(sock, 16)
    if header is None:
        return None, None
    msg_type = header.decode("utf-8").strip()
    length_data = _recv_exact(sock, 4)
    if length_data is None:
        return None, None
    length = struct.unpack("!I", length_data)[0]
    if length == 0:
        return msg_type, b""
    payload = _recv_exact(sock, length)
    if payload is None:
        return None, None
    return msg_type, payload


def _recv_exact(sock, n):
    buf = bytearray(n)
    view = memoryview(buf)
    pos = 0
    while pos < n:
        got = sock.recv_into(view[pos:], n - pos)
        if not got:
            return None
        pos += got
    return bytes(buf)


def encode_event(d: dict) -> bytes:
    return json.dumps(d, separators=(',', ':')).encode("utf-8")


def decode_event(b: bytes) -> dict:
    return json.loads(b.decode("utf-8"))


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


_pyautogui  = None
_grab_screen = None


# ---------- macOS 进程内截图（使用 mss 库，基于 CoreGraphics）----------
_mss_instance = None
_mss_monitor = None

def _grab_screen_cg():
    """用 mss 库在进程内截取整个屏幕，返回 PIL.Image（RGB）或 None"""
    global _mss_instance, _mss_monitor
    try:
        import mss
        from PIL import Image as _PILImage

        if _mss_instance is None:
            _mss_instance = mss.mss()
            _mss_monitor = _mss_instance.monitors[1] if len(_mss_instance.monitors) > 1 else _mss_instance.monitors[0]

        img = _mss_instance.grab(_mss_monitor)
        if img is None or img.size[0] == 0 or img.size[1] == 0:
            return None

        pil_img = _PILImage.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        return pil_img
    except Exception:
        return None


def _request_screen_capture_permission():
    """用 ctypes 调用 CGRequestScreenCaptureAccess 申请屏幕录制权限（会弹窗）"""
    try:
        import ctypes, ctypes.util
        coregraphics = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        coregraphics.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        coregraphics.CGRequestScreenCaptureAccess.argtypes = []
        return coregraphics.CGRequestScreenCaptureAccess()
    except Exception:
        return False


def _check_accessibility_permission():
    """检查是否有辅助功能权限"""
    import platform
    if platform.system() != "Darwin":
        return True
    try:
        import ctypes, ctypes.util
        hiservices = ctypes.CDLL(ctypes.util.find_library("HIServices"))
        hiservices.AXIsProcessTrusted.restype = ctypes.c_bool
        hiservices.AXIsProcessTrusted.argtypes = []
        return hiservices.AXIsProcessTrusted()
    except Exception:
        return True


def _request_accessibility_permission():
    """请求辅助功能权限（打开系统设置的辅助功能页面）"""
    import platform
    if platform.system() != "Darwin":
        return
    try:
        import subprocess
        subprocess.run([
            "osascript", "-e",
            'tell application "System Preferences" to activate'
        ])
        subprocess.run([
            "osascript", "-e",
            'tell application "System Preferences" to reveal anchor "Privacy_Accessibility" of pane id "com.apple.preference.security"'
        ])
    except Exception:
        pass


def _build_grab_fn():
    import platform
    if platform.system() != "Darwin":
        from PIL import ImageGrab
        return lambda: ImageGrab.grab()

    # macOS：用进程内 CGWindowListCreateImage，不再用 screencapture 命令
    return _grab_screen_cg


def _lazy_server_init():
    global _pyautogui, _grab_screen
    if _pyautogui is None:
        import pyautogui as _pag
        _pag.FAILSAFE = False
        _pag.PAUSE = 0
        _pyautogui = _pag
    if _grab_screen is None:
        _grab_screen = _build_grab_fn()


class HostServer:
    def __init__(self, port, code, on_status, on_permission_needed=None, on_accessibility_needed=None):
        self.port        = port
        self.code        = code
        self.on_status   = on_status
        self.on_permission_needed = on_permission_needed
        self.on_accessibility_needed = on_accessibility_needed
        self.server_sock = None
        self.client_sock = None
        self.running     = False
        self.connected   = False
        self.screen_w    = 0
        self.screen_h    = 0
        self._perm_requested = False
        self._access_requested = False

    def start(self):
        _lazy_server_init()
        self.screen_w, self.screen_h = _pyautogui.size()
        self.running     = True
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", self.port))
        self.server_sock.listen(1)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while self.running:
            try:
                self.server_sock.settimeout(1.0)
                try:
                    conn, addr = self.server_sock.accept()
                except socket.timeout:
                    continue
                self.on_status(f"连接中: {addr[0]}")
                self._handle_client(conn, addr)
                self.on_status("等待连接...")
            except Exception:
                if self.running:
                    time.sleep(0.5)

    def _handle_client(self, conn, addr):
        try:
            msg_type, payload = recv_msg(conn)
            if msg_type != MSG_AUTH:
                conn.close()
                return
            if payload.decode("utf-8") != self.code:
                send_msg(conn, MSG_AUTH_FAIL, b"wrong code")
                conn.close()
                self.on_status("认证失败，等待连接...")
                return
            send_msg(conn, MSG_AUTH_OK)
            send_msg(conn, MSG_SCREEN_SIZE,
                     f"{self.screen_w},{self.screen_h}".encode())
            self.client_sock = conn
            self.connected   = True
            self.on_status(f"已连接: {addr[0]}")
            ft = threading.Thread(target=self._send_frames, args=(conn,), daemon=True)
            ft.start()
            self._recv_events(conn)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self.client_sock = None
            self.connected   = False

    def _send_frames(self, conn):
        buf = io.BytesIO()
        while self.connected:
            try:
                img = _grab_screen()
                if img is None:
                    # 截图失败，请求权限（仅请求一次）
                    if not self._perm_requested and self.on_permission_needed:
                        self._perm_requested = True
                        self.on_permission_needed()
                    time.sleep(FRAME_INTERVAL)
                    continue
                self._perm_requested = False
                buf.seek(0)
                buf.truncate(0)
                img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=False)
                img.close()
                del img
                payload = buf.getvalue()
                send_msg(conn, MSG_FRAME, payload)
                time.sleep(FRAME_INTERVAL)
            except Exception:
                break
        buf.close()

    def _recv_events(self, conn):
        while self.connected:
            try:
                msg_type, payload = recv_msg(conn)
                if msg_type is None or msg_type == MSG_DISCONNECT:
                    break
                if msg_type == MSG_EVENT:
                    self._execute_event(decode_event(payload))
            except Exception:
                break

    def _execute_event(self, ev):
        try:
            if not _check_accessibility_permission():
                if not self._access_requested and self.on_accessibility_needed:
                    self._access_requested = True
                    self.on_accessibility_needed()
                return
            self._access_requested = False
            kind = ev.get("kind")
            if kind == "mouse_move":
                _pyautogui.moveTo(ev["x"], ev["y"], duration=0)
            elif kind == "mouse_down":
                _pyautogui.mouseDown(ev["x"], ev["y"], button=ev.get("button", "left"))
            elif kind == "mouse_up":
                _pyautogui.mouseUp(ev["x"], ev["y"], button=ev.get("button", "left"))
            elif kind == "mouse_click":
                btn = ev.get("button", "left")
                if ev.get("double"):
                    _pyautogui.doubleClick(ev["x"], ev["y"], button=btn)
                else:
                    _pyautogui.click(ev["x"], ev["y"], button=btn)
            elif kind == "mouse_scroll":
                _pyautogui.scroll(ev.get("delta", 1), x=ev["x"], y=ev["y"])
            elif kind == "key_press":
                _pyautogui.press(ev.get("key", ""))
            elif kind == "key_type":
                ch = ev.get("char", "")
                if ch:
                    _pyautogui.typewrite(ch, interval=0)
        except Exception:
            pass

    def stop(self):
        self.running   = False
        self.connected = False
        for s in (self.client_sock, self.server_sock):
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        self.client_sock = None
        self.server_sock = None


class RemoteClient:
    def __init__(self):
        self.sock          = None
        self.connected     = False
        self.server_w      = 1920
        self.server_h      = 1080
        self.on_disconnect = None
        self._frame_queue  = queue.Queue(maxsize=2)

    def connect(self, host, port, code):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(6)
        s.connect((host, port))
        s.settimeout(None)
        send_msg(s, MSG_AUTH, code.encode("utf-8"))
        msg_type, payload = recv_msg(s)
        if msg_type == MSG_AUTH_FAIL:
            s.close()
            raise PermissionError("密码错误，连接被拒绝")
        if msg_type != MSG_AUTH_OK:
            s.close()
            raise ConnectionError("握手协议失败")
        msg_type, payload = recv_msg(s)
        if msg_type == MSG_SCREEN_SIZE:
            parts = payload.decode().split(",")
            self.server_w, self.server_h = int(parts[0]), int(parts[1])
        self.sock      = s
        self.connected = True
        threading.Thread(target=self._recv_loop, daemon=True).start()

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
                            self._frame_queue.get_nowait()
                            self._frame_queue.put_nowait(payload)
                        except Exception:
                            pass
                    payload = None
            except Exception:
                break
        self.connected = False
        cb = self.on_disconnect
        if cb:
            cb()

    def send_event(self, ev):
        if self.connected and self.sock:
            try:
                send_msg(self.sock, MSG_EVENT, encode_event(ev))
            except Exception:
                pass

    def disconnect(self):
        self.connected = False
        if self.sock:
            try:
                send_msg(self.sock, MSG_DISCONNECT)
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def map_xy(self, cx, cy, cw, ch):
        return int(cx / cw * self.server_w), int(cy / ch * self.server_h)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("远程控制")
        self.root.configure(bg="#0f1117")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

        self._code                = _gen_code()
        self._server              = None
        self._client              = None
        self._current_frame_photo = None
        self._mode                = "home"
        self._view_w              = DISPLAY_W
        self._view_h              = DISPLAY_H

        self._build_home()
        self._auto_start_host()

    def _clear(self):
        for w in self.root.winfo_children():
            w.destroy()

    def _auto_start_host(self):
        def _do():
            srv = HostServer(DEFAULT_PORT, self._code, self._on_server_status,
                             on_permission_needed=self._on_permission_needed,
                             on_accessibility_needed=self._on_accessibility_needed)
            try:
                srv.start()
                self._server = srv
                self.root.after(0, self._on_host_ready)
            except Exception:
                self.root.after(0, self._on_host_fail)
        threading.Thread(target=_do, daemon=True).start()

    def _on_permission_needed(self):
        """服务端需要屏幕录制权限时回调，在主线程请求权限"""
        self.root.after(0, self._request_screen_permission)

    def _request_screen_permission(self):
        """在主线程请求屏幕录制权限"""
        import platform
        if platform.system() != "Darwin":
            return
        _request_screen_capture_permission()

    def _on_accessibility_needed(self):
        """服务端需要辅助功能权限时回调，在主线程请求权限"""
        self.root.after(0, self._request_accessibility)

    def _request_accessibility(self):
        """在主线程请求辅助功能权限"""
        import platform
        if platform.system() != "Darwin":
            return
        _request_accessibility_permission()

    def _on_host_ready(self):
        if self._mode != "home":
            return
        try:
            self._srv_status_var.set("● 等待连接")
            self._srv_status_lbl.config(fg="#22c55e")
        except Exception:
            pass

    def _copy_to_clipboard(self, text, label="内容"):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            self._show_toast(f"{label}已复制")
        except Exception:
            pass

    def _show_toast(self, message, duration=1500):
        try:
            tw = tk.Toplevel(self.root)
            tw.overrideredirect(True)
            tw.attributes("-topmost", True)
            tw.configure(bg="#1a1d27")
            tk.Label(tw, text=message, bg="#1a1d27", fg="#f1f5f9",
                     font=("Helvetica", 11), padx=18, pady=10).pack()
            try:
                tw.attributes("-alpha", 0.95)
            except Exception:
                pass

            def _center():
                try:
                    self.root.update_idletasks()
                    rx = self.root.winfo_x()
                    ry = self.root.winfo_y()
                    rw = self.root.winfo_width()
                    rh = self.root.winfo_height()
                    tw.update_idletasks()
                    tw_w = tw.winfo_width()
                    tw_h = tw.winfo_height()
                    x = rx + (rw - tw_w) // 2
                    y = ry + rh - tw_h - 30
                    tw.geometry(f"+{x}+{y}")
                except Exception:
                    pass

            _center()
            tw.after(duration, tw.destroy)
        except Exception:
            pass

    def _refresh_code(self):
        self._code = _gen_code()
        self._code_var.set(self._code)
        if self._server:
            self._server.code = self._code
        self._show_toast("密码已刷新")

    def _on_host_fail(self):
        if self._mode != "home":
            return
        try:
            self._srv_status_var.set("启动失败")
            self._srv_status_lbl.config(fg="#ef4444")
            if hasattr(self, "_srv_dot"):
                self._srv_dot.config(fg="#ef4444")
        except Exception:
            pass

    def _on_server_status(self, text):
        if self._mode != "home":
            return
        self.root.after(0, lambda: self._safe_update_srv_status(text))

    def _safe_update_srv_status(self, text):
        if self._mode != "home":
            return
        try:
            self._srv_status_var.set(text)
            color = "#22c55e" if "已连接" in text else "#22c55e" if "等待" in text else "#64748b"
            self._srv_status_lbl.config(fg=color)
            if hasattr(self, "_srv_dot"):
                self._srv_dot.config(fg=color)
        except Exception:
            pass

    def _build_home(self):
        self._mode = "home"
        self._clear()
        if hasattr(self, "canvas"):
            del self.canvas

        W, H = 700, 480
        self.root.geometry(f"{W}x{H}")
        self.root.resizable(False, False)

        BG       = "#0d1117"
        PANEL    = "#161b22"
        CARD     = "#0d1117"
        BORDER   = "#30363d"
        BORDER_2 = "#21262d"
        ACCENT   = "#58a6ff"
        ACCENT_2 = "#1f6feb"
        ACCENT_BG = "#238636"
        ACCENT_BG_HOVER = "#2ea043"
        GREEN    = "#3fb950"
        TEXT     = "#f0f6fc"
        TEXT_2   = "#c9d1d9"
        MUTED    = "#8b949e"
        DANGER   = "#f85149"
        YELLOW   = "#d29922"

        def _pill(parent, text, bg, fg, hover_bg, cmd,
                  pad_x=12, pad_y=5, font_size=11, bold=False):
            lbl = tk.Label(parent, text=text, bg=bg, fg=fg, cursor="hand2",
                           font=("Helvetica", font_size, "bold" if bold else "normal"),
                           padx=pad_x, pady=pad_y)
            lbl.bind("<Button-1>", lambda e: cmd())
            lbl.bind("<Enter>", lambda e: lbl.config(bg=hover_bg))
            lbl.bind("<Leave>", lambda e: lbl.config(bg=bg))
            return lbl

        header = tk.Frame(self.root, bg=BG, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="远程控制", bg=BG, fg=TEXT,
                 font=("Helvetica", 18, "bold")).pack(side="left", padx=32, pady=18)
        tk.Label(header, text="v3.0", bg=BG, fg=MUTED,
                 font=("Helvetica", 11)).pack(side="left", pady=26)

        sep = tk.Frame(self.root, bg=BORDER_2, height=1)
        sep.pack(fill="x")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        left  = tk.Frame(body, bg=PANEL, width=340)
        left.pack(side="left", fill="both", expand=True)
        left.pack_propagate(False)

        vdiv = tk.Frame(body, bg=BORDER_2, width=1)
        vdiv.pack(side="left", fill="y")

        right = tk.Frame(body, bg=BG, width=359)
        right.pack(side="left", fill="both", expand=True)
        right.pack_propagate(False)

        tk.Label(left, text="本机信息", bg=PANEL, fg=MUTED,
                 font=("Helvetica", 11, "bold")).pack(anchor="w", padx=32, pady=(32, 16))

        ip_card = tk.Frame(left, bg=PANEL, highlightthickness=1,
                           highlightbackground=BORDER_2)
        ip_card.pack(fill="x", padx=32, pady=(0, 12))
        ip_inner = tk.Frame(ip_card, bg=CARD)
        ip_inner.pack(fill="x", padx=1, pady=1)
        ip_head = tk.Frame(ip_inner, bg=CARD)
        ip_head.pack(fill="x", padx=18, pady=(16, 4))
        tk.Label(ip_head, text="设备码（IP）", bg=CARD, fg=MUTED,
                 font=("Helvetica", 10)).pack(side="left")
        _pill(ip_head, "复制", CARD, ACCENT, BORDER_2,
              lambda: self._copy_to_clipboard(self._ip_val.get(), "IP"),
              pad_x=10, pad_y=3, font_size=10).pack(side="right")
        self._ip_val = tk.StringVar(value="获取中...")
        tk.Label(ip_inner, textvariable=self._ip_val, bg=CARD, fg=TEXT,
                 font=("Helvetica", 22, "bold")).pack(anchor="w", padx=18, pady=(0, 16))

        def _fetch_ip():
            ip = get_local_ip()
            self.root.after(0, lambda: self._ip_val.set(ip) if self._mode == "home" else None)
        threading.Thread(target=_fetch_ip, daemon=True).start()

        pwd_card = tk.Frame(left, bg=PANEL, highlightthickness=1,
                            highlightbackground=BORDER_2)
        pwd_card.pack(fill="x", padx=32, pady=(0, 12))
        pwd_inner = tk.Frame(pwd_card, bg=CARD)
        pwd_inner.pack(fill="x", padx=1, pady=1)
        pw_head = tk.Frame(pwd_inner, bg=CARD)
        pw_head.pack(fill="x", padx=18, pady=(16, 4))
        tk.Label(pw_head, text="连接密码", bg=CARD, fg=MUTED,
                 font=("Helvetica", 10)).pack(side="left")
        pw_right = tk.Frame(pw_head, bg=CARD)
        pw_right.pack(side="right")
        _pill(pw_right, "刷新", CARD, ACCENT, BORDER_2,
              self._refresh_code, pad_x=10, pad_y=3, font_size=10).pack(side="right", padx=(8, 0))
        _pill(pw_right, "复制", CARD, ACCENT, BORDER_2,
              lambda: self._copy_to_clipboard(self._code_var.get(), "密码"),
              pad_x=10, pad_y=3, font_size=10).pack(side="right")
        self._code_var = tk.StringVar(value=self._code)
        tk.Label(pwd_inner, textvariable=self._code_var, bg=CARD, fg=GREEN,
                 font=("Menlo", 28, "bold")).pack(anchor="w", padx=18, pady=(0, 16))

        status_wrap = tk.Frame(left, bg=PANEL)
        status_wrap.pack(fill="x", padx=32, pady=(16, 0))

        srv_row = tk.Frame(status_wrap, bg=PANEL)
        srv_row.pack(fill="x")
        self._srv_dot = tk.Label(srv_row, text="●", bg=PANEL, fg=MUTED,
                                 font=("Helvetica", 9))
        self._srv_dot.pack(side="left")
        self._srv_status_var = tk.StringVar(value="初始化中...")
        self._srv_status_lbl = tk.Label(srv_row, textvariable=self._srv_status_var,
                                        bg=PANEL, fg=MUTED,
                                        font=("Helvetica", 11))
        self._srv_status_lbl.pack(side="left", padx=(8, 0))

        tk.Label(left, text="他人通过以上信息可连接本机", bg=PANEL, fg=MUTED,
                 font=("Helvetica", 10)).pack(anchor="w", padx=32, pady=(24, 0))

        tk.Label(right, text="连接到远程", bg=BG, fg=MUTED,
                 font=("Helvetica", 11, "bold")).pack(anchor="w", padx=36, pady=(32, 16))

        tk.Label(right, text="对方 IP 地址", bg=BG, fg=TEXT_2,
                 font=("Helvetica", 12)).pack(anchor="w", padx=36, pady=(0, 8))
        self._ip_entry = tk.Entry(right, font=("Helvetica", 14), bg=CARD,
                                  fg=TEXT, insertbackground=TEXT,
                                  relief="flat", highlightthickness=1,
                                  highlightbackground=BORDER_2,
                                  highlightcolor=ACCENT)
        self._ip_entry.pack(fill="x", padx=36, ipady=10)

        tk.Label(right, text="连接密码", bg=BG, fg=TEXT_2,
                 font=("Helvetica", 12)).pack(anchor="w", padx=36, pady=(18, 8))
        self._pwd_entry = tk.Entry(right, font=("Helvetica", 14), bg=CARD,
                                   fg=TEXT, insertbackground=TEXT,
                                   relief="flat", highlightthickness=1,
                                   highlightbackground=BORDER_2,
                                   highlightcolor=ACCENT,
                                   show="●")
        self._pwd_entry.pack(fill="x", padx=36, ipady=10)

        self._conn_status_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self._conn_status_var, bg=BG, fg=DANGER,
                 font=("Helvetica", 11)).pack(anchor="w", padx=36, pady=(12, 0))

        btn_wrap = tk.Frame(right, bg=BG)
        btn_wrap.pack(fill="x", padx=36, pady=(24, 0))
        self._connect_btn = _pill(btn_wrap, "连  接", ACCENT_BG, "white",
                                  ACCENT_BG_HOVER, self._do_connect,
                                  pad_x=0, pad_y=13, font_size=14, bold=True)
        self._connect_btn.pack(fill="x")

        hint = tk.Frame(right, bg=BG)
        hint.pack(fill="x", padx=36, pady=(28, 0))
        tk.Label(hint, text="•", bg=BG, fg=MUTED,
                 font=("Helvetica", 10)).pack(side="left")
        tk.Label(hint, text="  被控端需授权「屏幕录制」和「辅助功能」",
                 bg=BG, fg=MUTED, font=("Helvetica", 10)).pack(side="left")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close_home)

    def _change_code(self):
        new = simpledialog.askstring(
            "修改密码", "输入新密码（建议 6-12 位字母数字）:",
            initialvalue=self._code, parent=self.root)
        if new and new.strip():
            self._code = new.strip().upper()
            self._code_var.set(self._code)
            if self._server:
                self._server.code = self._code

    def _do_connect(self):
        host = self._ip_entry.get().strip()
        code = self._pwd_entry.get().strip()
        if not host:
            self._conn_status_var.set("请输入对方 IP 地址")
            return
        if not code:
            self._conn_status_var.set("请输入连接密码")
            return
        self._conn_status_var.set("连接中...")
        self._set_connect_btn(False)

        def _bg():
            cli = RemoteClient()
            try:
                cli.connect(host, DEFAULT_PORT, code)
            except PermissionError as e:
                self.root.after(0, self._on_connect_fail, str(e))
                return
            except Exception as e:
                self.root.after(0, self._on_connect_fail, f"连接失败: {e}")
                return
            self.root.after(0, self._on_connect_ok, cli)

        threading.Thread(target=_bg, daemon=True).start()

    def _set_connect_btn(self, enabled):
        try:
            if enabled:
                self._connect_btn.config(bg="#238636", fg="white", cursor="hand2")
                self._connect_btn.bind("<Button-1>", lambda e: self._do_connect())
                self._connect_btn.bind("<Enter>", lambda e: self._connect_btn.config(bg="#2ea043"))
                self._connect_btn.bind("<Leave>", lambda e: self._connect_btn.config(bg="#238636"))
            else:
                self._connect_btn.config(bg="#238636", fg="white", cursor="watch")
                self._connect_btn.unbind("<Button-1>")
                self._connect_btn.unbind("<Enter>")
                self._connect_btn.unbind("<Leave>")
        except Exception:
            pass

    def _on_connect_fail(self, msg):
        if self._mode != "home":
            return
        try:
            self._conn_status_var.set(msg)
            self._set_connect_btn(True)
        except Exception:
            pass

    def _on_connect_ok(self, cli):
        self._client = cli
        self._build_view_ui()
        cli.on_disconnect = self._on_remote_disconnect
        self._start_frame_poll()

    def _build_view_ui(self):
        self._mode   = "view"
        self._clear()
        if hasattr(self, "canvas"):
            del self.canvas
        self._current_frame_photo = None
        self._view_w = DISPLAY_W
        self._view_h = DISPLAY_H

        self.root.geometry(f"{DISPLAY_W}x{DISPLAY_H + 44}")
        self.root.resizable(True, True)

        BG_BAR = "#1a1d27"
        bar = tk.Frame(self.root, bg=BG_BAR, height=44)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        left_bar = tk.Frame(bar, bg=BG_BAR)
        left_bar.pack(side="left", fill="y")
        tk.Label(left_bar, text="🖥  远程控制中", bg=BG_BAR, fg="#f1f5f9",
                 font=("Helvetica", 12, "bold")).pack(side="left", padx=16, pady=10)

        host_ip = ""
        if self._client and self._client.sock:
            try:
                host_ip = self._client.sock.getpeername()[0]
            except Exception:
                pass
        if host_ip:
            tk.Label(left_bar, text=f"  {host_ip}", bg=BG_BAR, fg="#64748b",
                     font=("Helvetica", 10)).pack(side="left", pady=10)

        tk.Button(bar, text="断开连接", bg="#ef4444", fg="white",
                  font=("Helvetica", 10, "bold"), relief="flat",
                  padx=14, pady=4, cursor="hand2",
                  command=self._disconnect_and_home).pack(side="right", padx=12, pady=8)

        self.canvas = tk.Canvas(self.root, bg="#000000",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        def _on_resize(e):
            if e.width > 10 and e.height > 10:
                self._view_w = e.width
                self._view_h = e.height

        self.canvas.bind("<Configure>",        _on_resize)
        self.canvas.bind("<Motion>",           self._ev_move)
        self.canvas.bind("<ButtonPress-1>",    lambda e: self._ev_down(e, "left"))
        self.canvas.bind("<ButtonRelease-1>",  lambda e: self._ev_up(e, "left"))
        self.canvas.bind("<ButtonPress-3>",    lambda e: self._ev_down(e, "right"))
        self.canvas.bind("<ButtonRelease-3>",  lambda e: self._ev_up(e, "right"))
        self.canvas.bind("<ButtonPress-2>",    lambda e: self._ev_down(e, "middle"))
        self.canvas.bind("<ButtonRelease-2>",  lambda e: self._ev_up(e, "middle"))
        self.canvas.bind("<Double-Button-1>",  lambda e: self._ev_dblclick(e))
        self.canvas.bind("<MouseWheel>",       self._ev_scroll)
        self.canvas.bind("<Button-4>",         self._ev_scroll)
        self.canvas.bind("<Button-5>",         self._ev_scroll)
        self.root.bind("<KeyPress>",           self._ev_key)
        self.canvas.focus_set()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_view)

    def _map(self, ex, ey):
        if not self._client:
            return 0, 0
        cw = self._view_w if self._view_w > 0 else DISPLAY_W
        ch = self._view_h if self._view_h > 0 else DISPLAY_H
        return self._client.map_xy(ex, ey, cw, ch)

    def _ev_move(self, e):
        if not self._client:
            return
        x, y = self._map(e.x, e.y)
        self._client.send_event({"kind": "mouse_move", "x": x, "y": y})

    def _ev_down(self, e, btn):
        if not self._client:
            return
        x, y = self._map(e.x, e.y)
        self._client.send_event({"kind": "mouse_down", "x": x, "y": y, "button": btn})

    def _ev_up(self, e, btn):
        if not self._client:
            return
        x, y = self._map(e.x, e.y)
        self._client.send_event({"kind": "mouse_up", "x": x, "y": y, "button": btn})

    def _ev_dblclick(self, e):
        if not self._client:
            return
        x, y = self._map(e.x, e.y)
        self._client.send_event({"kind": "mouse_click", "x": x, "y": y,
                                  "button": "left", "double": True})

    def _ev_scroll(self, e):
        if not self._client:
            return
        x, y = self._map(e.x, e.y)
        if hasattr(e, "delta") and e.delta:
            delta = 3 if e.delta > 0 else -3
        elif e.num == 4:
            delta = 3
        else:
            delta = -3
        self._client.send_event({"kind": "mouse_scroll", "x": x, "y": y, "delta": delta})

    def _ev_key(self, e):
        if not self._client:
            return
        if e.char and e.char.isprintable() and len(e.char) == 1:
            self._client.send_event({"kind": "key_type", "char": e.char})
        else:
            km = {
                "Return": "enter", "BackSpace": "backspace", "Tab": "tab",
                "Escape": "escape", "Delete": "delete", "space": "space",
                "Left": "left", "Right": "right", "Up": "up", "Down": "down",
                "Home": "home", "End": "end", "Prior": "pageup", "Next": "pagedown",
                "F1": "f1",  "F2": "f2",  "F3": "f3",  "F4": "f4",
                "F5": "f5",  "F6": "f6",  "F7": "f7",  "F8": "f8",
                "F9": "f9",  "F10": "f10", "F11": "f11", "F12": "f12",
            }
            k = km.get(e.keysym)
            if k:
                self._client.send_event({"kind": "key_press", "key": k})

    def _start_frame_poll(self):
        self.root.after(16, self._poll_frames)

    def _poll_frames(self):
        if self._mode != "view" or not self._client:
            return
        try:
            data = self._client._frame_queue.get_nowait()
            self._render_frame(data)
        except queue.Empty:
            pass
        self.root.after(16, self._poll_frames)

    def _render_frame(self, data):
        if self._mode != "view" or not hasattr(self, "canvas"):
            return
        try:
            cw = self._view_w if self._view_w > 0 else DISPLAY_W
            ch = self._view_h if self._view_h > 0 else DISPLAY_H
            img   = Image.open(io.BytesIO(data))
            img   = img.resize((cw, ch), Image.BILINEAR)
            photo = ImageTk.PhotoImage(img)
            img.close()
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=photo)
            self._current_frame_photo = photo
        except Exception:
            pass

    def _on_remote_disconnect(self):
        self.root.after(0, self._handle_remote_disconnect)

    def _handle_remote_disconnect(self):
        cli           = self._client
        self._client  = None
        self._mode    = "home"
        if cli:
            cli.on_disconnect = None
        messagebox.showinfo("已断开", "远端已断开连接")
        self._build_home()

    def _disconnect_and_home(self):
        cli           = self._client
        self._client  = None
        self._mode    = "home"
        if cli:
            cli.on_disconnect = None
            cli.disconnect()
        self._build_home()

    def _on_close_view(self):
        cli           = self._client
        self._client  = None
        self._mode    = "home"
        if cli:
            cli.on_disconnect = None
            cli.disconnect()
        self.root.destroy()

    def _on_close_home(self):
        if self._server:
            self._server.stop()
            self._server = None
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
