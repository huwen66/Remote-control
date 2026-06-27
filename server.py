import socket
import threading
import io
import time
import tkinter as tk
from tkinter import ttk
import sys
import os

if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
else:
    _base = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _base)
from common import (
    send_msg, recv_msg, decode_event,
    MSG_AUTH, MSG_AUTH_OK, MSG_AUTH_FAIL,
    MSG_FRAME, MSG_EVENT, MSG_DISCONNECT, MSG_SCREEN_SIZE
)

DEFAULT_PORT = 5900
DEFAULT_PASSWORD = "demo1234"
JPEG_QUALITY = 50
FRAME_INTERVAL = 0.08

_pyautogui = None
_grab_screen = None

def _lazy_init():
    global _pyautogui, _grab_screen
    if _pyautogui is None:
        import pyautogui as _pag
        _pag.FAILSAFE = False
        _pyautogui = _pag
    if _grab_screen is None:
        _grab_screen = _build_grab_fn()

def _build_grab_fn():
    import platform
    if platform.system() != "Darwin":
        from PIL import ImageGrab
        return lambda: ImageGrab.grab()

    try:
        import Quartz
        from PIL import Image

        def _quartz_grab():
            region = Quartz.CGRectInfinite
            img_ref = Quartz.CGWindowListCreateImage(
                region,
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
                Quartz.kCGWindowImageDefault
            )
            if img_ref is None:
                return None
            width = Quartz.CGImageGetWidth(img_ref)
            height = Quartz.CGImageGetHeight(img_ref)
            bpp = Quartz.CGImageGetBitsPerPixel(img_ref)
            bpr = Quartz.CGImageGetBytesPerRow(img_ref)
            data_provider = Quartz.CGImageGetDataProvider(img_ref)
            raw = Quartz.CGDataProviderCopyData(data_provider)
            mode = "RGBA" if bpp == 32 else "RGB"
            img = Image.frombytes(mode, (width, height), bytes(raw), "raw", mode, bpr)
            return img.convert("RGB")

        return _quartz_grab
    except Exception:
        import subprocess
        import tempfile
        from PIL import Image

        def _screencapture_grab():
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                tmp = f.name
            try:
                subprocess.run(
                    ["screencapture", "-x", "-t", "png", tmp],
                    check=True, timeout=3,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                return Image.open(tmp).convert("RGB")
            except Exception:
                return None
            finally:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass

        return _screencapture_grab


class RemoteServer:
    def __init__(self, host="0.0.0.0", port=DEFAULT_PORT, password=DEFAULT_PASSWORD):
        self.host = host
        self.port = port
        self.password = password
        self.server_sock = None
        self.client_sock = None
        self.running = False
        self.connected = False
        self.status_var = None
        self.screen_w = 0
        self.screen_h = 0

    def start(self):
        _lazy_init()
        self.screen_w, self.screen_h = _pyautogui.size()
        self.running = True
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(1)
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()

    def _accept_loop(self):
        while self.running:
            try:
                self.server_sock.settimeout(1.0)
                try:
                    conn, addr = self.server_sock.accept()
                except socket.timeout:
                    continue
                self._set_status(f"客户端连接中: {addr[0]}:{addr[1]}")
                self._handle_client(conn, addr)
                self._set_status("等待连接...")
            except Exception:
                if self.running:
                    time.sleep(0.5)

    def _handle_client(self, conn, addr):
        try:
            msg_type, payload = recv_msg(conn)
            if msg_type != MSG_AUTH:
                conn.close()
                return
            if payload.decode("utf-8") != self.password:
                send_msg(conn, MSG_AUTH_FAIL, b"wrong password")
                conn.close()
                self._set_status("认证失败，等待连接...")
                return
            send_msg(conn, MSG_AUTH_OK)
            size_payload = f"{self.screen_w},{self.screen_h}".encode("utf-8")
            send_msg(conn, MSG_SCREEN_SIZE, size_payload)
            self.client_sock = conn
            self.connected = True
            self._set_status(f"已连接: {addr[0]}:{addr[1]}")

            frame_thread = threading.Thread(target=self._send_frames, args=(conn,), daemon=True)
            frame_thread.start()
            self._recv_events(conn)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self.client_sock = None
            self.connected = False

    def _send_frames(self, conn):
        while self.connected:
            try:
                img = _grab_screen()
                if img is None:
                    time.sleep(FRAME_INTERVAL)
                    continue
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=JPEG_QUALITY)
                frame_data = buf.getvalue()
                send_msg(conn, MSG_FRAME, frame_data)
                time.sleep(FRAME_INTERVAL)
            except Exception:
                break

    def _recv_events(self, conn):
        while self.connected:
            try:
                msg_type, payload = recv_msg(conn)
                if msg_type is None or msg_type == MSG_DISCONNECT:
                    break
                if msg_type == MSG_EVENT:
                    event = decode_event(payload)
                    self._execute_event(event)
            except Exception:
                break

    def _execute_event(self, event):
        try:
            kind = event.get("kind")
            if kind == "mouse_move":
                _pyautogui.moveTo(event["x"], event["y"])
            elif kind == "mouse_click":
                btn = event.get("button", "left")
                x, y = event["x"], event["y"]
                if event.get("double"):
                    _pyautogui.doubleClick(x, y, button=btn)
                else:
                    _pyautogui.click(x, y, button=btn)
            elif kind == "mouse_scroll":
                _pyautogui.moveTo(event["x"], event["y"])
                _pyautogui.scroll(event.get("delta", 1))
            elif kind == "key_press":
                key = event.get("key", "")
                if key:
                    _pyautogui.press(key)
            elif kind == "key_type":
                ch = event.get("char", "")
                if ch:
                    _pyautogui.typewrite(ch, interval=0)
        except Exception:
            pass

    def _set_status(self, text):
        if self.status_var:
            self.status_var.set(text)

    def stop(self):
        self.running = False
        self.connected = False
        if self.client_sock:
            try:
                self.client_sock.close()
            except Exception:
                pass
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    root = tk.Tk()
    root.title("远程控制 - 服务端（被控端）")
    root.geometry("420x320")
    root.resizable(False, False)
    root.configure(bg="#1e1e2e")
    root.lift()
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))
    root.focus_force()

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("TLabel", background="#1e1e2e", foreground="#cdd6f4", font=("Helvetica", 12))
    style.configure("Title.TLabel", background="#1e1e2e", foreground="#89b4fa", font=("Helvetica", 16, "bold"))
    style.configure("Status.TLabel", background="#313244", foreground="#a6e3a1", font=("Helvetica", 11))
    style.configure("TButton", font=("Helvetica", 11), padding=6)

    port_var = tk.StringVar(value=str(DEFAULT_PORT))
    password_var = tk.StringVar(value=DEFAULT_PASSWORD)
    status_var = tk.StringVar(value="未启动")
    server_ref = [None]

    ttk.Label(root, text="远程桌面控制 - 服务端", style="Title.TLabel").pack(pady=(18, 8))

    info_frame = tk.Frame(root, bg="#313244", bd=0)
    info_frame.pack(fill="x", padx=24, pady=4)

    def make_row(parent, label_text, value_text):
        row = tk.Frame(parent, bg="#313244")
        row.pack(fill="x", padx=12, pady=3)
        tk.Label(row, text=label_text, bg="#313244", fg="#89dceb", font=("Helvetica", 11), width=10, anchor="w").pack(side="left")
        tk.Label(row, text=value_text, bg="#313244", fg="#cdd6f4", font=("Helvetica", 11, "bold"), anchor="w").pack(side="left")

    make_row(info_frame, "本机 IP:", get_local_ip())
    make_row(info_frame, "端口:", port_var.get())
    make_row(info_frame, "密码:", password_var.get())

    status_frame = tk.Frame(root, bg="#1e1e2e")
    status_frame.pack(pady=10)
    tk.Label(status_frame, text="状态:", bg="#1e1e2e", fg="#89dceb", font=("Helvetica", 11)).pack(side="left")
    tk.Label(status_frame, textvariable=status_var, bg="#1e1e2e", fg="#a6e3a1", font=("Helvetica", 11, "bold")).pack(side="left", padx=6)

    btn_frame = tk.Frame(root, bg="#1e1e2e")
    btn_frame.pack(pady=8)

    def on_start():
        if server_ref[0] is not None:
            return
        start_btn.config(state="disabled")
        status_var.set("初始化中...")

        def _do_start():
            srv = RemoteServer(port=int(port_var.get()), password=password_var.get())
            srv.status_var = status_var
            srv.start()
            server_ref[0] = srv
            root.after(0, lambda: status_var.set("等待连接..."))
            root.after(0, lambda: stop_btn.config(state="normal"))

        threading.Thread(target=_do_start, daemon=True).start()

    def on_stop():
        if server_ref[0]:
            server_ref[0].stop()
            server_ref[0] = None
        status_var.set("已停止")
        start_btn.config(state="normal")
        stop_btn.config(state="disabled")

    start_btn = tk.Button(btn_frame, text="▶  启动服务", bg="#89b4fa", fg="#1e1e2e",
                          font=("Helvetica", 11, "bold"), relief="flat", padx=16, pady=6,
                          cursor="hand2", command=on_start)
    start_btn.pack(side="left", padx=8)

    stop_btn = tk.Button(btn_frame, text="■  停止服务", bg="#f38ba8", fg="#1e1e2e",
                         font=("Helvetica", 11, "bold"), relief="flat", padx=16, pady=6,
                         cursor="hand2", command=on_stop, state="disabled")
    stop_btn.pack(side="left", padx=8)

    tk.Label(root, text="macOS 需授权「辅助功能」和「屏幕录制」权限", bg="#1e1e2e",
             fg="#6c7086", font=("Helvetica", 9)).pack(pady=(4, 0))

    def on_close():
        on_stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
