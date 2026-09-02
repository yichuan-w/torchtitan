#!/usr/bin/env python3
"""Can we read a sandbox's own cgroup counters? Everything else depends on it.

Fzz1/Tmax-Tasks-Clean's ram numbers come from /sys/fs/cgroup/memory.peak plus
memory.current sampled during the agent phase. Reproducing that for the TW half
needs the same files readable from inside a Daytona sandbox we booted.
"""
import asyncio
import sys

import solve_daytona as sd

PROBE = (
    "for f in memory.peak memory.current memory.max cpu.stat cpu.max "
    "io.stat pids.current; do "
    "printf '%s = ' $f; cat /sys/fs/cgroup/$f 2>&1 | head -3 | tr '\\n' ' '; "
    "echo; done; "
    "echo 'cgroup version:'; stat -fc %T /sys/fs/cgroup 2>&1; "
    "echo 'writable?'; (echo 0 > /sys/fs/cgroup/memory.peak && echo yes || echo no) 2>&1"
)


async def main() -> None:
    tid = sys.argv[1] if len(sys.argv) > 1 else "tw_648569"
    src = sd.resolve_src(tid)
    if src is None:
        print(f"{tid}: no pool dir")
        return
    md = sd.pack.to_row(str(src))["metadata"]
    print(f"booting {tid} ...")
    async with sd.boot_agent_sandbox(
        md.get("image") or "", dockerfile=md.get("dockerfile") or None,
        build_context=md.get("build_context") or None,
        install_claude=False, disk_gb=10,
    ) as sandbox:
        sb = sd.dr._Root(sandbox)
        rc, out, err = await sb.exec(PROBE, check=False, timeout=120)
        print(f"rc={rc}\n{out}\n{err or ''}")


if __name__ == "__main__":
    asyncio.run(main())
