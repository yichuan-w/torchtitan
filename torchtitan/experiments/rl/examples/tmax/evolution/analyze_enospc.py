#!/usr/bin/env python3
"""Test whether session-create ENOSPC is a property of the sandbox or of its host.

The error is `mkdir /root/.daytona/sessions/<uuid>: no space left on device`,
raised by the Daytona daemon while creating a session directory. It happens in
sandboxes given 8 GiB whose whole image is under a gigabyte, so the box's own
quota cannot be what ran out. The remaining candidate is the node underneath:
the overlay's upper directory lives on the host, and a full host fails every
sandbox scheduled onto it regardless of what that sandbox was promised.

That hypothesis makes a prediction the logs can settle. A host condition is
shared, so failures should bunch in time and hit whatever tasks happen to be
running in that window; a sandbox condition is private, so failures should track
the task and its size and scatter in time. This counts both.

It matters because the harness does not retry this error, and daytona.py says
why: a fresh session UUID cannot free blocks. That is right about the UUID and
says nothing about a fresh *sandbox*, which may land on a different node -- so
if the clustering shows up, the mitigation that was ruled out is not the one
that would work.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics as st
from pathlib import Path

TS = re.compile(r"(\d{2}-\d{2} \d{2}:\d{2}:\d{2})|(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def records(paths: list[str]) -> list[dict]:
    out = []
    for p in paths:
        for i, line in enumerate(open(p, errors="replace")):
            if "[sandbox_issue] " not in line:
                continue
            try:
                r = json.loads(line.split("[sandbox_issue] ", 1)[1])
            except Exception:  # noqa: BLE001
                continue
            m = TS.search(line)
            r["_ts"] = (m.group(0) if m else "")
            r["_line"] = i
            r["_log"] = Path(p).name
            out.append(r)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    a = ap.parse_args()
    rs = records(a.logs)
    en = [r for r in rs if r.get("kind") == "session_disk_exhausted"]
    print(f"sandbox_issue {len(rs)} 条, 其中爆盘 {len(en)} 条")
    if not en:
        return

    print("\n-- 分配尺寸: 如果是盒子自己的容量, 应该集中在小盘 --")
    print("  disk_gb:", dict(sorted(collections.Counter(r.get("disk_gb") for r in en).items(),
                                    key=lambda kv: (kv[0] is None, kv[0]))))
    print("  全部 issue 的 disk_gb 作对照:",
          dict(sorted(collections.Counter(r.get("disk_gb") for r in rs).items(),
                      key=lambda kv: (kv[0] is None, kv[0]))))

    print("\n-- 时间聚集: 宿主故障是共享的, 应该成簇 --")
    for log in sorted({r["_log"] for r in en}):
        ln = sorted(r["_line"] for r in en if r["_log"] == log)
        total = sum(1 for r in rs if r["_log"] == log)
        gaps = [b - a_ for a_, b in zip(ln, ln[1:])]
        print(f"  {log}: {len(ln)} 次, 行号 {ln}")
        if gaps:
            print(f"    相邻间隔 median {st.median(gaps):.0f} 行 (该日志共 {total} 条 issue)")

    print("\n-- 每个沙箱是不是只报一次 --")
    per_sb = collections.Counter(r.get("sandbox_id") for r in en)
    print("  不同 sandbox_id:", len(per_sb), "| 每个的次数分布:",
          dict(collections.Counter(per_sb.values())))

    print("\n-- 同一 group 内的分布: 一个 group 的 rollout 同时开, 落在同一批节点上 --")
    g = collections.Counter((r.get("instance_id"), r.get("group_id")) for r in en)
    for (tid, gid), n in g.most_common():
        print(f"  {tid} group={gid}: {n} 次")

    print("\n-- 任务是否重复中招 --")
    print("  ", dict(collections.Counter(r.get("instance_id") for r in en)))


if __name__ == "__main__":
    main()
