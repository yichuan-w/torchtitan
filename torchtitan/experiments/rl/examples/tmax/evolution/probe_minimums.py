#!/usr/bin/env python3
"""What is the smallest sandbox Daytona will actually give us?

The SDK types cpu/memory/disk as whole integers (cores, GiB, GiB), so a task
measured at 207MB can only be given 1 GiB -- there is no finer setting. The docs
state org-level ceilings and no minimums at all, so the floor has to be found by
asking for it. Boots each candidate and reports what the cgroup says it got,
since a request that is silently rounded up is not a minimum.
"""
import asyncio

import solve_daytona as sd

CANDIDATES = [(1, 1, 1), (1, 1, 2), (1, 1, 3), (2, 1, 3), (1, 2, 3)]
READ = ("echo mem_max=$(cat /sys/fs/cgroup/memory.max); "
        "echo cpu_max=$(cat /sys/fs/cgroup/cpu.max); "
        "echo disk_avail=$(df -B1 --output=size / | tail -1); "
        "echo img=$(du -sx --block-size=1 / 2>/dev/null | cut -f1)")


async def try_one(md: dict, cpu: int, mem: int, disk: int) -> None:
    label = f"cpu={cpu} mem={mem}Gi disk={disk}Gi"
    try:
        async with sd.boot_agent_sandbox(
            md.get("image") or "", dockerfile=md.get("dockerfile") or None,
            build_context=md.get("build_context") or None,
            install_claude=False, cpu=cpu, memory=mem, disk_gb=disk,
        ) as sandbox:
            sb = sd.dr._Root(sandbox)
            rc, out, err = await sb.exec(READ, check=False, timeout=120)
            got = {}
            for line in (out or "").splitlines():
                k, _, v = line.partition("=")
                got[k.strip()] = v.strip()
            mm = got.get("mem_max", "?")
            mm_gi = f"{int(mm)/1073741824:.2f}Gi" if mm.isdigit() else mm
            av = got.get("disk_avail", "?")
            av_gi = f"{int(av)/1073741824:.2f}Gi" if av.isdigit() else av
            im = got.get("img", "?")
            im_mb = f"{int(im)/1048576:.0f}MB" if im.isdigit() else im
            print(f"  {label:<28} OK   memory.max={mm_gi}  cpu.max={got.get('cpu_max','?')}  "
                  f"df_size={av_gi}  image={im_mb}")
    except Exception as e:  # noqa: BLE001
        print(f"  {label:<28} FAIL {type(e).__name__}: {str(e)[:90]}")


async def main() -> None:
    md = sd.pack.to_row(str(sd.resolve_src("tw_100135")))["metadata"]
    print("booting each candidate on tw_100135:")
    for cpu, mem, disk in CANDIDATES:
        await try_one(md, cpu, mem, disk)


if __name__ == "__main__":
    asyncio.run(main())
