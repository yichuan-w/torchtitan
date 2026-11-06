import subprocess, os

def run(s, d):
    r = subprocess.run(["/app/build/strtool", s, d], capture_output=True, text=True, timeout=10)
    return r.stdout, r.stderr, r.returncode

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

# Hidden edge case: consecutive delimiters must produce empty segments
def test_consecutive():
    o, e, rc = run("one,,three", ",")
    assert rc == 0 and "Parts (3)" in o, f"Expected 3 parts:\n{o}"
    assert "[0]: 'one'" in o
    assert "[1]: ''" in o, f"Missing empty segment for consecutive delimiters:\n{o}"
    assert "[2]: 'three'" in o


