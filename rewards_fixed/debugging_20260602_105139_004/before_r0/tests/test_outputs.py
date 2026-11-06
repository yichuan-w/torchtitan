import math
import os
import re
import subprocess
import sys

import pytest


def run_report():
    """Run report.py and return parsed output as a list of monthly rows
    plus the TOTAL row. Each row is a dict."""
    result = subprocess.run(
        [sys.executable, "report.py", "sales.csv"],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"report.py failed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    lines = result.stdout.strip().split("\n")
    # Skip header and separator lines
    data_lines = []
    in_data = False
    for line in lines:
        if line.startswith("Month"):
            in_data = True
            continue
        if line.startswith("-"):
            continue
        if not in_data:
            continue
        data_lines.append(line)

    # Last line is TOTAL, rest are month rows
    assert len(data_lines) >= 2, f"Expected at least TOTAL row, got: {data_lines}"

    total_line = data_lines[-1]
    month_lines = data_lines[:-1]

    def parse_row(line):
        parts = line.split()
        # Format: MonthName  Revenue  Discount  Net  Txns
        # Month has format "YYYY-MM MonthName" (e.g., "2024-01 January")
        label = f"{parts[0]} {parts[1]}"  # "2024-01 January"
        revenue = float(parts[2])
        discount = float(parts[3])
        net = float(parts[4])
        txns = int(parts[5])
        return {
            "label": label,
            "revenue": revenue,
            "discount": discount,
            "net": net,
            "txns": txns,
        }

    months = [parse_row(line) for line in month_lines]
    total = parse_row(total_line.replace("TOTAL", "TOTAL TOTAL"))
    return months, total


# Expected values computed from the CSV data with correct logic
# (DD/MM/YYYY dates, net = revenue - discount, chronological order)
EXPECTED_MONTHS = [
    # (label, revenue, discount, net, txns)
    ("2024-01 January", 690.00, 81.00, 609.00, 3),
    ("2024-02 February", 200.00, 30.00, 170.00, 1),
    ("2024-03 March", 570.00, 44.50, 525.50, 2),
    ("2024-04 April", 225.00, 11.25, 213.75, 1),
    ("2024-05 May", 900.00, 108.00, 792.00, 3),
    ("2024-06 June", 275.00, 55.00, 220.00, 1),
    ("2024-07 July", 500.00, 60.00, 440.00, 2),
    ("2024-08 August", 487.50, 31.25, 456.25, 2),
    ("2024-09 September", 187.50, 0.00, 187.50, 1),
    ("2024-10 October", 275.00, 13.75, 261.25, 1),
    ("2024-11 November", 75.00, 11.25, 63.75, 1),
    ("2024-12 December", 550.00, 35.00, 515.00, 2),
]

EXPECTED_TOTAL = {
    "revenue": 4935.00,
    "discount": 481.00,
    "net": 4454.00,
    "txns": 20,
}


def test_report_has_all_months():
    """Verify the report contains exactly the 12 expected months."""
    months, total = run_report()
    labels = [m["label"] for m in months]
    expected_labels = [e[0] for e in EXPECTED_MONTHS]
    assert labels == expected_labels, (
        f"Month list mismatch.\nExpected: {expected_labels}\nGot: {labels}"
    )


def test_report_figures():
    """Verify revenue, discount, net, and transaction counts per month."""
    months, total = run_report()
    for i, (month_data, expected) in enumerate(zip(months, EXPECTED_MONTHS)):
        label, exp_rev, exp_disc, exp_net, exp_txns = expected
        rtol = 1e-9
        for field, exp_val, actual_val in [
            ("revenue", exp_rev, month_data["revenue"]),
            ("discount", exp_disc, month_data["discount"]),
            ("net", exp_net, month_data["net"]),
        ]:
            if abs(exp_val) > 1e-6:
                rel_err = abs(actual_val - exp_val) / abs(exp_val)
                assert rel_err < rtol, (
                    f"Month {label}: {field} mismatch: "
                    f"expected {exp_val:.2f}, got {actual_val:.2f}"
                )
            else:
                assert abs(actual_val - exp_val) < rtol, (
                    f"Month {label}: {field} mismatch: "
                    f"expected {exp_val:.2f}, got {actual_val:.2f}"
                )
        assert month_data["txns"] == exp_txns, (
            f"Month {label}: txns mismatch: expected {exp_txns}, got {month_data['txns']}"
        )


def test_report_total():
    """Verify the TOTAL row matches expected grand totals."""
    months, total = run_report()
    rtol = 1e-9
    for field in ["revenue", "discount", "net"]:
        exp = EXPECTED_TOTAL[field]
        act = total[field]
        if abs(exp) > 1e-6:
            rel_err = abs(act - exp) / abs(exp)
            assert rel_err < rtol, (
                f"TOTAL {field}: expected {exp:.2f}, got {act:.2f}"
            )
        else:
            assert abs(act - exp) < rtol, (
                f"TOTAL {field}: expected {exp:.2f}, got {act:.2f}"
            )
    assert total["txns"] == EXPECTED_TOTAL["txns"], (
        f"TOTAL txns: expected {EXPECTED_TOTAL['txns']}, got {total['txns']}"
    )


def test_chronological_order():
    """Verify months are in chronological order (Jan → Dec)."""
    months, _ = run_report()
    for i in range(1, len(months)):
        prev = months[i - 1]["label"]
        curr = months[i]["label"]
        assert prev < curr, (
            f"Months not in chronological order: '{prev}' before '{curr}'"
        )


def test_net_equals_revenue_minus_discount():
    """Verify net = revenue - discount for every row."""
    months, total = run_report()
    rtol = 1e-9
    for m in months:
        expected_net = m["revenue"] - m["discount"]
        if abs(expected_net) > 1e-6:
            rel_err = abs(m["net"] - expected_net) / abs(expected_net)
            assert rel_err < rtol, (
                f"Month {m['label']}: net ({m['net']:.2f}) != "
                f"revenue ({m['revenue']:.2f}) - discount ({m['discount']:.2f})"
            )
        else:
            assert abs(m["net"] - expected_net) < rtol, (
                f"Month {m['label']}: net mismatch"
            )
    # Total row
    expected_total_net = total["revenue"] - total["discount"]
    if abs(expected_total_net) > 1e-6:
        rel_err = abs(total["net"] - expected_total_net) / abs(expected_total_net)
        assert rel_err < rtol, "TOTAL net mismatch"
    else:
        assert abs(total["net"] - expected_total_net) < rtol, "TOTAL net mismatch"


