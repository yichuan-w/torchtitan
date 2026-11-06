#!/bin/bash
set +e
python3 - <<'PY'
import importlib.util
import traceback

spec = importlib.util.spec_from_file_location("test_outputs", "/tests/test_outputs.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
failed = 0
for name in sorted(item for item in vars(module) if item.startswith("test_")):
    try:
        getattr(module, name)()
        print(f"PASS {name}")
    except Exception:
        failed += 1
        print(f"FAIL {name}")
        traceback.print_exc()
if failed:
    raise SystemExit(1)
PY
status=$?
if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
