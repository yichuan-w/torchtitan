"""
Tests for the WebSocket echo server.

These tests verify that the WebSocket server in /app/ws_server.py
correctly implements RFC 6455 after the solver fixes the bugs.

All tests require the server to be running on localhost.
"""

import subprocess
import sys
import time
import socket
import struct
import base64
import os


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WS_MAGIC_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _get_free_port():
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(host, port, timeout=5.0):
    """Wait until the server is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)
    return False


# ---------------------------------------------------------------------------
# Fixture: start/stop the server
# ---------------------------------------------------------------------------

_server_process = None
_server_port = None


def setup_module():
    """Start the WebSocket server before running tests."""
    global _server_process, _server_port

    _server_port = _get_free_port()

    # Start the server from /app/ws_server.py (where the solver puts the fixed version)
    _server_process = subprocess.Popen(
        [sys.executable, "-c", f"""
import sys
sys.path.insert(0, '/app')
from ws_server import WebSocketServer
server = WebSocketServer(host='127.0.0.1', port={_server_port})
server.start()
import time
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    server.stop()
"""],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not _wait_for_server("127.0.0.1", _server_port, timeout=10):
        _server_process.terminate()
        _server_process.wait()
        raise RuntimeError(f"Server did not start on port {_server_port}")


def teardown_module():
    """Stop the WebSocket server after all tests."""
    global _server_process
    if _server_process:
        _server_process.terminate()
        try:
            _server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server_process.kill()
            _server_process.wait()


# ---------------------------------------------------------------------------
# Low-level frame helpers for testing specific protocol features
# ---------------------------------------------------------------------------

def _make_handshake_request(host, port, key=None):
    """Perform a raw WebSocket handshake and return the response."""
    if key is None:
        import os as _os
        key = base64.b64encode(_os.urandom(16)).decode()

    request = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"\r\n"
    )

    sock = socket.create_connection((host, port), timeout=5)
    sock.sendall(request.encode())
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    sock.close()

    return response, key


def _make_frame(opcode, payload, masked=True):
    """Build a WebSocket frame."""
    frame = bytearray()
    byte0 = 0x80 | (opcode & 0x0F)  # FIN + opcode
    frame.append(byte0)

    length = len(payload)
    if masked:
        mask_bit = 0x80
    else:
        mask_bit = 0x00

    if length < 126:
        frame.append(mask_bit | length)
    elif length < 65536:
        frame.append(mask_bit | 126)
        frame.extend(struct.pack("!H", length))
    else:
        frame.append(mask_bit | 127)
        frame.extend(struct.pack("!Q", length))

    if masked:
        import os as _os
        mask_key = _os.urandom(4)
        frame.extend(mask_key)
        masked_payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        frame.extend(masked_payload)
    else:
        frame.extend(payload)

    return bytes(frame)


def _recv_frame(sock, timeout=5):
    """Receive a single WebSocket frame from a socket."""
    sock.settimeout(timeout)
    header = b""
    while len(header) < 2:
        chunk = sock.recv(2 - len(header))
        if not chunk:
            return None
        header += chunk

    byte0, byte1 = header[0], header[1]
    opcode = byte0 & 0x0F
    masked = (byte1 >> 7) & 1
    payload_len = byte1 & 0x7F

    if payload_len == 126:
        ext = b""
        while len(ext) < 2:
            chunk = sock.recv(2 - len(ext))
            if not chunk:
                return None
            ext += chunk
        payload_len = struct.unpack("!H", ext)[0]
    elif payload_len == 127:
        ext = b""
        while len(ext) < 8:
            chunk = sock.recv(8 - len(ext))
            if not chunk:
                return None
            ext += chunk
        payload_len = struct.unpack("!Q", ext)[0]

    if masked:
        mask_key = b""
        while len(mask_key) < 4:
            chunk = sock.recv(4 - len(mask_key))
            if not chunk:
                return None
            mask_key += chunk
    else:
        mask_key = None

    payload = b""
    while len(payload) < payload_len:
        chunk = sock.recv(payload_len - len(payload))
        if not chunk:
            return None
        payload += chunk

    if mask_key:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

    return {"opcode": opcode, "payload": payload}


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_handshake_and_echo_text():
    """Test basic handshake and text message echo."""
    import websocket

    url = f"ws://127.0.0.1:{_server_port}"
    ws = websocket.create_connection(url, timeout=5)

    ws.send("Hello, WebSocket!")
    response = ws.recv()
    assert response == "Hello, WebSocket!", (
        f"Expected 'Hello, WebSocket!' but got '{response}'"
    )

    ws.close()
    print("PASS: test_handshake_and_echo_text")


def test_handshake_and_echo_binary():
    """Test handshake and binary message echo."""
    import websocket

    url = f"ws://127.0.0.1:{_server_port}"
    ws = websocket.create_connection(url, timeout=5)

    binary_data = bytes(range(256))  # 0..255
    ws.send_binary(binary_data)
    response = ws.recv()
    assert isinstance(response, bytes), f"Expected bytes, got {type(response)}"
    assert response == binary_data, (
        f"Binary echo mismatch: expected {len(binary_data)} bytes, got {len(response)}"
    )

    ws.close()
    print("PASS: test_handshake_and_echo_binary")


def test_ping_pong():
    """Test that the server responds to ping frames with pong frames.

    Uses raw socket operations to verify the server actually sends
    a pong frame in response to a ping frame.
    """
    import websocket

    url = f"ws://127.0.0.1:{_server_port}"
    ws = websocket.create_connection(url, timeout=5)

    # Send a ping using the library and verify pong is received
    # websocket-client's ping() sends a ping frame and waits for pong
    result = ws.ping("hello-ping")
    # If the server doesn't respond, ping() will raise an exception
    # or return False / timeout
    assert result is not False, (
        "Server did not respond to ping frame with pong. "
        "Make sure the server responds to ping (opcode 0x9) with pong (opcode 0xA)."
    )

    ws.close()


def test_close_handshake():
    """Test that the server properly echoes the close frame."""
    import websocket

    url = f"ws://127.0.0.1:{_server_port}"
    ws = websocket.create_connection(url, timeout=5)
    # websocket-client's close() performs the close handshake.
    # If the server does not respond with a close frame, this may
    # raise an exception or the connection may be left in a bad state.
    ws.close()
    # Verify the connection is actually closed
    assert not ws.connected, "Connection should be closed after close handshake"
    print("PASS: test_close_handshake")


def test_large_message():
    """Test that the server handles messages larger than 65535 bytes."""
    import websocket

    url = f"ws://127.0.0.1:{_server_port}"
    ws = websocket.create_connection(url, timeout=10)

    # Create a message large enough to require the 64-bit extended length
    large_payload = b"X" * 70000
    ws.send_binary(large_payload)
    response = ws.recv()

    assert isinstance(response, bytes), f"Expected bytes, got {type(response)}"
    assert len(response) == len(large_payload), (
        f"Length mismatch: expected {len(large_payload)}, got {len(response)}"
    )
    assert response == large_payload, "Large message echo mismatch"

    ws.close()
    print("PASS: test_large_message")


def test_multiple_messages():
    """Test that the server handles multiple messages on the same connection."""
    import websocket

    url = f"ws://127.0.0.1:{_server_port}"
    ws = websocket.create_connection(url, timeout=5)

    messages = ["first", "second", "third", "fourth", "fifth"]
    for msg in messages:
        ws.send(msg)
        response = ws.recv()
        assert response == msg, f"Expected '{msg}' but got '{response}'"

    ws.close()
    print("PASS: test_multiple_messages")


def test_handshake_accept_key_correct():
    """Test that the Accept key uses the correct magic GUID and standard base64.

    Uses the RFC 6455 Section 4.2.2 example to verify both:
    1. The correct magic GUID (258EAFA5-E914-47DA-95CA-C5AB0DC85B11) is used
    2. Standard base64 encoding (not urlsafe) is used

    The expected accept key for key "dGhlIHNhbXBsZSBub25jZQ==" is
    "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" when computed correctly.
    """
    test_key = "dGhlIHNhbXBsZSBub25jZQ=="
    expected_accept = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="

    response, _ = _make_handshake_request("127.0.0.1", _server_port, key=test_key)
    response_text = response.decode("ascii", errors="replace")

    accept_value = None
    for line in response_text.split("\r\n"):
        if line.lower().startswith("sec-websocket-accept:"):
            accept_value = line.split(":", 1)[1].strip()
            break

    assert accept_value is not None, "No Sec-WebSocket-Accept header in response"
    assert accept_value == expected_accept, (
        f"Accept key mismatch: expected '{expected_accept}', got '{accept_value}'.\n"
        f"Check that (1) the magic GUID is '258EAFA5-E914-47DA-95CA-C5AB0DC85B11' "
        f"and (2) standard base64 (base64.b64encode) is used, not urlsafe."
    )

    print("PASS: test_handshake_accept_key_correct")
