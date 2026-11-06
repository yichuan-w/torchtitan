"""Tests for the multi-format product catalog merge task.

All tests verify the END STATE after the solver has processed the data files.
"""

import csv
import json
import os
from pathlib import Path


def test_catalog_exists():
    """Verify catalog.csv exists and is a non-empty file."""
    catalog = Path("/app/output/catalog.csv")
    assert catalog.exists(), "/app/output/catalog.csv does not exist"
    assert catalog.is_file(), "/app/output/catalog.csv is not a file"
    assert catalog.stat().st_size > 0, "/app/output/catalog.csv is empty"


def test_catalog_headers():
    """Verify catalog.csv has the correct column headers in the right order."""
    with open("/app/output/catalog.csv", "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
    
    expected = [
        "product_id", "name", "category", "price", "cost",
        "supplier", "stock", "warehouse", "price_tier_qty_break", "last_updated"
    ]
    assert headers == expected, (
        f"Expected headers {expected}, got {headers}"
    )


def test_catalog_product_count():
    """Verify catalog has exactly 22 unique products (all products from all sources)."""
    with open("/app/output/catalog.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    # There should be 22 unique products across all sources
    # (PROD-001 through PROD-022; PROD-022 is a discontinued product
    # that only appears in suppliers.csv.gz)
    product_ids = {row["product_id"].strip() for row in rows}
    
    assert len(rows) == 22, (
        f"Expected 22 product rows, got {len(rows)}. "
        f"Product IDs present: {sorted(product_ids)}"
    )
    
    # Verify all expected product IDs are present
    expected_ids = {f"PROD-{i:03d}" for i in range(1, 23)}
    missing = expected_ids - product_ids
    extra = product_ids - expected_ids
    assert not missing, f"Missing product IDs: {sorted(missing)}"
    assert not extra, f"Unexpected product IDs: {sorted(extra)}"


def test_catalog_no_bom_in_output():
    """Verify the output CSV does NOT have a BOM (solver should strip it)."""
    with open("/app/output/catalog.csv", "rb") as f:
        first_bytes = f.read(3)
    assert first_bytes != b'\xef\xbb\xbf', (
        "Output catalog.csv contains a BOM marker — it should be stripped"
    )


def test_latin1_characters_preserved():
    """Verify Latin-1 special characters from legacy TSV are correctly preserved as UTF-8."""
    with open("/app/output/catalog.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {row["product_id"].strip(): row for row in reader}
    
    # Check accented characters from legacy TSV
    # PROD-016: Papelería González
    prod_016 = rows.get("PROD-016")
    assert prod_016 is not None, "PROD-016 missing from catalog"
    assert "Papeler" in prod_016["supplier"], (
        f"PROD-016 supplier should contain 'Papelería González', got '{prod_016['supplier']}'"
    )
    # The í and á should be preserved as proper UTF-8
    assert "\u00ed" in prod_016["supplier"] or "í" in prod_016["supplier"], (
        f"PROD-016 supplier missing accented 'í': '{prod_016['supplier']}'"
    )
    assert "\u00e1" in prod_016["supplier"] or "á" in prod_016["supplier"], (
        f"PROD-016 supplier missing accented 'á': '{prod_016['supplier']}'"
    )
    
    # PROD-017: Müller Kaffee GmbH
    prod_017 = rows.get("PROD-017")
    assert prod_017 is not None, "PROD-017 missing from catalog"
    assert "\u00fc" in prod_017["supplier"] or "ü" in prod_017["supplier"], (
        f"PROD-017 supplier missing 'ü': '{prod_017['supplier']}'"
    )
    
    # PROD-020: Té Verde Orgánico
    prod_020 = rows.get("PROD-020")
    assert prod_020 is not None, "PROD-020 missing from catalog"
    assert "\u00e9" in prod_020["name"] or "é" in prod_020["name"], (
        f"PROD-020 name missing 'é': '{prod_020['name']}'"
    )


def test_catalog_primary_source_preference():
    """Verify that main CSV values are preferred over legacy TSV when available."""
    with open("/app/output/catalog.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {row["product_id"].strip(): row for row in reader}
    
    # PROD-001: main CSV says name="Wireless Mouse", price=29.99
    # legacy TSV says name="Wireless Mouse Pro", price=32.50
    prod = rows.get("PROD-001")
    assert prod is not None, "PROD-001 missing from catalog"
    assert prod["name"] == "Wireless Mouse", (
        f"PROD-001 name should be 'Wireless Mouse' (from main CSV), got '{prod['name']}'"
    )
    assert float(prod["price"]) == 29.99, (
        f"PROD-001 price should be 29.99 (from main CSV), got {prod['price']}"
    )
    
    # PROD-003: main CSV says price=45.00, legacy TSV says price=48.00
    prod = rows.get("PROD-003")
    assert prod is not None, "PROD-003 missing from catalog"
    assert float(prod["price"]) == 45.00, (
        f"PROD-003 price should be 45.00 (from main CSV), got {prod['price']}"
    )


def test_catalog_fallback_to_secondary_source():
    """Verify fallback to secondary source when main CSV doesn't have a product."""
    with open("/app/output/catalog.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {row["product_id"].strip(): row for row in reader}
    
    # PROD-016: only in legacy TSV and inventory JSON, not in main CSV
    prod = rows.get("PROD-016")
    assert prod is not None, "PROD-016 missing from catalog"
    assert prod["name"] != "", f"PROD-016 should have a name from legacy TSV"
    assert "Papel" in prod["name"] or "Resma" in prod["name"], (
        f"PROD-016 name should come from legacy TSV, got '{prod['name']}'"
    )
    
    # PROD-021: only in inventory JSON
    prod = rows.get("PROD-021")
    assert prod is not None, "PROD-021 missing from catalog"
    assert prod["stock"] != "", f"PROD-021 should have stock from inventory JSON"
    assert int(float(prod["stock"])) == 150, (
        f"PROD-021 stock should be 150, got {prod['stock']}"
    )


def test_catalog_inventory_integration():
    """Verify inventory data (stock, warehouse) is correctly merged, including
    products matched via normalized IDs."""
    with open("/app/output/catalog.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {row["product_id"].strip(): row for row in reader}
    
    # PROD-001: stock=145, warehouse=WH-A
    prod = rows.get("PROD-001")
    assert prod is not None
    assert int(float(prod["stock"])) == 145, (
        f"PROD-001 stock should be 145, got {prod['stock']}"
    )
    assert prod["warehouse"] == "WH-A", (
        f"PROD-001 warehouse should be WH-A, got {prod['warehouse']}"
    )
    
    # PROD-010: missing stock in inventory JSON (should be empty)
    prod = rows.get("PROD-010")
    assert prod is not None
    assert prod["stock"] == "", (
        f"PROD-010 stock should be empty (missing from inventory), got '{prod['stock']}'"
    )
    # But warehouse should still be there
    assert prod["warehouse"] == "WH-B", (
        f"PROD-010 warehouse should be WH-B, got '{prod['warehouse']}'"
    )
    
    # PROD-017: has stock=32, warehouse=WH-C from inventory (trailing space in ID)
    prod = rows.get("PROD-017")
    assert prod is not None
    assert int(float(prod["stock"])) == 32, (
        f"PROD-017 stock should be 32, got {prod['stock']}"
    )
    assert prod["warehouse"] == "WH-C", (
        f"PROD-017 warehouse should be WH-C, got {prod['warehouse']}"
    )


def test_catalog_pricing_integration():
    """Verify pricing tier data is correctly merged."""
    with open("/app/output/catalog.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {row["product_id"].strip(): row for row in reader}
    
    # PROD-003: qty_break=5 from pricing.txt
    prod = rows.get("PROD-003")
    assert prod is not None
    assert int(float(prod["price_tier_qty_break"])) == 5, (
        f"PROD-003 price_tier_qty_break should be 5, got {prod['price_tier_qty_break']}"
    )
    
    # PROD-009: qty_break=10 from pricing.txt
    prod = rows.get("PROD-009")
    assert prod is not None
    assert int(float(prod["price_tier_qty_break"])) == 10, (
        f"PROD-009 price_tier_qty_break should be 10, got {prod['price_tier_qty_break']}"
    )
    
    # PROD-001: qty_break=1 from pricing.txt
    prod = rows.get("PROD-001")
    assert prod is not None
    assert int(float(prod["price_tier_qty_break"])) == 1, (
        f"PROD-001 price_tier_qty_break should be 1, got {prod['price_tier_qty_break']}"
    )


def test_corrupt_rows_not_in_catalog():
    """Verify corrupt/malformed rows are NOT included in the output."""
    with open("/app/output/catalog.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {row["product_id"].strip(): row for row in reader}
    
    # PROD-011 in legacy TSV has missing vendor field (corrupt row)
    # It should still be in catalog (from main CSV), but the legacy corrupt data
    # should not have corrupted the main data
    prod = rows.get("PROD-011")
    assert prod is not None, "PROD-011 should still be present (from main CSV)"
    assert prod["name"] == "Noise-Cancelling Headphones", (
        f"PROD-011 name should be from main CSV, got '{prod['name']}'"
    )


def test_issues_json_exists():
    """Verify issues.json exists and is valid JSON."""
    issues_file = Path("/app/output/issues.json")
    assert issues_file.exists(), "/app/output/issues.json does not exist"
    
    with open(issues_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    assert isinstance(data, list), "issues.json should contain a JSON array"
    assert len(data) > 0, "issues.json should contain at least one issue"


def test_issues_json_structure():
    """Verify each issue has the required fields."""
    with open("/app/output/issues.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    
    required_fields = {"file", "line", "severity", "field", "description"}
    for i, issue in enumerate(data):
        missing = required_fields - set(issue.keys())
        assert not missing, (
            f"Issue at index {i} missing required fields: {missing}. "
            f"Has: {list(issue.keys())}"
        )
        assert issue["severity"] in ("warning", "error"), (
            f"Issue {i}: severity should be 'warning' or 'error', got '{issue['severity']}'"
        )


def test_issues_json_covers_known_problems():
    """Verify issue entries use the source/location/field/description contract."""
    with open("/app/output/issues.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    source_files = {
        "products_main.csv", "products_legacy.tsv", "inventory.json",
        "pricing.txt", "product_details.xml", "suppliers.csv.gz",
    }
    for i, issue in enumerate(data):
        assert issue["file"] in source_files, (
            f"Issue {i}: file should be a source filename, got {issue['file']!r}"
        )
        assert issue["line"] is None or (
            isinstance(issue["line"], int)
            and not isinstance(issue["line"], bool)
            and issue["line"] >= 0
        ), f"Issue {i}: line should be an approximate record index or null"
        assert issue["field"] is None or isinstance(issue["field"], str), (
            f"Issue {i}: field should be a field name or null"
        )
        assert isinstance(issue["description"], str) and issue["description"].strip(), (
            f"Issue {i}: description should be a human-readable explanation"
        )


def test_category_normalization():
    """Verify that categories are standardized across sources.
    
    The legacy TSV uses "Office" while the main CSV uses "Office Supplies".
    The instruction says to standardize to the primary system's taxonomy.
    """
    with open("/app/output/catalog.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {row["product_id"].strip(): row for row in reader}
    
    # PROD-008: main CSV says "Office Supplies", legacy says "Office" -> should be "Office Supplies"
    prod = rows.get("PROD-008")
    assert prod is not None
    assert prod["category"] == "Office Supplies", (
        f"PROD-008 category should be 'Office Supplies' (standardized), got '{prod['category']}'"
    )
    
    # PROD-010: main CSV says "Office Supplies", legacy says "Office"
    prod = rows.get("PROD-010")
    assert prod is not None
    assert prod["category"] == "Office Supplies", (
        f"PROD-010 category should be 'Office Supplies' (standardized), got '{prod['category']}'"
    )
    
    # PROD-018: only in legacy TSV, category "Office" -> should be standardized to "Office Supplies"
    prod = rows.get("PROD-018")
    assert prod is not None
    assert prod["category"] == "Office Supplies", (
        f"PROD-018 category should be 'Office Supplies' (standardized from 'Office'), "
        f"got '{prod['category']}'"
    )
    
    # PROD-017: only in legacy TSV, category "Pantry" -> no primary equivalent, keep as-is
    prod = rows.get("PROD-017")
    assert prod is not None
    assert prod["category"] == "Pantry", (
        f"PROD-017 category should remain 'Pantry' (no primary equivalent), got '{prod['category']}'"
    )


def test_catalog_no_malformed_product_ids():
    """Verify all product IDs in catalog match the expected PROD-NNN format."""
    with open("/app/output/catalog.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    import re
    for row in rows:
        pid = row["product_id"].strip()
        assert re.match(r'^PROD-\d{3}$', pid), (
            f"Product ID '{pid}' does not match expected format PROD-NNN"
        )


def test_pricing_tier_data_integration():
    """Verify pricing tier qty_break values are numeric or empty (not corrupt text)."""
    with open("/app/output/catalog.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    for row in rows:
        qty = row.get("price_tier_qty_break", "").strip()
        if qty:
            try:
                val = float(qty)
                assert val == int(val), (
                    f"price_tier_qty_break for {row['product_id']} should be integer: {qty}"
                )
                assert val > 0, (
                    f"price_tier_qty_break for {row['product_id']} should be positive: {qty}"
                )
            except ValueError:
                raise AssertionError(
                    f"price_tier_qty_break for {row['product_id']} is not numeric: '{qty}'"
                )


def test_price_discrepancies_exists():
    """Verify price_discrepancies.csv exists."""
    disc_file = Path("/app/output/price_discrepancies.csv")
    assert disc_file.exists(), "/app/output/price_discrepancies.csv does not exist"
    assert disc_file.is_file(), "/app/output/price_discrepancies.csv is not a file"


def test_price_discrepancies_headers():
    """Verify price_discrepancies.csv has correct headers."""
    with open("/app/output/price_discrepancies.csv", "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
    expected = ["product_id", "catalog_price", "tier1_price", "difference_pct"]
    assert headers == expected, (
        f"Expected headers {expected}, got {headers}"
    )


def test_price_discrepancies_products_covered():
    """Verify all products present in BOTH main CSV and pricing.txt appear in the report."""
    with open("/app/output/price_discrepancies.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        disc_rows = {row["product_id"].strip(): row for row in reader}
    
    # Products that appear in BOTH products_main.csv AND pricing.txt:
    # PROD-001, 002, 003, 004, 005, 007, 009, 012, 015
    expected_pids = {"PROD-001", "PROD-002", "PROD-003", "PROD-004", "PROD-005",
                     "PROD-007", "PROD-009", "PROD-012", "PROD-015"}
    
    actual_pids = set(disc_rows.keys())
    
    missing = expected_pids - actual_pids
    extra = actual_pids - expected_pids
    
    assert not missing, (
        f"Missing products in price_discrepancies.csv: {sorted(missing)}"
    )
    # Extra products are acceptable (solver might include additional comparisons)


def test_price_discrepancies_tier1_na():
    """Verify products without tier-1 pricing have N/A for tier1_price."""
    with open("/app/output/price_discrepancies.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        disc_rows = {row["product_id"].strip(): row for row in reader}
    
    # PROD-003 has qty_break=5 (not tier 1) → tier1_price should be N/A
    prod = disc_rows.get("PROD-003")
    assert prod is not None, "PROD-003 should be in price_discrepancies"
    assert prod["tier1_price"].strip().upper() == "N/A", (
        f"PROD-003 tier1_price should be N/A (no tier-1 pricing), got '{prod['tier1_price']}'"
    )
    
    # PROD-009 has qty_break=10 (not tier 1) → tier1_price should be N/A
    prod = disc_rows.get("PROD-009")
    assert prod is not None, "PROD-009 should be in price_discrepancies"
    assert prod["tier1_price"].strip().upper() == "N/A", (
        f"PROD-009 tier1_price should be N/A (no tier-1 pricing), got '{prod['tier1_price']}'"
    )


def test_price_discrepancies_valid_comparisons():
    """Verify products with tier-1 pricing have valid numeric comparisons."""
    with open("/app/output/price_discrepancies.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        disc_rows = {row["product_id"].strip(): row for row in reader}
    
    # PROD-001: main price=29.99, pricing tier1=29.99 → diff should be 0.0
    prod = disc_rows.get("PROD-001")
    assert prod is not None
    assert prod["tier1_price"].strip().upper() != "N/A", "PROD-001 should have tier1 price"
    assert float(prod["catalog_price"]) == 29.99
    assert float(prod["tier1_price"]) == 29.99
    assert abs(float(prod["difference_pct"])) < 0.1, (
        f"PROD-001 diff should be ~0.0%, got {prod['difference_pct']}%"
    )
    
    # PROD-004: main price=399.99, pricing tier1=399.99
    prod = disc_rows.get("PROD-004")
    assert prod is not None
    assert abs(float(prod["difference_pct"])) < 0.1, (
        f"PROD-004 diff should be ~0.0%, got {prod['difference_pct']}%"
    )
