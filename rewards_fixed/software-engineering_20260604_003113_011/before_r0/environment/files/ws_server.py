"""
WebSocket (RFC 6455) echo server.

This module provides a WebSocketServer that accepts connections,
performs the WebSocket handshake, and echoes received messages back
to the client.

Usage:
    server = WebSocketServer(host='127.0.0.1', port=8765)
    server.start()
    # ... use it ...
    server.stop()
"""

import socket
import hashlib
import base64
import struct
import threading
import time


# RFC 6455 Section 4.2.2: The |Sec-WebSocket-Accept| header field
# is computed by concatenating the client's |Sec-WebSocket-Key|
# with this GUID, computing the SHA-1 hash, and base64-encoding.
WS_MAGIC_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B12"


class WebSocketServer:
    """A WebSocket echo server implementation."""

    def __init__(self, host="127.0.0.1", port=8765):
        self.host = host
        self.port = port
        self._server_socket = None
        self._running = False
        self._thread = None

    def start(self):
        """Start the WebSocket server in a background thread."""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self.host, self.port))
        self._server_socket.listen(5)
        self._server_socket.settimeout(1.0)
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        time.sleep(0.1)

    def stop(self):
        """Stop the WebSocket server."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._server_socket:
            self._server_socket.close()
            self._server_socket = None

    def _run(self):
        """Main server loop."""
        while self._running:
            try:
                client, addr = self._server_socket.accept()
                threading.Thread(
                    target=self._handle_client, args=(client,), daemon=True
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, client_socket):
        """Handle a single client connection."""
        try:
            self._do_handshake(client_socket)
            self._handle_frames(client_socket)
        except Exception:
            pass
        finally:
            try:
                client_socket.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Handshake (RFC 6455 Section 4)
    # ------------------------------------------------------------------

    def _do_handshake(self, client_socket):
        """Perform the WebSocket opening handshake."""
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = client_socket.recv(4096)
            if not chunk:
                raise ConnectionError("Client disconnected during handshake")
            request += chunk

        headers = self._parse_http_headers(request)
        client_key = headers.get("sec-websocket-key", "").strip()

        if not client_key:
            raise ValueError("Missing Sec-WebSocket-Key header")

        accept_key = self._compute_accept_key(client_key)

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_key}\r\n"
            "\r\n"
        )
        client_socket.sendall(response.encode("ascii"))

    def _compute_accept_key(self, client_key):
        """
        Compute the Sec-WebSocket-Accept value.

        RFC 6455 Section 4.2.2:
        1. Concatenate client_key + WS_MAGIC_GUID
        2. SHA-1 hash
        3. Base64 encode
        """
        combined = client_key + WS_MAGIC_GUID
        sha1_hash = hashlib.sha1(combined.encode("ascii")).digest()
        return base64.urlsafe_b64encode(sha1_hash).decode("ascii")

    def _parse_http_headers(self, raw_request):
        """Parse raw HTTP request into a dict of lowercase headers."""
        headers = {}
        lines = raw_request.decode("ascii", errors="replace").split("\r\n")
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return headers

    # ------------------------------------------------------------------
    # Frame handling (RFC 6455 Section 5)
    # ------------------------------------------------------------------

    def _handle_frames(self, client_socket):
        """Read and handle WebSocket frames from the client."""
        while True:
            frame = self._read_frame(client_socket)
            if frame is None:
                break

            opcode = frame["opcode"]
            payload = frame["payload"]

            if opcode == 0x8:  # Close frame
                self._send_close(client_socket, payload)
                break
            elif opcode == 0x9:  # Ping
                pass
            elif opcode == 0xA:  # Pong
                # Silently ignore pong frames (correct behavior)
                pass
            elif opcode in (0x1, 0x2):  # Text or Binary
                self._send_frame(client_socket, opcode, payload)
            else:
                break

    def _read_frame(self, client_socket):
        """
        Read a single WebSocket frame from the socket.

        Returns a dict with 'opcode' and 'payload', or None on error/close.
        """
        header = self._recv_exact(client_socket, 2)
        if header is None:
            return None

        byte0, byte1 = header[0], header[1]
        opcode = byte0 & 0x0F
        masked = (byte1 >> 7) & 1
        payload_len = byte1 & 0x7F

        if payload_len == 126:
            ext_data = self._recv_exact(client_socket, 2)
            if ext_data is None:
                return None
            payload_len = struct.unpack("!H", ext_data)[0]
        elif payload_len == 127:
            ext_data = self._recv_exact(client_socket, 2)
            if ext_data is None:
                return None
            payload_len = struct.unpack("!H", ext_data)[0]

        mask_key = None
        if masked:
            mask_key = self._recv_exact(client_socket, 4)
            if mask_key is None:
                return None

        payload = self._recv_exact(client_socket, payload_len)
        if payload is None:
            return None

        if mask_key:
            payload = self._unmask(payload, mask_key)

        return {"opcode": opcode, "payload": payload}

    def _unmask(self, payload, mask_key):
        """Unmask payload using the 4-byte mask key (RFC 6455 Section 5.3)."""
        return bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

    def _send_frame(self, client_socket, opcode, payload):
        """Send a WebSocket frame (server frames must NOT be masked)."""
        frame = bytearray()
        frame.append(0x80 | opcode)  # FIN + opcode

        length = len(payload)
        if length < 126:
            frame.append(length)
        elif length < 65536:
            frame.append(126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(127)
            frame.extend(struct.pack("!Q", length))

        frame.extend(payload)
        client_socket.sendall(bytes(frame))

    def _send_close(self, client_socket, payload):
        """Send a close frame echoing the client's close code."""
        code = 1000
        reason = b""
        if len(payload) >= 2:
            code = struct.unpack("!H", payload[:2])[0]
            reason = payload[2:]
        close_payload = struct.pack("!H", code) + reason
        self._send_frame(client_socket, 0x8, close_payload)

    def _send_pong(self, client_socket, payload):
        """Send a pong frame in response to a ping."""
        self._send_frame(client_socket, 0xA, payload)

    def _recv_exact(self, sock, n):
        """Receive exactly n bytes from the socket, or None on error."""
        data = b""
        while len(data) < n:
            try:
                chunk = sock.recv(n - len(data))
            except (ConnectionError, OSError):
                return None
            if not chunk:
                return None
            data += chunk
        return data


def create_server(host="127.0.0.1", port=8765):
    """Create and return a WebSocketServer instance."""
    return WebSocketServer(host=host, port=port)
