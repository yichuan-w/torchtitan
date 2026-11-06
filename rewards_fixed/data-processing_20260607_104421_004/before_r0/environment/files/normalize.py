#!/usr/bin/env python3
"""Convert European-format sales CSV to US format."""

import csv
from datetime import datetime


def eur_to_us(s):
    """Convert European number to US format."""
    s = s.strip()
    s = s.replace(',', '.')
    s = s.replace('.', '')
    return s


def parse_date(s):
    """Parse DD/MM/YYYY date."""
    s = s.strip()
    dt = datetime.strptime(s, '%d/%m/%Y')
    return dt.strftime('%Y-%m-%d')


def main():
    records = []
    with open('/app/sales_export.csv', 'r') as f:
        header = f.readline()
        for line in f:
            line = line.strip()
            if not line:
                continue

            parts = line.split(';')

            if len(parts) != 8:
                continue

            tid, date_str, customer, product, qty_str, up_str, ta_str, region = parts

            date_iso = parse_date(date_str)

            qty = int(float(eur_to_us(qty_str)))
            up = float(eur_to_us(up_str))
            ta = float(eur_to_us(ta_str))

            records.append({
                'TransactionID': tid.strip(),
                'Date': date_iso,
                'CustomerName': customer.strip().strip('"'),
                'ProductCode': product.strip(),
                'Quantity': str(qty),
                'UnitPrice': str(up),
                'TotalAmount': str(ta),
                'Region': region.strip(),
            })

    with open('/app/output.csv', 'w') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'TransactionID', 'Date', 'CustomerName', 'ProductCode',
            'Quantity', 'UnitPrice', 'TotalAmount', 'Region'
        ])
        writer.writeheader()
        writer.writerows(records)


if __name__ == '__main__':
    main()
