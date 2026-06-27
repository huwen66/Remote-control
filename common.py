import struct
import json

MSG_AUTH = "AUTH"
MSG_AUTH_OK = "AUTH_OK"
MSG_AUTH_FAIL = "AUTH_FAIL"
MSG_FRAME = "FRAME"
MSG_EVENT = "EVENT"
MSG_DISCONNECT = "DISCONNECT"
MSG_SCREEN_SIZE = "SCREEN_SIZE"

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
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data

def encode_event(event_dict: dict) -> bytes:
    return json.dumps(event_dict).encode("utf-8")

def decode_event(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))
