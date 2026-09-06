#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Find the things in a task corpus that stop working on a date nobody chose.

A task is validated once and served for months. Between those two moments its
base distribution can leave support, the archive it installs from can move or
expire, a key can lapse, a certificate it ships can pass its notAfter, a test
can compare against the year it was written in. None of that is visible in a
pass/fail table, and all of it is visible in the text. This reads every
Dockerfile, reference solution and test in the corpus and lists, per task, the
patterns that carry a date or an external dependency:

  base       the FROM image and, with --resolved, the distribution it really is
  apt_repo   third-party apt/yum repositories and the keys they need
  fetch      URLs fetched at build, solve or grade time
  floating   things that resolve to "whatever is newest today": :latest images,
             releases/latest, stable.txt, @latest, unpinned pip/npm/gem installs
  cert       embedded certificates, with their notAfter (needs openssl)
  token      embedded JWTs, with their exp claim
  clock      tests that read the clock or hard-code a year

Read-only. Nothing is built or run.

  audit_time_bombs.py --tar tasks-00000.tar --mix live.jsonl \
      --train-ready train_ready_ids.txt --resolved resolved_bases.json --out audit/
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import tarfile
from collections import Counter
from pathlib import Path

FROM_RE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", re.M)
URL_RE = re.compile(r"""(?<![\w/])(https?://[^\s'"`<>)\]\\|;,]+)""")
LOCAL_HOSTS = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "example.com",
    "example.org",
    "example.net",
    "host.docker.internal",
    "::1",
)
APT_REPO_RE = re.compile(
    r"^\s*(?:RUN\s+)?.*\b(?:deb|deb-src)\s+(?:\[[^\]]*\]\s*)?(https?://\S+)", re.M
)
KNOWN_REPO_HOSTS = (
    "nodesource.com",
    "yarnpkg.com",
    "download.docker.com",
    "packages.microsoft.com",
    "apt.postgresql.org",
    "repo.mongodb.org",
    "packages.cloud.google.com",
    "apt.kubernetes.io",
    "pkgs.k8s.io",
    "apt.releases.hashicorp.com",
    "erlang-solutions.com",
    "ppa.launchpad.net",
    "ppa.launchpadcontent.net",
    "apt.llvm.org",
    "grafana.com",
    "artifacts.elastic.co",
    "dl.google.com",
    "packagecloud.io",
    "download.opensuse.org",
    "deb.torproject.org",
    "packages.sury.org",
    "dl.winehq.org",
    "repo.saltproject.io",
    "download.virtualbox.org",
    "apt.armbian.com",
    "nginx.org/packages",
    "packages.gitlab.com",
    "cli.github.com/packages",
    "packages.redis.io",
    "dl.cloudsmith.io",
    "repo.mysql.com",
    "dev.mysql.com",
    "apache.jfrog.io",
    "packages.adoptium.net",
    "download.mono-project.com",
    "apt.corretto.aws",
    "packages.fluentbit.io",
    "deb.debian.org/debian-security",
    "archive.debian.org",
)
FLOATING = {
    "image_latest": re.compile(
        r"^\s*FROM\s+(?:--platform=\S+\s+)?\S+:latest\b|^\s*FROM\s+(?:--platform=\S+\s+)?[^:\s@]+\s*$",
        re.M,
    ),
    "github_releases_latest": re.compile(
        r"github\.com/[^/\s]+/[^/\s]+/releases/(?:latest|download/latest)"
    ),
    "k8s_stable_txt": re.compile(r"dl\.k8s\.io/release/stable"),
    "go_install_latest": re.compile(r"\bgo\s+(?:install|get)\s+\S+@latest"),
    "pip_unpinned": re.compile(
        r"\bpip3?\s+install\b(?![^\n]*(?:==|-r\s|--requirement|\.whl|\.tar\.gz|\./|\s/\S|git\+))[^\n]*"
    ),
    "npm_g_unpinned": re.compile(
        r"\bnpm\s+install\s+(?:-g|--global)\s+(?![^\n]*@\d)[^\n]*"
    ),
    "gem_unpinned": re.compile(r"\bgem\s+install\s+(?![^\n]*(?:-v|--version))[^\n]*"),
    "cargo_install": re.compile(r"\bcargo\s+install\s+(?![^\n]*--version)[^\n]*"),
    "install_script_pipe": re.compile(
        r"(?:curl|wget)[^\n|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b"
    ),
    "nodesource_setup": re.compile(r"deb\.nodesource\.com/setup_\S+"),
}
CLOCK = {
    "shell_date": re.compile(r"\$\(date\b|`date\b|\bdate\s+\+"),
    "py_now": re.compile(
        r"datetime\.(?:datetime\.)?(?:now|utcnow|today)\(|date\.today\(|time\.time\(|time\.localtime\(|time\.gmtime\("
    ),
    "js_now": re.compile(r"Date\.now\(|new Date\(\)"),
    "year_literal": re.compile(r"(?<![\d.])(202[5-9]|203\d)(?![\d.])"),
    "expiry_word": re.compile(
        r"\b(?:expir\w*|not_?after|notAfter|valid_?until|validUntil|max-age|maxAge)\b",
        re.I,
    ),
}
CERT_RE = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")

PHASES = {
    "build": ("environment/Dockerfile",),
    "solve": ("solution/",),
    "grade": ("tests/",),
}


def phase_of(rel: str) -> str:
    if rel == "environment/Dockerfile":
        return "build"
    if rel.startswith("solution/"):
        return "solve"
    if rel.startswith("tests/"):
        return "grade"
    return "other"


def host_of(url: str) -> str:
    m = re.match(r"https?://([^/:?#]+)", url)
    return m.group(1).lower() if m else url


def cert_not_after(pem: str) -> str:
    try:
        r = subprocess.run(
            ["openssl", "x509", "-noout", "-enddate"],
            input=pem.encode(),
            capture_output=True,
            timeout=10,
        )
        return (
            r.stdout.decode().strip().replace("notAfter=", "") or r.stderr.decode()[:60]
        )
    except Exception as e:  # noqa: BLE001
        return f"error {e}"


def jwt_exp(tok: str) -> str:
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        d = json.loads(base64.urlsafe_b64decode(payload))
        import datetime

        return (
            datetime.datetime.utcfromtimestamp(int(d["exp"])).isoformat()
            if "exp" in d
            else "no exp"
        )
    except Exception:  # noqa: BLE001
        return "unparsed"


def scan_text(rel: str, text: str, rec: dict) -> None:
    ph = phase_of(rel)
    for url in URL_RE.findall(text):
        h = host_of(url)
        if any(h.startswith(x) or h == x for x in LOCAL_HOSTS) or h.endswith(
            (".local", ".internal")
        ):
            continue
        rec["fetch"].append({"phase": ph, "file": rel, "url": url[:200]})
    if ph == "build":
        for m in APT_REPO_RE.finditer(text):
            rec["apt_repo"].append({"file": rel, "line": m.group(0).strip()[:200]})
        for name, rx in FLOATING.items():
            for m in rx.finditer(text):
                rec["floating"].append(
                    {
                        "phase": ph,
                        "file": rel,
                        "kind": name,
                        "line": m.group(0).strip()[:160],
                    }
                )
    else:
        for name in (
            "github_releases_latest",
            "k8s_stable_txt",
            "go_install_latest",
            "pip_unpinned",
            "npm_g_unpinned",
            "install_script_pipe",
            "nodesource_setup",
        ):
            for m in FLOATING[name].finditer(text):
                rec["floating"].append(
                    {
                        "phase": ph,
                        "file": rel,
                        "kind": name,
                        "line": m.group(0).strip()[:160],
                    }
                )
    for m in CERT_RE.finditer(text):
        rec["cert"].append({"file": rel, "not_after": cert_not_after(m.group(0))})
    for m in JWT_RE.finditer(text):
        rec["token"].append({"file": rel, "exp": jwt_exp(m.group(0))})
    if ph in ("grade", "solve", "build"):
        for name, rx in CLOCK.items():
            hits = rx.findall(text)
            if hits:
                rec["clock"].append(
                    {
                        "phase": ph,
                        "file": rel,
                        "kind": name,
                        "n": len(hits),
                        "sample": sorted(set(str(h) for h in hits))[:5],
                    }
                )


