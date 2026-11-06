# Tests for sales analytics pipeline
import os
import csv


def test_output_file_exists():
    """Test that the output file was created."""
    assert os.path.exists("/app/output/monthly_revenue.csv"), \
        "Output file /app/output/monthly_revenue.csv does not exist"


def test_output_structure():
    """Test that the output file has the correct structure."""
    with open("/app/output/monthly_revenue.csv", "r") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        
        assert fieldnames is not None, "Output file has no headers"
        assert "month" in fieldnames, "Missing 'month' column"
        assert "category" in fieldnames, "Missing 'category' column"
        assert "total_revenue" in fieldnames, "Missing 'total_revenue' column"
        
        rows = list(reader)
        assert len(rows) > 0, "Output file is empty"


def test_correct_months():
    """Test that all expected months are present."""
    with open("/app/output/monthly_revenue.csv", "r") as f:
        reader = csv.DictReader(f)
        months = set(row["month"] for row in reader)
        
        expected_months = {"2024-01", "2024-02", "2024-03"}
        assert months == expected_months, \
            f"Expected months {expected_months}, got {months}"


def test_correct_categories():
    """Test that categories are properly cleaned (no hidden Unicode)."""
    with open("/app/output/monthly_revenue.csv", "r") as f:
        reader = csv.DictReader(f)
        categories = set(row["category"] for row in reader)
        
        # Should only have 3 categories (Electronics, Clothing, Home & Garden)
        # The zero-width space variant should be merged with Electronics
        assert "Electronics" in categories, "Missing 'Electronics' category"
        assert "Clothing" in categories, "Missing 'Clothing' category"
        assert "Home & Garden" in categories, "Missing 'Home & Garden' category"
        
        # Check for hidden Unicode characters
        for cat in categories:
            # Zero-width space (U+200B) should not be present
            assert "\u200b" not in cat, \
                f"Category '{cat}' contains hidden zero-width space character"
        
        # Should not have the corrupted category
        assert len(categories) == 3, \
            f"Expected 3 categories, got {len(categories)}: {categories}"


def test_revenue_is_numeric():
    """Test that total_revenue values are numeric."""
    with open("/app/output/monthly_revenue.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                float(row["total_revenue"])
            except ValueError:
                assert False, f"Non-numeric revenue value: {row['total_revenue']}"


def test_january_electronics_revenue():
    """Test January Electronics revenue is correct."""
    with open("/app/output/monthly_revenue.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["month"] == "2024-01" and row["category"] == "Electronics":
                # The correct value after fixing all bugs:
                # - Removing $ prefix from revenues
                # - Merging Electronics and Electro​nics (with zero-width space)
                # - Excluding invalid dates
                # - Properly calculating net revenue with discounts
                expected = 3940  # Corrected value
                actual = float(row["total_revenue"])
                assert abs(actual - expected) < 1, \
                    f"January Electronics revenue: expected {expected}, got {actual}"
                return
    assert False, "January Electronics row not found"


def test_february_clothing_revenue():
    """Test February Clothing revenue is correct."""
    with open("/app/output/monthly_revenue.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["month"] == "2024-02" and row["category"] == "Clothing":
                expected = 5100  # Corrected value
                actual = float(row["total_revenue"])
                assert abs(actual - expected) < 1, \
                    f"February Clothing revenue: expected {expected}, got {actual}"
                return
    assert False, "February Clothing row not found"


def test_march_home_garden_revenue():
    """Test March Home & Garden revenue is correct."""
    with open("/app/output/monthly_revenue.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["month"] == "2024-03" and row["category"] == "Home & Garden":
                expected = 5280  # Corrected value
                actual = float(row["total_revenue"])
                assert abs(actual - expected) < 1, \
                    f"March Home & Garden revenue: expected {expected}, got {actual}"
                return
    assert False, "March Home & Garden row not found"


def test_total_rows():
    """Test that the output has the correct number of rows (3 months x 3 categories)."""
    with open("/app/output/monthly_revenue.csv", "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 9, \
            f"Expected 9 rows (3 months x 3 categories), got {len(rows)}"
