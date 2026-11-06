# Hardening rationale

## Attempt evidence and present strategy

All 12 attempts earned reward while carrying out the requested tunnel repair rather than bypassing grading. They inspect `/app/start-tunnel.sh`, create a key and authorize it for `tunnel`, enable `AllowTcpForwarding`, restart `sshd`, and use shorthand remote forwards such as `-R 9090:127.0.0.1:8080`. For example, `traces/attempt-01.jsonl` turns 30–48 identifies the included hardening file, enables forwarding there, and verifies all three endpoints; attempt 04 turns 16–31 follows the same workflow. The seed verifier checks running SSH forwarding processes, endpoint semantics, forwarding configuration, key setup, and preservation of the original services, so these rewarded runs are not reward hacks.

The task currently spares the student any server-side restriction on *which listen addresses and ports* remote forwarding may request. Every successful strategy can enable forwarding globally and submit the familiar host-omitting `-R port:host:hostport` syntax.

## Upstream difficulty researched

- https://www.openssh.com/txt/release-7.8 — OpenSSH 7.8 says: “add a PermitListen directive to sshd_config(5) and a corresponding permitlisten= authorized_keys option that control which listen addresses and port numbers may be used by remote forwarding (ssh -R ...).”
- https://man.openbsd.org/sshd_config.5 — the upstream manual says: “Specifies the addresses/ports on which a remote TCP port forwarding may listen,” and gives distinct `PermitListen port` and `PermitListen host:port` forms. It also says configuration keywords generally use “the first obtained value”.
- https://cdn.openbsd.org/pub/OpenBSD/OpenSSH/portable/openssh-9.6p1.tar.gz — the pinned upstream source archive has SHA-256 `910211c07255a8c5ad654391b40ee59800710dd8119dd5362de09385aa7a777c`. Its `channels.c` `open_listen_match_tcpip()` compares the permitted and requested listen host as well as the port; its `sshd_config.5` documents the two forms above.

The revised image pins the installed OpenSSH implementation to package version `1:9.6p1-3ubuntu13.20` and verifies both the package and runtime versions during build. (The immutable Ubuntu 24.04 base currently provides this build; the upstream 9.6p1 archive above is the primary-source basis for the behavior.) It configures `PermitListen` for the three required loopback listeners. It contains the restriction, not a patch or note naming the client-side fix.

## Reproduction in this environment

On the seed Ubuntu 24.04 image (`OpenSSH_9.6p1 Ubuntu-3ubuntu13.20`), I launched an isolated sshd on port 2222 with `AllowTcpForwarding yes` and `PermitListen 127.0.0.1:9190`, then ran:

```
ssh -p 2222 -N -f -o ExitOnForwardFailure=yes -i /root/.ssh/id_ed25519 -R 9190:127.0.0.1:8080 tunnel@localhost
ssh -p 2222 -N -f -o ExitOnForwardFailure=yes -i /root/.ssh/id_ed25519 -R 127.0.0.1:9190:127.0.0.1:8080 tunnel@localhost
```

The first printed `Error: remote port forwarding failed for listen port 9190`; the second succeeded, `netstat` showed `127.0.0.1:9190` owned by `sshd: tunnel`, and curl returned the dashboard JSON. Thus the behavior is real here, and the failure names remote forwarding but does not disclose the omitted listen-host decision.

## Changed decision

The student must now inspect effective sshd policy and reconcile an allowed loopback listen address with the exact listen address sent by the client. This is one modest OpenSSH configuration/debugging step beyond the traces’ demonstrated ability to discover include precedence and repair `AllowTcpForwarding`; it preserves the same three-tunnel goal and key-authentication workflow.

## Seed setup repair

A fresh sandbox did not honor the instruction’s statement that SSH and the three services were already running: PID 1 was the harness launcher, `tunnel` did not exist, and no service ports listened. Most attempts compensated by manually running `/app/entrypoint.sh` or recreating its setup. The environment will use an entrypoint that performs setup and then executes the harness command, so the promised starting state exists without student action. This is recorded separately in `run/repair.json`.

## Plausible fixes to replay

Pending after the revised image is built:

- Error-led / first trace response: enable or reinstall/regenerate the forwarding setup but retain host-omitting `-R 9090:...` forwards.
- Investigated fix: request each permitted loopback listen address explicitly in `-R 127.0.0.1:9090:...` (and 9091/9092).

Their grades and two unchanged successful-attempt replays will be appended after execution.

## Fix trials and grades

From a fresh revised image I applied the traces' first-line repair: generated and authorized a key, changed both `AllowTcpForwarding no` settings to `yes`, restarted sshd, and retained shorthand `-R 9090:...`, `-R 9091:...`, and `-R 9092:...`. The client printed `Error: remote port forwarding failed for listen port 9090`; no 909x listener existed, and `./sandbox grade` returned reward `0.0`.

Without changing key setup, server policy, or targets, I then made each client request explicit loopback listeners (`-R 127.0.0.1:9090:...`, etc.). All three curls succeeded and `./sandbox grade` returned reward `1.0`. Reinstalling/regenerating is not a distinct promising repair here: the image's client and server already report the pinned 9.6p1 behavior, and regeneration of keys/configuration leaves the address mismatch unchanged.

## Successful-attempt replays

I replayed the joined keystrokes of attempts 01 and 04 separately, each after `./sandbox reset`, without starting anything first. Both returned reward `0.0`.

- Attempt 01 stops after it discovers and enables the included `AllowTcpForwarding` denial: every subsequent host-omitting remote-forward request is rejected. It fails all three tunnel-process checks and all three tunneled endpoint checks; its key, forwarding setting, listener restriction, and original-service checks remain satisfied.
- Attempt 04 overwrites the hardening file while enabling forwarding, thereby removing `PermitListen`, but OpenSSH uses the first global `AllowTcpForwarding no` and its `Match` block comes after the attempted connection workflow in the replay. No tunnel starts. It fails all three process checks, all three endpoint checks, forwarding-enabled inspection, and non-empty key authorization in the final replay state; the required-listener policy check and original-services check pass. The essential changed-path failure is still its unchanged host-omitting `-R` requests, which cannot satisfy the revised policy.

The unchanged attempts therefore no longer pass, while the intended one-decision correction does. For the next same-size group I expect **6 of 12**: the traces already demonstrate strong sshd include/configuration debugging, but none of 12 used an explicit remote listen host and the generic client error does not identify that mismatch.
