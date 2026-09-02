#!/usr/bin/env python3
"""How much of the measured memory floor is the measuring tool's own?

resources.jsonl was collected with codex installed in the sandbox: a 99MB binary
whose pages sit in the cgroup's page cache and count toward memory.current. The
trained policy runs terminus, whose loop lives in harbor on the HOST -- inside
the sandbox it is tmux and a shell. So the floor those measurements carry is not
the floor training pays, and sizing memory from peak_ram_mb over-provisions by
whatever the difference is.

Three readings on one sandbox: bare boot, after tmux, after codex.
"""
import asyncio
import sys

import solve_daytona as sd

READ = ("echo cur=$(cat /sys/fs/cgroup/memory.current); "
        "echo peak=$(cat /sys/fs/cgroup/memory.peak)")


async def main() -> None:
    tid = sys.argv[1] if len(sys.argv) > 1 else "tw_100135"
    md = sd.pack.to_row(str(sd.resolve_src(tid)))["metadata"]
    async with sd.boot_agent_sandbox(
        md.get("image") or "", dockerfile=md.get("dockerfile") or None,
        build_context=md.get("build_context") or None,
        install_claude=False, cpu=4, memory=8, disk_gb=10,
    ) as sandbox:
        sb = sd.dr._Root(sandbox)

        async def read(label: str) -> None:
            _, out, _ = await sb.exec(READ, check=False, timeout=120)
            vals = {}
            for line in (out or "").splitlines():
                k, _, v = line.partition("=")
                if v.strip().isdigit():
                    vals[k.strip()] = int(v) / 1048576
            print(f"  {label:<24} current={vals.get('cur', 0):7.1f} MB   "
                  f"peak={vals.get('peak', 0):7.1f} MB")

        await read("bare boot")
        # What training actually needs in-sandbox: harbor drives tmux from the
        # host, so tmux (and the shell it spawns) is the whole footprint.
        rc, out, err = await sb.exec(
            "command -v tmux >/dev/null 2>&1 || "
            "(apt-get update -qq && apt-get install -y -qq tmux) >/dev/null 2>&1; "
            "tmux new-session -d -s probe 2>/dev/null; sleep 2; "
            "tmux list-sessions 2>&1 | head -1",
            check=False, timeout=420)
        print(f"  [tmux setup rc={rc}] {(out or '').strip()[:60]}")
        await read("after tmux session")
        # And what the measurement run carried on top of that.
        py_prog = ("import urllib.request; urllib.request.urlretrieve("
                   f"{sd.CODEX_URL!r}, '/tmp/cx.tgz')")
        await sb.exec(
            "test -x /usr/local/bin/codex || { "
            f"( command -v curl >/dev/null 2>&1 && curl -fsSL {sd.CODEX_URL} -o /tmp/cx.tgz ) || "
            f'( PYBIN=$(command -v python3 || command -v python); "$PYBIN" -c {sd.shlex.quote(py_prog)} ); '
            "tar -xzf /tmp/cx.tgz -C /tmp && "
            "mv /tmp/codex-x86_64-unknown-linux-musl /usr/local/bin/codex && "
            "chmod +x /usr/local/bin/codex; }",
            check=False, timeout=420)
        await read("after codex install")


if __name__ == "__main__":
    asyncio.run(main())
