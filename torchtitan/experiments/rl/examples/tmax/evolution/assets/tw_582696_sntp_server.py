"""Stand in for the public NTP server this task used to query.

The sandbox lets nothing out on UDP except port 53, so no public time server was
ever reachable from here and the verifier always fell through to its own clock.
That made `date > /app/result.txt` a full-marks answer to a task about speaking
SNTP, which is worse for training than a task that simply fails.

Serving a clock deliberately offset from the sandbox's own removes that: reading
the local time now lands 14 days away from the answer, and only a client that
parses the reply and converts NTP epoch to Unix epoch can pass. SKEW has to
match the one in tests/test_state.py.
"""

import socket
import struct
import time

SKEW = 1234567
NTP_EPOCH = 2208988800

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 123))
while True:
    try:
        _, addr = sock.recvfrom(48)
    except OSError:
        continue
    stamp = int(time.time()) + SKEW + NTP_EPOCH
    packet = bytearray(48)
    packet[0] = 0x24  # LI=0, VN=4, mode=4 (server)
    packet[1] = 1     # stratum
    packet[2] = 4     # poll
    packet[3] = 0xEC  # precision
    packet[12:16] = b"LOCL"
    for off in (24, 32, 40):  # reference, receive, transmit timestamps
        packet[off:off + 4] = struct.pack("!I", stamp)
    sock.sendto(bytes(packet), addr)
