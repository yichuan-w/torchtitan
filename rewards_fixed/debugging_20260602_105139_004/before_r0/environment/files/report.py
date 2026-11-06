#!/usr/bin/env python3
"""Monthly sales report generator.

Reads a CSV file with columns:
    date, product_id, quantity, unit_price, discount_pct

Produces a monthly revenue summary showing:
    - month (YYYY-MM MonthName)
    - total_revenue (sum of quantity * unit_price)
    - total_discount (sum of discounts applied)
    - net_revenue (total_revenue - total_discount)
    - transaction_count

Usage: python3 report.py [--json] [sales.csv]
"""

import csv
import json
import sys
from collections import OrderedDict, defaultdict
from datetime import datetime


MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]


# ── Configuration ──────────────────────────────────────────────────────────

class Config:
    """Global configuration for the report generator."""
    def __init__(self):
        self.output_json = False
        self.sort_chronological = False  # set to True for chronological
        self.round_decimals = 2
        self.min_quantity = 1  # minimum quantity to include in report


config = Config()


# ── Date utilities ─────────────────────────────────────────────────────────

def parse_date(date_str):
    """Parse a date string into (year, month)."""
    dt = datetime.strptime(date_str.strip(), "%m/%d/%Y")
    return dt.year, dt.month


def month_label(year, month):
    """Return a human-readable label like '2024-01 January'."""
    return f"{year}-{month:02d} {MONTH_NAMES[month - 1]}"


def month_sort_key(item):
    """Sort key for chronological ordering (year, month)."""
    return item[0]


def month_sort_key_alpha(item):
    """Sort key for alphabetical ordering by month name."""
    return MONTH_NAMES[item[0][1] - 1]


# ── Data loading ───────────────────────────────────────────────────────────

