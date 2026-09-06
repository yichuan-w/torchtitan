#!/usr/bin/env python3
"""Probe every URL in a list and print one JSON line per URL: status code, or 0 with the error.

A HEAD first, then a ranged GET where the server refuses HEAD, with a browser-like
User-Agent since several hosts answer curl's own with 403 or 418. A 200 here says
the URL resolves today and nothing more; a 404 or 0 is a lead to read the line that
uses it, since a URL in a comment or an identifier string breaks nothing.

  audit_time_bombs.py ... --out audit/      # writes per_task.jsonl with the URLs
  check_urls.py urls.txt > url_checks.jsonl
"""
import json, sys, urllib.request, urllib.error, ssl
from concurrent.futures import ThreadPoolExecutor
ctx = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0 (corpus-audit; read-only)"}
def probe(u):
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(u, method=method, headers=UA | ({"Range": "bytes=0-0"} if method == "GET" else {}))
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                return {"url": u, "code": r.status, "final": r.geturl()[:200]}
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405, 400, 404):
                continue
            return {"url": u, "code": e.code}
        except Exception as e:  # noqa: BLE001
            if method == "HEAD":
                continue
            return {"url": u, "code": 0, "error": f"{type(e).__name__}: {e}"[:120]}
    return {"url": u, "code": 0, "error": "unreachable"}
urls = [l.strip() for l in open(sys.argv[1]) if l.strip()]
with ThreadPoolExecutor(12) as ex:
    for r in ex.map(probe, urls):
        print(json.dumps(r), flush=True)
