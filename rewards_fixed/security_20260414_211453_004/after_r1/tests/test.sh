#!/bin/bash

set +e

pytest /tests/test_outputs.py -rA
public_status=$?

python - <<'PY'
import sys

sys.path.insert(0, "/app")
import app


class ExternalResponse:
    headers = {"content-length": "2"}
    status_code = 200
    url = "https://example.com/public/resource"

    def iter_content(self, chunk_size):
        yield b"ok"


app.requests.get = lambda *args, **kwargs: ExternalResponse()
result = app.fetch_url("https://example.com/public/resource")
assert result.get("success") is True and result.get("content") == "ok", "A legitimate external HTTPS URL must remain fetchable"
PY
external_status=$?

if [ "$public_status" -eq 0 ] && [ "$external_status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
  exit 0
else
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi
