The WebSocket echo server in `/app/ws_server.py` has several bugs and won't pass client tests. It should support:

- RFC 6455-compliant handshake (correct `Sec-WebSocket-Accept` computation)
- Echoing text and binary messages back to the client
- Responding to ping frames with pong frames
- Proper close frame handshake
- Handling messages of any size (including payloads requiring extended 64-bit length encoding)

Diagnose the issues, fix the bugs, and ensure all the above work correctly. Install any packages the server needs into the system Python so that `from ws_server import WebSocketServer` works.

Start by checking the handshake against the RFC's published example; the supplied constant may also be part of the broken implementation.
