import hashlib

from scapy.all import Ether, IP, UDP, Raw, wrpcap

K0 = 42
A = 17
C = 105
plaintext = b"TELE_CRITICAL_SYSTEM_ALERT_FLAG{lcg_stream_broken_1337}"

checksum = hashlib.sha256(plaintext).digest()
encrypted = bytearray()
state = K0
for byte in plaintext:
    encrypted.append(byte ^ state)
    state = (A * state + C) % 256

packet = (
    Ether(src="02:00:00:00:00:2a", dst="02:00:00:00:00:07")
    / IP(src="10.13.37.42", dst="198.51.100.7", id=0x1337)
    / UDP(sport=49152, dport=4589)
    / Raw(checksum + encrypted)
)
packet.time = 1700000000.123456
wrpcap("/home/user/traffic.bin", [packet])