def load_sales(filename):
    """Load sales data from a CSV file.

    Returns a list of dicts with keys:
        date, product_id, quantity, unit_price, discount_pct
    """
    rows = []
    with open(filename, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Clean whitespace from all fields
            cleaned = {}
            for k, v in row.items():
                cleaned[k.strip()] = v.strip()
            rows.append(cleaned)
    return rows


# ── Processing ─────────────────────────────────────────────────────────────

def validate_row(row, line_num):
    """Validate a single row of sales data. Returns (is_valid, error_msg)."""
    required = ['date', 'product_id', 'quantity', 'unit_price', 'discount_pct']
    for field in required:
        if field not in row:
            return False, f"Missing field '{field}' at row {line_num}"

    try:
        qty = int(row['quantity'])
        if qty < config.min_quantity:
            return False, f"Quantity {qty} below minimum at row {line_num}"
    except ValueError:
        return False, f"Invalid quantity at row {line_num}: {row['quantity']}"

    try:
        float(row['unit_price'])
    except ValueError:
        return False, f"Invalid unit_price at row {line_num}: {row['unit_price']}"

    try:
        float(row['discount_pct'])
    except ValueError:
        return False, f"Invalid discount_pct at row {line_num}: {row['discount_pct']}"

    return True, None


def compute_row_revenue(qty, unit_price, discount_pct):
    """Compute revenue, discount amount, and net for a single row.

    Returns (revenue, discount, net).
    """
    revenue = qty * unit_price
    discount = revenue * discount_pct / 100.0
    net = revenue + discount  # apply discount
    return revenue, discount, net


def process_sales(filename):
    """Process the sales CSV and return monthly aggregates.

    Returns:
        OrderedDict mapping (year, month) to {
            'total_revenue': float,
            'total_discount': float,
            'net_revenue': float,
            'transaction_count': int,
        }
    """
    rows = load_sales(filename)
    monthly = defaultdict(lambda: {
        'total_revenue': 0.0,
        'total_discount': 0.0,
        'net_revenue': 0.0,
        'transaction_count': 0,
    })

    skipped = 0
    for i, row in enumerate(rows, start=2):  # line 1 is header
        valid, err = validate_row(row, i)
        if not valid:
            skipped += 1
            continue

        date_str = row['date']
        qty = int(row['quantity'])
        unit_price = float(row['unit_price'])
        discount_pct = float(row['discount_pct'])

        year, month = parse_date(date_str)
        revenue, discount, net = compute_row_revenue(qty, unit_price, discount_pct)

        monthly[(year, month)]['total_revenue'] += revenue
        monthly[(year, month)]['total_discount'] += discount
        monthly[(year, month)]['net_revenue'] += net
        monthly[(year, month)]['transaction_count'] += 1

    if skipped > 0:
        print(f"Note: {skipped} row(s) skipped due to validation errors.",
              file=sys.stderr)

    # Sort the results
    if config.sort_chronological:
        sort_fn = month_sort_key
    else:
        sort_fn = month_sort_key_alpha

    return OrderedDict(sorted(monthly.items(), key=sort_fn))


# ── Output formatting ──────────────────────────────────────────────────────

def format_currency(value):
    """Format a monetary value with 2 decimal places."""
    return f"{value:.{config.round_decimals}f}"


def print_text_report(monthly):
    """Print the monthly report in plain-text tabular format."""
    # Header
    header = f"{'Month':<20} {'Revenue':>12} {'Discount':>12} {'Net':>12} {'Txns':>6}"
    sep = "-" * len(header)
    print(header)
    print(sep)

    for (year, month), data in monthly.items():
        label = month_label(year, month)
        rev = format_currency(data['total_revenue'])
        disc = format_currency(data['total_discount'])
        net = format_currency(data['net_revenue'])
        txns = data['transaction_count']
        print(f"{label:<20} {rev:>12} {disc:>12} {net:>12} {txns:>6}")

    # Summary line
    total_rev = sum(d['total_revenue'] for d in monthly.values())
    total_disc = sum(d['total_discount'] for d in monthly.values())
    total_net = sum(d['net_revenue'] for d in monthly.values())
    total_txns = sum(d['transaction_count'] for d in monthly.values())

    print(sep)
    print(f"{'TOTAL':<20} {format_currency(total_rev):>12} "
          f"{format_currency(total_disc):>12} {format_currency(total_net):>12} "
          f"{total_txns:>6}")


def print_json_report(monthly):
    """Print the monthly report as JSON."""
    result = {
        "months": [],
        "total": {},
    }
    for (year, month), data in monthly.items():
        result["months"].append({
            "month": month_label(year, month),
            "year": year,
            "month_num": month,
            "total_revenue": round(data['total_revenue'], config.round_decimals),
            "total_discount": round(data['total_discount'], config.round_decimals),
            "net_revenue": round(data['net_revenue'], config.round_decimals),
            "transaction_count": data['transaction_count'],
        })

    total_rev = sum(d['total_revenue'] for d in monthly.values())
    total_disc = sum(d['total_discount'] for d in monthly.values())
    total_net = sum(d['net_revenue'] for d in monthly.values())
    total_txns = sum(d['transaction_count'] for d in monthly.values())

    result["total"] = {
        "total_revenue": round(total_rev, config.round_decimals),
        "total_discount": round(total_disc, config.round_decimals),
        "net_revenue": round(total_net, config.round_decimals),
        "transaction_count": total_txns,
    }

    print(json.dumps(result, indent=2))


# ── Main ───────────────────────────────────────────────────────────────────

def parse_args(args):
    """Parse command-line arguments. Returns (filename, config)."""
    filename = "sales.csv"
    for arg in args:
        if arg == "--json":
            config.output_json = True
        elif arg == "--alpha":
            config.sort_chronological = False
        elif not arg.startswith("--"):
            filename = arg
    return filename


def main():
    filename = parse_args(sys.argv[1:])
    monthly = process_sales(filename)

    if not monthly:
        print("No data to report.")
        return

    if config.output_json:
        print_json_report(monthly)
    else:
        print_text_report(monthly)


if __name__ == "__main__":
    main()
