#!/usr/bin/env python3
"""Size the TMax tasks' prebuilt images from the registry, not from a sandbox.

The TW half of the mix builds from a Dockerfile, so its footprint can only be
learned by building it (measure_disk.py). The TMax half runs a prebuilt public
image, and the registry already knows how big that is, so 400 HTTP calls answer
what 400 sandbox boots would have.

Registry reports the COMPRESSED size. Layers are gzipped, so on-disk is larger;
--expansion sets the multiplier used for the estimate (2.5 by default, the usual
gzip ratio for OS/python layers). The result is a floor: it counts the image,
not what the agent writes at runtime.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import time
import urllib.error
import urllib.request

API = "https://hub.docker.com/v2/repositories/{repo}/tags/{tag}"


def size_of(image: str) -> tuple[str, int | None]:
    """Registry size for one image, retrying through the anonymous rate limit.

    Hub throttles a burst of anonymous tag lookups; a request that comes back
    empty is almost always that, not a missing tag, so a bare failure here reads
    as "this image does not exist" and silently shrinks the sample.
    """
    ref = image.split("://")[-1]
    if ref.startswith("docker.io/"):
        ref = ref[len("docker.io/"):]
    repo, _, tag = ref.partition(":")
    url = API.format(repo=repo, tag=tag)
    delay = 1.0
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "size-probe"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return image, json.load(r).get("full_size")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503, 502) or attempt == 5:
                return image, None
        except Exception:
            if attempt == 5:
                return image, None
        time.sleep(delay + random.random())
        delay *= 2
    return image, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mix", default="/scratch/gpfs/TRIDAO/al9080/terminal-rl"
                                     "/data/mix/mix_live.jsonl")
    ap.add_argument("--prefix", default="task_", help="id prefix selecting the rows")
    ap.add_argument("--expansion", type=float, default=2.5)
    ap.add_argument("--fleet-default", type=int, default=2, help="GiB")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    images = []
    for line in open(args.mix):
        if not line.strip():
            continue
        md = json.loads(line).get("metadata") or {}
        if str(md.get("instance_id", "")).startswith(args.prefix) and md.get("image"):
            images.append(md["image"])

    print(f"{len(images)} rows with a prebuilt image, querying the registry...")
    got: dict[str, int | None] = {}
    with concurrent.futures.ThreadPoolExecutor(args.workers) as ex:
        for img, sz in ex.map(size_of, images):
            got[img] = sz

    ok = {k: v for k, v in got.items() if v}
    missing = [k for k, v in got.items() if not v]
    if not ok:
        print("registry returned nothing; check network or rate limits")
        return

    mb = sorted(v / 1024 / 1024 for v in ok.values())
    est = [m * args.expansion / 1024 for m in mb]  # GiB on disk
    over = sum(1 for g in est if g > args.fleet_default)
    print(f"  answered {len(ok)}, unresolved {len(missing)}")
    print(f"\ncompressed size MB: min {mb[0]:.0f}  median {mb[len(mb)//2]:.0f}  "
          f"p95 {mb[int(len(mb)*0.95)]:.0f}  max {mb[-1]:.0f}")
    print(f"estimated on disk at x{args.expansion} (GiB): "
          f"median {est[len(est)//2]:.2f}  p95 {est[int(len(est)*0.95)]:.2f}  "
          f"max {est[-1]:.2f}")
    print(f"\nrows whose image alone exceeds the {args.fleet_default} GiB fleet "
          f"default: {over}")
    if over:
        big = sorted(((v / 1024 / 1024, k) for k, v in ok.items()), reverse=True)
        for m, img in big[:10]:
            print(f"  {m * args.expansion / 1024:5.2f} GiB est  {img}")
    if missing:
        print(f"\nunresolved (first 5): {missing[:5]}")


if __name__ == "__main__":
    main()
