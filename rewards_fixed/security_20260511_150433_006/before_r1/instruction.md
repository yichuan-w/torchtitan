A web hosting company has provided you with a forensic disk image from one of their compromised Linux servers. The server was running an e-commerce platform before unusual network activity was detected by their monitoring system. They immediately isolated the server and created a disk image for forensic analysis.

Your objective is to investigate the compromise and produce a comprehensive incident report. Specifically, you need to:

1. **Mount and analyze** the disk image located at `/app/evidence/server.img`
2. **Identify the initial attack vector** - how did the attacker gain access?
3. **Discover all persistence mechanisms** - what backdoors or scheduled tasks were installed?
4. **Reconstruct the attack timeline** - when did key events occur?
5. **Recover the exfiltration target** - the attacker staged sensitive credentials for extraction. You must find and recover the actual secret value.

The disk image contains a full Linux filesystem. Use standard forensic tools and techniques to examine the artifacts without booting the image.

Write your findings to `/app/incident_report.txt` with the following format:

```
ATTACK_VECTOR: <brief description of how access was gained>
PERSISTENCE: <comma-separated list of persistence mechanisms found>
TIMESTAMP: <YYYY-MM-DD HH:MM:SS of initial compromise>
EXFIL_TARGET: <the actual secret value that was staged for exfiltration>
```

The exfiltration target should contain the recovered password value that was being extracted.

The staged credential strings may not be the final secret; correlate them with cryptographic artifacts in the image and recover plaintext before choosing `EXFIL_TARGET`.
