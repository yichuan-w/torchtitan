"""Tests for the optimized solution script."""
import csv
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ORDERS_CSV = Path("/app/orders.csv")
CUSTOMERS_CSV = Path("/app/customers.csv")
PRODUCTS_CSV = Path("/app/products.csv")
ANALYSIS_PY = Path("/app/analysis.py")
SOLUTION_PY = Path("/app/solution.py")
OUTPUT_CSV = Path("/app/output.csv")


def _compute_correct_output():
    """Compute the correct revenue summary directly from the raw CSVs.
    Uses unit_price from orders (not list_price from products).
    Excludes test customers and non-completed orders."""
    # Build lookup dicts
    cust_lookup = {}
    with open(CUSTOMERS_CSV, 'r') as f:
        for row in csv.DictReader(f):
            cust_lookup[row['customer_id']] = row['segment']
    
    prod_lookup = {}
    with open(PRODUCTS_CSV, 'r') as f:
        for row in csv.DictReader(f):
            prod_lookup[row['product_id']] = row['category']
    
    summary = defaultdict(lambda: defaultdict(lambda: {'revenue': 0.0, 'count': 0}))
    
    with open(ORDERS_CSV, 'r') as f:
        for order in csv.DictReader(f):
            if order['status'] != 'completed':
                continue
            segment = cust_lookup.get(order['customer_id'])
            if segment is None or segment == 'test':
                continue
            category = prod_lookup.get(order['product_id'])
            if category is None:
                continue
            line_total = int(order['quantity']) * float(order['unit_price'])
            summary[segment][category]['revenue'] += line_total
            summary[segment][category]['count'] += 1
    
    result = []
    for segment in sorted(summary.keys()):
        for category in sorted(summary[segment].keys()):
            stats = summary[segment][category]
            revenue = round(stats['revenue'], 2)
            count = stats['count']
            avg = round(revenue / count, 2) if count > 0 else 0.0
            result.append({
                'segment': segment,
                'category': category,
                'total_revenue': str(revenue),
                'order_count': str(count),
                'avg_order_value': str(avg),
            })
    return result


def _run_script(script_path: Path) -> tuple[float, list]:
    """Run a Python script and return (elapsed_time, output_rows)."""
    if OUTPUT_CSV.exists():
        OUTPUT_CSV.unlink()
    
    t0 = time.perf_counter()
    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, 'PYTHONUNBUFFERED': '1'},
    )
    elapsed = time.perf_counter() - t0
    
    if result.returncode != 0:
        raise RuntimeError(f"Script {script_path} failed:\n{result.stderr}")
    
    rows = []
    if OUTPUT_CSV.exists():
        with open(OUTPUT_CSV, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    
    return elapsed, rows


def test_solution_file_exists():
    """Verify /app/solution.py exists."""
    assert SOLUTION_PY.exists(), f"{SOLUTION_PY} does not exist. Create it as required."


def test_solution_runs_without_error():
    """Verify solution.py executes without errors."""
    result = subprocess.run(
        [sys.executable, str(SOLUTION_PY)],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, 'PYTHONUNBUFFERED': '1'},
    )
    assert result.returncode == 0, (
        f"solution.py exited with code {result.returncode}.\n"
        f"STDERR:\n{result.stderr[:2000]}"
    )


def test_output_file_exists():
    """Verify output.csv is created when solution runs."""
    _run_script(SOLUTION_PY)
    assert OUTPUT_CSV.exists(), (
        f"{OUTPUT_CSV} was not created by solution.py"
    )


def test_output_correctness():
    """Verify solution.py produces the correct revenue summary."""
    expected = _compute_correct_output()
    
    solution_elapsed, solution_rows = _run_script(SOLUTION_PY)
    
    assert len(solution_rows) == len(expected), (
        f"Row count mismatch: solution has {len(solution_rows)}, expected {len(expected)}"
    )
    
    for i, (sol_row, exp_row) in enumerate(zip(solution_rows, expected)):
        for key in ['segment', 'category', 'total_revenue', 'order_count', 'avg_order_value']:
            sv = sol_row.get(key, '')
            ev = exp_row.get(key, '')
            # Float columns with tolerance
            if key in ('total_revenue', 'avg_order_value'):
                assert abs(float(sv) - float(ev)) <= 0.02, (
                    f"Row {i}, column '{key}' differs.\n"
                    f"Solution: {sv!r}\nExpected: {ev!r}"
                )
            else:
                assert sv == ev, (
                    f"Row {i}, column '{key}' differs.\n"
                    f"Solution: {sv!r}\nExpected: {ev!r}"
                )


def test_performance_improvement():
    """Verify solution.py is significantly faster than the original analysis.py."""
    original_elapsed, _ = _run_script(ANALYSIS_PY)
    print(f"Original script time: {original_elapsed:.3f}s")
    
    solution_elapsed, _ = _run_script(SOLUTION_PY)
    print(f"Solution script time: {solution_elapsed:.3f}s")
    
    speedup = original_elapsed / solution_elapsed if solution_elapsed > 0 else float('inf')
    print(f"Speedup: {speedup:.1f}x")
    
    assert speedup >= 3.0, (
        f"Solution is only {speedup:.1f}x faster. Need at least 3x speedup. "
        f"(Original: {original_elapsed:.3f}s, Solution: {solution_elapsed:.3f}s)"
    )


def test_output_has_required_columns():
    """Verify output.csv has the correct column headers."""
    _run_script(SOLUTION_PY)
    
    assert OUTPUT_CSV.exists(), "output.csv does not exist"
    
    with open(OUTPUT_CSV, 'r') as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames)
    
    expected = ['segment', 'category', 'total_revenue', 'order_count', 'avg_order_value']
    assert headers == expected, (
        f"Column headers mismatch.\nGot: {headers}\nExpected: {expected}"
    )


def test_output_no_null_or_empty():
    """Verify output.csv has no empty or null values."""
    _run_script(SOLUTION_PY)
    
    assert OUTPUT_CSV.exists(), "output.csv does not exist"
    
    with open(OUTPUT_CSV, 'r') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            for col in reader.fieldnames:
                val = row[col]
                assert val is not None and val != '', (
                    f"Row {i}, column '{col}' is empty or null"
                )

