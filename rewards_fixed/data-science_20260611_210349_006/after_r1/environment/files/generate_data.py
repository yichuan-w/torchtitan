"""Generate mock CSV files with heterogeneous schemas for the normalization task."""
import csv
import os
import random
import sys
from datetime import datetime, timedelta

random.seed(42)

OUTPUT_DIR = "/app/data"
N_ROWS = 500

# Common base data
base_ids = list(range(1001, 1001 + N_ROWS))
base_dates = [datetime(2023, 1, 1) + timedelta(days=random.randint(0, 364)) for _ in range(N_ROWS)]
base_amounts = [round(random.uniform(10, 5000), 2) for _ in range(N_ROWS)]
base_categories = random.choices(['Electronics', 'Clothing', 'Food', 'Books', 'Sports'], k=N_ROWS)
base_regions = random.choices(['North', 'South', 'East', 'West', 'Central'], k=N_ROWS)


def write_csv(filename, headers, row_gen, encoding='utf-8', add_bom=False):
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, 'w', newline='', encoding=encoding) as f:
        if add_bom:
            f.write('\ufeff')
        writer = csv.writer(f)
        writer.writerow(headers)
        for i in range(N_ROWS):
            writer.writerow(row_gen(i))
    print(f"  Wrote {path} ({N_ROWS} rows)")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # File 1: transactions_2023_q1.csv - Clean, programmer-friendly, ISO dates, BUT with BOM
    print("Generating transactions_2023_q1.csv (clean, UTF-8 BOM)...")
    write_csv("transactions_2023_q1.csv",
              ["id", "order_date", "amount", "category", "region"],
              lambda i: [
                  base_ids[i],
                  base_dates[i].strftime("%Y-%m-%d"),
                  base_amounts[i],
                  base_categories[i],
                  base_regions[i],
              ],
              add_bom=True)

    # File 2: sales_report_feb.csv - Human-readable names, US dates, $ prefix, trailing spaces in headers
    print("Generating sales_report_feb.csv (human-readable, US dates, $, trailing spaces)...")
    write_csv("sales_report_feb.csv",
              ["Order ID ", "Purchase Date ", "Sale Amount ", "Product Category ", "Sales Region "],
              lambda i: [
                  base_ids[i] + 500,
                  base_dates[i].strftime("%m/%d/%Y"),
                  f"${base_amounts[i]:,.2f}",
                  base_categories[i],
                  base_regions[i],
              ])

    # File 3: eu_export.csv - European format, abbreviated names, semicolon-like issues
    print("Generating eu_export.csv (European format, abbreviated names)...")
    def row_c(i):
        amt_str = f"{base_amounts[i]:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
        return [
            base_ids[i] + 300,
            base_dates[i].strftime("%d-%m-%Y"),
            amt_str,
            base_categories[i],
            base_regions[i],
        ]
    write_csv("eu_export.csv",
              ["txn_no", "sale_dt", "rev_eur", "prod_cat", "geo"],
              row_c)

    # File 4: legacy_dump.csv - Different column order, mixed dates, missing vals, latin-1 encoding
    print("Generating legacy_dump.csv (latin-1 encoding, mixed formats, missing values)...")
    formats = ["%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"]
    special_cats_legacy = {
        'Electronics': 'Électronique',
        'Clothing': 'Vêtements',
        'Food': 'Alimentation',
        'Books': 'Livres',
        'Sports': 'Sport',
    }
    def row_d(i):
        fmt = formats[i % len(formats)]
        date_str = base_dates[i].strftime(fmt)
        amt = base_amounts[i]
        if i % 7 == 0:
            amt_str = f"{amt:,.2f}"
        else:
            amt_str = f"{amt:.2f}"
        rid = base_ids[i] + 700
        cat = special_cats_legacy[base_categories[i]] if i % 13 != 0 else ""
        reg = base_regions[i] if i % 17 != 0 else "N/A"
        return [reg, cat, date_str, amt_str, rid]

    write_csv("legacy_dump.csv",
              ["region_name", "category_name", "sale_date", "transaction_amount", "record_id"],
              row_d,
              encoding='latin-1')

    # File 5: weekly_export_2023.csv - Same schema as q1 but with overlapping IDs and new rows
    print("Generating weekly_export_2023.csv (overlap + new rows)...")
    write_csv("weekly_export_2023.csv",
              ["id", "order_date", "amount", "category", "region"],
              lambda i: [
                  base_ids[i] if i < 350 else (2000 + i),
                  base_dates[i].strftime("%Y-%m-%d"),
                  base_amounts[i] if i < 350 else round(random.uniform(10, 5000), 2),
                  base_categories[i] if i < 350 else random.choice(['Electronics', 'Clothing', 'Food', 'Books', 'Sports']),
                  base_regions[i] if i < 350 else random.choice(['North', 'South', 'East', 'West', 'Central']),
              ])

    # File 6: partner_feed.csv - Special characters, verbose naming, USD prefix
    print("Generating partner_feed.csv (special chars, verbose names)...")
    special_cats = {
        'Electronics': 'Éléctronics',
        'Clothing': 'Clöthing & Accéssoires',
        'Food': 'Food & Beverages',
        'Books': 'Böoks',
        'Sports': 'Sports & Outdoors',
    }
    def row_f(i):
        return [
            base_ids[i] + 100,
            base_dates[i].strftime("%Y-%m-%d"),
            f"USD {base_amounts[i]:.2f}",
            special_cats[base_categories[i]],
            base_regions[i],
        ]
    write_csv("partner_feed.csv",
              ["Record Number", "Transaction Date", "Total (USD)", "Item Category", "Location"],
              row_f)

    # File 7: nordic_export.csv - Semicolon-delimited (European CSV), different naming
    print("Generating nordic_export.csv (semicolon-delimited, Scandinavian names)...")
    path = os.path.join(OUTPUT_DIR, "nordic_export.csv")
    with open(path, 'w', newline='', encoding='utf-8') as f:
        f.write("ordrenr;dato;beløp;produktgruppe;område\n")
        for i in range(N_ROWS):
            f.write(f"{base_ids[i] + 200};{base_dates[i].strftime('%Y-%m-%d')};{base_amounts[i]:.2f};{base_categories[i]};{base_regions[i]}\n")
    print(f"  Wrote {path} ({N_ROWS} rows)")

    # File 8: tab_export.tsv - Tab-delimited, dates with slashes, different names
    print("Generating tab_export.tsv (tab-delimited, different naming)...")
    path = os.path.join(OUTPUT_DIR, "tab_export.tsv")
    with open(path, 'w', newline='', encoding='utf-8') as f:
        f.write("ref_num\ttxn_date\tnet_amt\tdept\tterritory\n")
        for i in range(N_ROWS):
            f.write(f"{base_ids[i] + 600}\t{base_dates[i].strftime('%m/%d/%Y')}\t{base_amounts[i]:.2f}\t{base_categories[i]}\t{base_regions[i]}\n")
    print(f"  Wrote {path} ({N_ROWS} rows)")

    # File 9: raw_export.txt - No header row, pipe-delimited, column positions differ
    print("Generating raw_export.txt (no header, pipe-delimited, different column order)...")
    path = os.path.join(OUTPUT_DIR, "raw_export.txt")
    with open(path, 'w', newline='', encoding='utf-8') as f:
        # No header! Columns in order: date|region|category|amount|id
        for i in range(N_ROWS):
            f.write(f"{base_dates[i].strftime('%d/%m/%Y')}|{base_regions[i]}|{base_categories[i]}|{base_amounts[i]:.2f}|{base_ids[i] + 800}\n")
    print(f"  Wrote {path} ({N_ROWS} rows)")

    print("\nAll CSV/TSV files generated successfully!")


if __name__ == "__main__":
    main()