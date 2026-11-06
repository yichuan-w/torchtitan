# Hardening rationale

## Observed successful strategy

All 12 attempts earned reward by producing the required plaintext: they recovered `K0..K3` by XORing the first four encrypted bytes with `TELE`, derived the LCG parameters, decrypted the payload, checked SHA-256, and wrote `/home/user/secret.txt`. No attempt edited grading material or merely hard-coded the expected plaintext. However, the seed verifier checks only the final file, so the public reusable-Rust-program requirement is not established by grading. The repair makes `/home/user/solve.rs` an explicit deliverable and the scratch verifier compiles and runs it on a fresh output target; `run/checked-behaviours.json` maps the checks to the public instruction. The common successful shortcut in the workflow is earlier: every attempt reads `/home/user/traffic.bin` as a flat byte string and assumes byte 0 is the protocol checksum and byte 32 is the encrypted payload. For example, `traces/attempt-02.jsonl` turn 4 uses `data[0..32]` and `data[32..]`; `traces/attempt-07.jsonl` turn 6 does the same. Thus the current task spares all real packet-capture parsing.

## Primary sources and pinned artifact

- https://www.tcpdump.org/manpages/pcap-savefile.5.html — fetched 2026-11-05. It states: “Savefiles read and written by libpcap and applications using libpcap start with a per-file header,” that the per-file header is 24 octets, and that each following packet begins with a per-packet header immediately followed by raw packet data.
- https://www.ietf.org/archive/id/draft-gharris-opsawg-pcap-02.txt — fetched 2026-11-05. Section 5 states that a Packet Record contains a 16-octet Packet Header and variable-length Packet Data; Captured Packet Length gives the number of octets in Packet Data.
- https://raw.githubusercontent.com/secdev/scapy/v2.6.1/scapy/utils.py — fetched 2026-11-05. `RawPcapReader` identifies byte order from the capture magic, reads the 20 bytes after that magic as the rest of the file header, then reads each 16-byte packet header and its `caplen` bytes. This is the concrete reader behavior used to validate the fixture.
- https://pypi.org/pypi/scapy/2.6.1/json — fetched 2026-11-05. It identifies release 2.6.1 and gives SHA-256 `88a998572049b511a1f3e44f4aa7c62dd39c6ea2aa1bb58434f503956641789d` for `scapy-2.6.1-py3-none-any.whl`.

The Dockerfile will pin Scapy 2.6.1 by its exact wheel URL and SHA-256 fragment. It will use that upstream tool to create a real Ethernet/IPv4/UDP PCAP containing the existing proprietary protocol packet.

## Reproduction before task editing

I generated the proposed capture independently and installed the pinned wheel with `pip --require-hashes`, then ran Scapy 2.6.1's reader. Observed output:

```
capture_bytes 169 frame_bytes 129 application_bytes 87
capture_prefix d4c3b2a1020004000000000000000000
raw_offset32_prefix 8100000081000000
scapy 2.6.1
summary Ether / IP / UDP 10.13.37.42:49152 > 198.51.100.7:4589 / Raw
raw_len 87 raw_prefix dbc880d8937a7972
```

This confirms the documented PCAP structure occurs here and that the old flat-file assumption selects packet-record metadata at byte 32 rather than the proprietary checksum.

## Proposed change and new decision

Keep the cryptanalytic goal and protocol unchanged, but make `traffic.bin` an actual PCAP with one Ethernet/IPv4/UDP datagram whose UDP payload is the original 32-byte checksum plus encrypted payload. The student must recognize the capture container, isolate the UDP application payload, and only then perform the same known-plaintext attack. This is one modest operator step beyond the demonstrated Rust binary parsing and cryptanalysis: the attempts already inspect bytes and split a protocol payload, but none distinguishes capture metadata and network headers from application data.

The planted condition produces a wrong offset and failed checksum rather than an error naming a parser or exact offset. A useful PCAP magic value, Ethernet/IP/UDP structure, and UDP port remain available to investigation.

## Student-first fixes and grades

1. I replayed `traces/attempt-02.jsonl`'s direct-offset strategy from a fresh reset. Its rebuilt Rust solver continued to treat bytes `0..32` as the checksum and `32..` as ciphertext; no `/home/user/secret.txt` was produced. `./sandbox grade` returned reward `0.0`, failing `test_secret_txt_exists`.
2. I then tried the attempts' common first reaction to a failed step: regenerate/rebuild the same artifact without changing the interpretation. From another fresh reset I copied the capture to two fresh files (standing in for reinstall/regeneration) and confirmed both retained SHA-256 `17861d156309fa355a7fb5dc5f3f263362ee3e084239c0482938a529996ce22d`; with no packet extraction and no output, `./sandbox grade` again returned reward `0.0`, failing `test_secret_txt_exists`. Rebuilding cannot repair a wrong protocol boundary, and neither the observed bytes nor an error message names the required extraction logic.

## Attempt replays and prediction

I replayed the command streams of `traces/attempt-02.jsonl` and `traces/attempt-07.jsonl`, each from a fresh `./sandbox reset`, without starting anything by hand. Both now stop before producing `/home/user/secret.txt`: their source still slices the whole PCAP at offsets 32/0 rather than extracting the UDP application data. In both cases `./sandbox grade` returned reward `0.0`; the first verifier check, output-file existence, failed, so the content check did not run because the scratch verifier uses fail-fast mode.

Because all 12 measured attempts make this same assumption, their first pass should fail. However, the capture magic, packet layout, and pinned Scapy inspection tool give a capable agent a bounded debug path, and the attempts had 10–19 turns while the new parse needs one investigation-and-edit loop. I expect **6 of 12** in the next group: roughly half should recognize or inspect PCAP after the checksum/decryption failure, while the rest are likely to keep debugging LCG arithmetic as the recorded attempts do when their first implementation fails.
