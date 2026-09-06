#!/usr/bin/env python3
"""Say which distribution release each base image tag really is, from the registry.

A tag rarely names its distribution (`python:3.6-slim`, `node:14`, `ruby:2.7`),
and a Dockerfile audit keyed on the tag misses every one of them. The image
config carries the answer: Debian images built by debuerreotype keep a history
line `debian.sh --arch 'amd64' out/ '<codename>'`, Ubuntu images carry the
`org.opencontainers.image.version` label, and the others say so in their
labels or environment. Nothing is pulled but the manifest and the config blob.

  resolve_base_images.py --tags tags.txt --out resolved.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MEDIA = ", ".join([
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])


def split(ref: str) -> tuple[str, str, str]:
    """registry, repository, tag."""
    registry = "registry-1.docker.io"
    if "/" in ref and ("." in ref.split("/")[0] or ":" in ref.split("/")[0]):
        registry, ref = ref.split("/", 1)
    tag = "latest"
    if "@" in ref:
        ref, tag = ref.split("@", 1)
    elif ":" in ref:
        ref, tag = ref.rsplit(":", 1)
    if registry == "registry-1.docker.io" and "/" not in ref:
        ref = "library/" + ref
    return registry, ref, tag


def get(url: str, headers: dict) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), dict(r.headers)


def head_digest(url: str, headers: dict) -> str | None:
    """Docker-Content-Digest of a manifest, via HEAD. Docker Hub does not count
    HEAD requests toward the pull-rate limit, GETs it does (two per pull)."""
    req = urllib.request.Request(url, headers=headers, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.headers.get("Docker-Content-Digest")
    except urllib.error.HTTPError as e:
        if e.code in (404, 401):
            return None
        raise


CODENAMES = {
    "trixie": "debian:trixie", "bookworm": "debian:bookworm", "bullseye": "debian:bullseye",
    "buster": "debian:buster", "stretch": "debian:stretch", "jessie": "debian:jessie",
    "noble": "ubuntu:24.04", "jammy": "ubuntu:22.04", "focal": "ubuntu:20.04",
    "bionic": "ubuntu:18.04", "xenial": "ubuntu:16.04", "alpine": "alpine",
}


def layer_os_release(base: str, man: dict, h: dict) -> str | None:
    """Read /etc/os-release out of the first (root filesystem) layer."""
    import gzip, io, tarfile
    layers = man.get("layers") or []
    if not layers:
        return None
    body, _ = get(f"{base}/blobs/{layers[0]['digest']}", h)
    try:
        raw = gzip.decompress(body)
    except OSError:
        raw = body
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        for name in ("etc/os-release", "./etc/os-release", "usr/lib/os-release", "./usr/lib/os-release"):
            try:
                m = t.getmember(name)
            except KeyError:
                continue
            if m.issym() or m.islnk():
                try:
                    m = t.getmember(m.linkname.lstrip("/").replace("../", "")) if not m.linkname.startswith("etc") else t.getmember(m.linkname)
                except KeyError:
                    continue
            txt = t.extractfile(m).read().decode("utf-8", "replace")
            kv = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', txt, re.M))
            return f"{kv.get('ID','?')}:{kv.get('VERSION_CODENAME') or kv.get('VERSION_ID') or '?'}"
    return None


def token(registry: str, repo: str) -> str:
    if registry == "registry-1.docker.io":
        url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
    elif registry == "nvcr.io":
        url = f"https://nvcr.io/proxy_auth?scope=repository:{repo}:pull"
    else:
        return ""
    body, _ = get(url, {})
    return json.loads(body).get("token", "")


def resolve(ref: str) -> dict:
    out = {"ref": ref}
    try:
        registry, repo, tag = split(ref)
        tok = token(registry, repo)
        h = {"Accept": MEDIA}
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        base = f"https://{registry}/v2/{repo}"
        # 1. free path: the same image under a codename-bearing sibling tag
        if "@" not in ref and registry == "registry-1.docker.io":
            mine = head_digest(f"{base}/manifests/{tag}", h)
            if mine:
                for cn, os_ in CODENAMES.items():
                    if cn in tag:
                        out["os"] = os_ + ("" if os_ != "alpine" else ":?")
                        out["how"] = "tag names it"
                        break
                else:
                    for cn, os_ in CODENAMES.items():
                        if head_digest(f"{base}/manifests/{tag}-{cn}", h) == mine:
                            out["os"] = os_ + ("" if os_ != "alpine" else ":?")
                            out["how"] = f"same digest as {tag}-{cn}"
                            break
                if out.get("os"):
                    return out
        # 2. counted path: the config blob's history and labels
        body, hdr = get(f"{base}/manifests/{tag}", h)
        man = json.loads(body)
        if "manifests" in man:  # index: pick linux/amd64
            cands = [m for m in man["manifests"] if m.get("platform", {}).get("os") == "linux"
                     and m["platform"].get("architecture") == "amd64"]
            if not cands:
                out["error"] = "no linux/amd64 manifest"
                return out
            body, _ = get(f"{base}/manifests/{cands[0]['digest']}", h)
            man = json.loads(body)
        cfg_digest = man["config"]["digest"]
        body, _ = get(f"{base}/blobs/{cfg_digest}", h)
        cfg = json.loads(body)
        out["created"] = cfg.get("created", "")[:10]
        labels = (cfg.get("config") or {}).get("Labels") or {}
        env = (cfg.get("config") or {}).get("Env") or []
        hist = " || ".join(x.get("created_by", "") for x in cfg.get("history", []))
        m = re.search(r"debian\.sh --arch '?\w+'? out/ '?([a-z]+)'?", hist)
        if m:
            out["os"] = f"debian:{m.group(1)}"
        elif labels.get("org.opencontainers.image.ref.name") == "ubuntu":
            out["os"] = f"ubuntu:{labels.get('org.opencontainers.image.version', '?')}"
        elif "ubuntu" in labels.get("org.opencontainers.image.ref.name", "").lower():
            out["os"] = f"ubuntu:{labels.get('org.opencontainers.image.version', '?')}"
        elif re.search(r"alpine", hist, re.I) or "alpine" in tag:
            m2 = re.search(r"alpine[-:_ ]v?(\d+\.\d+)", hist + " " + tag, re.I)
            out["os"] = f"alpine:{m2.group(1) if m2 else '?'}"
        elif labels.get("org.label-schema.name") or labels.get("name"):
            out["os"] = f"{labels.get('org.label-schema.name') or labels.get('name')}:{labels.get('org.label-schema.version') or labels.get('version') or labels.get('org.opencontainers.image.version') or '?'}"
        else:
            out["os"] = "?"
        out["how"] = "config"
        if out["os"] in ("?", "alpine:?") or out["os"].startswith("CentOS"):
            try:
                osr = layer_os_release(base, man, h)
                if osr:
                    out["os"] = osr
                    out["how"] = "first layer /etc/os-release"
            except Exception as e:  # noqa: BLE001
                out["layer_error"] = f"{type(e).__name__}: {e}"[:120]
        # keep what may explain it
        out["labels"] = {k: v for k, v in labels.items()
                         if any(s in k for s in ("version", "name", "ref.name", "release", "vendor"))}
        out["env_hint"] = [e for e in env if re.match(r"^(NODE|PYTHON|RUBY|PHP|GO|RUST|JULIA|ERLANG|OTP|ELIXIR|JAVA|REDIS|MONGO)_?(VERSION|MAJOR)", e)]
        out["history_hint"] = hist[:300]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", required=True, help="file, one image reference per line")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    refs = [l.strip() for l in open(a.tags) if l.strip() and not l.startswith("#")]
    with ThreadPoolExecutor(a.workers) as ex:
        rows = list(ex.map(resolve, refs))
    json.dump(rows, open(a.out, "w"), indent=1)
    ok = sum(1 for r in rows if r.get("os") and r["os"] != "?")
    print(f"resolved {ok}/{len(rows)} -> {a.out}", file=sys.stderr)
    for r in rows:
        print(f"{r['ref']:45s} {r.get('os','?'):22s} created={r.get('created','')} {r.get('error','')}")


if __name__ == "__main__":
    main()