def new_rec(tid: str) -> dict:
    return {
        "task_id": tid,
        "from": [],
        "fetch": [],
        "apt_repo": [],
        "floating": [],
        "cert": [],
        "token": [],
        "clock": [],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tar", required=True)
    ap.add_argument(
        "--mix",
        default=None,
        help="live mix; rows without a package (TMax) are scanned from the row",
    )
    ap.add_argument("--train-ready", required=True)
    ap.add_argument("--resolved", default=None, help="resolve_base_images.py output")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    tr = set(l.strip() for l in open(a.train_ready) if l.strip())
    resolved = {}
    if a.resolved:
        for r in json.load(open(a.resolved)):
            resolved[r["ref"]] = r.get("os", "?")

    recs: dict[str, dict] = {}
    tar = tarfile.open(a.tar)
    for m in tar.getmembers():
        if not m.isfile():
            continue
        parts = m.name.split("/")
        if len(parts) < 3 or parts[0] != "tasks":
            continue
        tid = parts[1]
        rel = "/".join(parts[2:])
        rec = recs.setdefault(tid, new_rec(tid))
        if m.size > 2_000_000 and not rel.endswith(
            (
                ".sh",
                ".py",
                "Dockerfile",
                ".pem",
                ".crt",
                ".json",
                ".yaml",
                ".yml",
                ".toml",
                ".txt",
                ".md",
                ".js",
                ".ts",
            )
        ):
            continue
        raw = tar.extractfile(m).read()
        if b"\x00" in raw[:4096] and not rel.endswith((".pem", ".crt")):
            continue  # binary fixture
        text = raw.decode("utf-8", "replace")
        if rel == "environment/Dockerfile":
            rec["from"] = FROM_RE.findall(text)
        scan_text(rel, text, rec)

    mix_rows = 0
    if a.mix:
        for line in open(a.mix):
            if not line.strip():
                continue
            r = json.loads(line)
            md = r.get("metadata") or {}
            tid = r.get("label") or md.get("instance_id")
            mix_rows += 1
            if tid in recs:
                continue  # TW: the package was scanned
            rec = recs.setdefault(tid, new_rec(tid))
            rec["source"] = "mix_row"
            df = md.get("dockerfile") or ""
            if df:
                rec["from"] = FROM_RE.findall(df)
                scan_text("environment/Dockerfile", df, rec)
            tm = md.get("tmax") or {}
            if isinstance(tm, dict):
                if tm.get("test_sh"):
                    scan_text("tests/test.sh", tm["test_sh"], rec)
                if tm.get("pre_test_sh"):
                    scan_text("tests/pre_test.sh", tm["pre_test_sh"], rec)
            oc = md.get("oracle_commands")
            if isinstance(oc, list):
                scan_text("solution/solve.sh", "\n".join(str(x) for x in oc), rec)
            elif isinstance(oc, str):
                scan_text("solution/solve.sh", oc, rec)

    for tid, rec in recs.items():
        rec["train_ready"] = tid in tr
        rec["base_os"] = [resolved.get(f, "?") for f in rec["from"]]
        rec["hosts"] = sorted({host_of(f["url"]) for f in rec["fetch"]})

    with open(a.out / "per_task.jsonl", "w") as fh:
        for tid in sorted(recs):
            fh.write(json.dumps(recs[tid], ensure_ascii=False) + "\n")

    # summaries
    def summarize(sel):
        s = {"tasks": len(sel)}
        s["base_os"] = Counter(os_ for r in sel for os_ in r["base_os"]).most_common()
        s["apt_repo_hosts"] = Counter(
            host_of(URL_RE.search(x["line"]).group(1))
            if URL_RE.search(x["line"])
            else "?"
            for r in sel
            for x in r["apt_repo"]
        ).most_common()
        s["floating_kinds"] = Counter(
            x["kind"] for r in sel for x in r["floating"]
        ).most_common()
        s["tasks_with_floating"] = sum(1 for r in sel if r["floating"])
        s["fetch_hosts_by_phase"] = {
            ph: Counter(
                host_of(x["url"]) for r in sel for x in r["fetch"] if x["phase"] == ph
            ).most_common(40)
            for ph in ("build", "solve", "grade")
        }
        s["tasks_fetching_at_grade"] = sorted(
            r["task_id"] for r in sel if any(x["phase"] == "grade" for x in r["fetch"])
        )
        s["tasks_fetching_at_solve"] = sum(
            1 for r in sel if any(x["phase"] == "solve" for x in r["fetch"])
        )
        s["certs"] = [
            (r["task_id"], c["file"], c["not_after"]) for r in sel for c in r["cert"]
        ]
        s["tokens"] = [
            (r["task_id"], t["file"], t["exp"]) for r in sel for t in r["token"]
        ]
        s["clock_in_grade"] = [
            (r["task_id"], c["kind"], c["sample"])
            for r in sel
            for c in r["clock"]
            if c["phase"] == "grade"
        ]
        return s

    tr_rows = [r for r in recs.values() if r["train_ready"]]
    tmax_rows = [r for r in recs.values() if r.get("source") == "mix_row"]
    summary = {
        "train_ready": summarize(tr_rows),
        "tmax_rows": summarize(tmax_rows),
        "all_packages": summarize(
            [r for r in recs.values() if r.get("source") != "mix_row"]
        ),
    }
    json.dump(summary, open(a.out / "summary.json", "w"), indent=1, ensure_ascii=False)
    print(
        f"tasks scanned: {len(recs)} (train-ready {len(tr_rows)}, "
        f"mix rows without package {len(tmax_rows)}); mix rows seen {mix_rows}"
    )
    print(f"-> {a.out}/per_task.jsonl, {a.out}/summary.json")


if __name__ == "__main__":
    main()
