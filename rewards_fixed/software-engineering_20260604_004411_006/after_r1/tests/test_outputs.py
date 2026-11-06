import hashlib
import os
import shutil
import subprocess

def run(s, d):
    r = subprocess.run(["/app/build/strtool", s, d], capture_output=True, text=True, timeout=10)
    return r.stdout, r.stderr, r.returncode

def test_make_builds_binary():
    shutil.rmtree("/app/build", ignore_errors=True)
    r = subprocess.run(["make"], cwd="/app", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and os.path.isfile("/app/build/strtool"), f"make failed:\n{r.stdout}\n{r.stderr}"

def test_binary_exists():
    assert os.path.exists("/app/build/strtool"), "Missing binary"

def test_simple():
    o, e, rc = run("a,b,c", ",")
    assert rc == 0 and "Parts (3)" in o, f"Bad:\n{o}"
    assert "[0]: 'a'" in o and "[1]: 'b'" in o and "[2]: 'c'" in o

def test_space():
    o, e, rc = run("hello world", " ")
    assert rc == 0 and "Parts (2)" in o
    assert "[0]: 'hello'" in o and "[1]: 'world'" in o

def test_single():
    o, e, rc = run("solo", ",")
    assert rc == 0 and "Parts (1)" in o and "[0]: 'solo'" in o

# Public requirement: consecutive delimiters must produce empty segments
def test_consecutive():
    o, e, rc = run("one,,three", ",")
    assert rc == 0 and "Parts (3)" in o, f"Expected 3 parts:\n{o}"
    assert "[0]: 'one'" in o
    assert "[1]: ''" in o, f"Missing empty segment for consecutive delimiters:\n{o}"
    assert "[2]: 'three'" in o

def test_multiple_consecutive_delimiters():
    o, e, rc = run("a,,,b", ",")
    assert rc == 0 and "Parts (4)" in o and "[0]: 'a'" in o and "[1]: ''" in o and "[2]: ''" in o and "[3]: 'b'" in o, f"Missing empty parts for adjacent delimiters:\n{o}"

def test_header_unmodified():
    with open("/app/include/strlib.h", "rb") as header:
        digest = hashlib.sha256(header.read()).hexdigest()
    assert digest == "3359722dc68edb39373807541616297af3c4dacbd750c6409070fecbcc4cea16", "include/strlib.h was modified"
