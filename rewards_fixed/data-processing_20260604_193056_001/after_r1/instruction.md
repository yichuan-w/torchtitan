A legacy ERP system has been decommissioned and its product catalog was exported as several files in the `/app/data/` directory. Each export came from a different subsystem and uses its own format:

- `products_main.csv` — The primary product catalog (CSV format)
- `products_legacy.tsv` — Older product records from a deprecated inventory module (TSV format)
- `inventory.json` — Warehouse stock levels from the warehouse management system (JSON format)
- `pricing.txt` — Tiered pricing data from the sales system (fixed-width text format, no header row)
- `product_details.xml` — Product specifications from the content management system (XML format)
- `suppliers.csv.gz` — Supplier-product mappings exported from the procurement module (compressed CSV)

The files partially overlap — some products appear in multiple files, some appear in only one. The data was exported under time pressure during the shutdown and contains quality issues: encoding problems, inconsistent column naming, different date and ID formats, inconsistent category naming conventions, corrupt rows, and conflicting values between sources.

Your job is to produce three output files:

### 1. `/app/output/catalog.csv`
A single CSV file merging all available information for every unique product. Include these columns (in this order): `product_id`, `name`, `category`, `price`, `cost`, `supplier`, `stock`, `warehouse`, `price_tier_qty_break`, `last_updated`.

### 2. `/app/output/issues.json`
A JSON array documenting every data quality problem you encounter and resolve during processing. Each issue object should have: `file` (source filename), `line` (approximate line number or record index, or null if file-level), `severity` ("warning" or "error"), `field` (affected field name or null), `description` (human-readable explanation of the issue and how it was handled).

### 3. `/app/output/price_discrepancies.csv`
Cross-reference the main catalog prices against the pricing data. Include every product that appears in BOTH `products_main.csv` AND `pricing.txt`. For each such product:
- If the product has tier-1 pricing in `pricing.txt` (qty_break = 1), compare the main CSV price against it. Compute the absolute percentage difference, rounded to 1 decimal place.
- If the product appears in pricing.txt but only has a non-tier-1 qty_break (e.g., 5, 10), mark `tier1_price` and `difference_pct` as "N/A".

Columns: `product_id`, `catalog_price`, `tier1_price`, `difference_pct`.

### Data merging rules

- **Product identity**: Match records across files by product ID. IDs may have inconsistent formatting (leading/trailing whitespace, and in rare cases hidden characters). Normalize before matching.
- **Conflict resolution**: When the same product field appears in multiple sources, prefer the value from `products_main.csv` (the primary system). If a field is empty or missing in the primary source, fall back to the secondary source with the most recent timestamp.
- **Category standardization**: Different systems use different category names for the same concepts (e.g. "Office" vs "Office Supplies"). Standardize all category values to a consistent taxonomy. The primary system defines the canonical names; map equivalent categories from other sources to match.
- **Corrupt or unparseable rows**: Skip them but document them in issues.json.
- **Missing fields**: Leave the field empty in the output — do not invent data.

### Known issues to watch for

The export contains real-world data quality problems. You'll need to investigate the files to discover them. A competent data engineer will spot encoding issues, schema inconsistencies, format problems, and corrupt records by examining the raw bytes and trying different parsers.
