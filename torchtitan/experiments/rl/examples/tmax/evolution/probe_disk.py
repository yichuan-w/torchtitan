#!/usr/bin/env python3
"""Which counter sees a file that is written and then deleted?

A final `du` cannot: the file is gone by the time it runs. Peak disk therefore
needs a counter that either samples fast enough to catch the file while it
exists, or accumulates. This writes 400MB, measures, deletes, measures again,
and prints what each candidate said at each point.
"""
import asyncio

import solve_daytona as sd

STEPS = (
    "m(){ printf '%-10s df_used=%s du_root=%s io_w=%s\\n' \"$1\" "
    "\"$(df -B1 --output=used / 2>/dev/null | tail -1)\" "
    "\"$(du -sx --block-size=1 / 2>/dev/null | cut -f1)\" "
    "\"$(awk '{for(i=1;i<=NF;i++) if($i ~ /^wbytes=/){split($i,a,\"=\"); s+=a[2]}} END{print s+0}' "
    "/sys/fs/cgroup/io.stat 2>/dev/null)\"; }; "
    "m before; "
    "time_du_start=$(date +%s%3N); du -sx --block-size=1 / >/dev/null 2>&1; "
    "time_du_end=$(date +%s%3N); "
    "echo \"du_cost_ms=$((time_du_end-time_du_start))\"; "
    "dd if=/dev/zero of=/tmp/big.bin bs=1M count=400 2>/dev/null; sync; "
    "m with_file; "
    "rm -f /tmp/big.bin; sync; "
    "m after_del; "
    "echo 'mount of /:'; findmnt -no FSTYPE,SOURCE / 2>/dev/null || stat -fc '%T' /"
)


async def main() -> None:
    md = sd.pack.to_row(str(sd.resolve_src("tw_100135")))["metadata"]
    async with sd.boot_agent_sandbox(
        md.get("image") or "", dockerfile=md.get("dockerfile") or None,
        build_context=md.get("build_context") or None,
        install_claude=False, cpu=4, memory=8, disk_gb=10,
    ) as sandbox:
        sb = sd.dr._Root(sandbox)
        rc, out, err = await sb.exec(STEPS, check=False, timeout=300)
        print(f"rc={rc}\n{out}\n{err or ''}")


if __name__ == "__main__":
    asyncio.run(main())
