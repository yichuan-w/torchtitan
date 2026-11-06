import ast
import math
import subprocess
from pathlib import Path

from mpmath import mp


EXPECTED = [17, -29, 37, -47, 53, -67]
DYNAMIC_CONSTANTS = '''from mpmath import mp

mp.dps = 200

def get_constants():
    x0 = mp.mpf(1)
    x1 = mp.sqrt(2)
    x2 = mp.sqrt(3)
    x3 = mp.sqrt(5)
    x4 = mp.sqrt(7)
    partial = 17*x0 - 29*x1 + 37*x2 - 47*x3 + 53*x4
    x5 = partial / 67
    return [x0, x1, x2, x3, x4, x5]

def get_precision():
    return mp.dps
'''


def test_completed_pslq_workflow():
    source_path = Path("/app/find_relation.py")
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "pslq"]
    prohibited_import = any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "pslq" for alias in node.names)
        for node in ast.walk(tree)
    )
    prohibited_call = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "pslq"
        for node in ast.walk(tree)
    )
    placeholder = not functions or any(
        isinstance(node, ast.Raise)
        and (
            isinstance(node.exc, ast.Name) and node.exc.id == "NotImplementedError"
            or isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name) and node.exc.func.id == "NotImplementedError"
        )
        for node in ast.walk(functions[0])
    )
    imports_mpmath = any(
        (isinstance(node, ast.Import) and any(alias.name == "mpmath" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "mpmath")
        for node in ast.walk(tree)
    )
    assert imports_mpmath and not prohibited_import and not prohibited_call and not placeholder, "pslq() must be implemented with mpmath without forwarding to a library PSLQ"

    Path("/app/constants.py").write_text(DYNAMIC_CONSTANTS, encoding="utf-8")
    relation_path = Path("/app/relation.txt")
    relation_path.unlink(missing_ok=True)
    result = subprocess.run(["python3", str(source_path)], capture_output=True, text=True, timeout=300)
    assert result.returncode == 0 and relation_path.is_file(), f"completed starter did not create /app/relation.txt: stdout={result.stdout!r}, stderr={result.stderr!r}"

    lines = relation_path.read_text(encoding="utf-8").splitlines()
    try:
        coefficients = [int(line.strip()) for line in lines]
    except ValueError as error:
        raise AssertionError(f"relation.txt must contain one integer per line: {error}") from error
    assert len(lines) == 6 and all(line.strip() for line in lines), f"expected six integer lines, got {lines!r}"
    assert coefficients == EXPECTED, f"expected normalized dynamic relation {EXPECTED}, got {coefficients}"

    values = [mp.mpf(1), mp.sqrt(2), mp.sqrt(3), mp.sqrt(5), mp.sqrt(7)]
    values.append((17 * values[0] - 29 * values[1] + 37 * values[2] - 47 * values[3] + 53 * values[4]) / 67)
    residual = mp.fsum(coefficient * value for coefficient, value in zip(coefficients, values))
    nonzero = [abs(coefficient) for coefficient in coefficients if coefficient]
    assert coefficients and nonzero and math.gcd(*nonzero) == 1 and next(coefficient for coefficient in coefficients if coefficient) > 0 and abs(residual) < mp.mpf("1e-100"), f"relation is zero, unnormalized, incorrectly signed, or inaccurate: residual={residual}"
