"""Tests for customer data integration task."""

import csv
import json
import os
import re
from pathlib import Path


def test_unified_csv_exists():
    """Test that the unified CSV output file exists."""
    path = Path("/app/customers_unified.csv")
    assert path.exists(), "/app/customers_unified.csv should exist"
    assert path.is_file(), "/app/customers_unified.csv should be a file"


def test_dedup_report_exists():
    """Test that the dedup report JSON exists."""
    path = Path("/app/dedup_report.json")
    assert path.exists(), "/app/dedup_report.json should exist"
    assert path.is_file(), "/app/dedup_report.json should be a file"


def test_data_quality_report_exists():
    """Test that the data quality report JSON exists."""
    path = Path("/app/data_quality_report.json")
    assert path.exists(), "/app/data_quality_report.json should exist"
    assert path.is_file(), "/app/data_quality_report.json should be a file"


def test_data_quality_report_structure():
    """Test that the data quality report has expected structure."""
    with open("/app/data_quality_report.json") as f:
        report = json.load(f)

    assert isinstance(report, dict), "quality report should be a JSON object"
    assert "total_issues_found" in report, (
        "quality report must have total_issues_found"
    )
    assert isinstance(report["total_issues_found"], int), (
        "total_issues_found must be an integer"
    )
    assert report["total_issues_found"] > 0, (
        "there should be at least some issues found"
    )


def test_data_quality_report_date_issues():
    """Test that the quality report has date_format_issues with correct structure."""
    with open("/app/data_quality_report.json") as f:
        report = json.load(f)

    assert "date_format_issues" in report, (
        "quality report should have date_format_issues"
    )
    date_issues = report["date_format_issues"]
    assert isinstance(date_issues, list), "date_format_issues should be a list"
    assert len(date_issues) >= 4, (
        f"Expected at least 4 date format issues, got {len(date_issues)}"
    )
    for issue in date_issues:
        assert isinstance(issue, dict), f"Each date issue should be a dict, got {type(issue)}"
        assert "record_id" in issue, f"Missing record_id in: {issue}"
        assert "original_value" in issue, f"Missing original_value in: {issue}"
        assert "normalized_to" in issue, f"Missing normalized_to in: {issue}"


def test_no_title_prefixes_in_output():
    """Test that first_name fields have no title prefixes."""
    titles = {"Mr.", "Mrs.", "Ms.", "Dr.", "Prof."}
    with open("/app/customers_unified.csv", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fn = row["first_name"].strip()
            first_word = fn.split()[0] if fn else ""
            assert first_word not in titles, (
                f"Title prefix '{first_word}' found in first_name for {row['email']}"
            )


def test_nested_name_objects_handled():
    """Test that customers from nested name objects appear in output."""
    with open("/app/customers_unified.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    heather_found = False
    derek_found = False
    for row in rows:
        if (row["first_name"] == "Heather" and row["last_name"] == "Morris"
                and row["email"] == "heather.m@email.com"):
            heather_found = True
        if (row["first_name"] == "Derek" and row["last_name"] == "Wong"
                and row["email"] == "derek.wong@email.com"):
            derek_found = True

    assert heather_found, "Heather Morris (nested name in W0007) not found"
    assert derek_found, "Derek Wong (nested name in W0008) not found"


def test_unified_csv_headers():
    """Test that the CSV has the correct headers in order."""
    with open("/app/customers_unified.csv", newline="") as f:
        reader = csv.reader(f)
        headers = next(reader)

    expected = [
        "unified_id", "first_name", "last_name", "email", "phone",
        "address", "city", "state", "zip", "country",
        "signup_date", "source_systems",
    ]
    assert headers == expected, (
        f"Expected headers {expected}, got {headers}"
    )


def test_unified_csv_row_count():
    """Test that there are exactly 62 unique customers after dedup."""
    with open("/app/customers_unified.csv", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 70, (
        f"Expected 70 unique customer rows, got {len(rows)}"
    )


def test_unified_ids_are_unique():
    """Test that all unified_ids are unique and follow the pattern."""
    with open("/app/customers_unified.csv", newline="") as f:
        reader = csv.DictReader(f)
        ids = [row["unified_id"] for row in reader]

    assert len(ids) == len(set(ids)), "unified_ids are not unique"

    for uid in ids:
        assert uid.startswith("U"), f"unified_id {uid} should start with 'U'"
        num_part = uid[1:]
        assert num_part.isdigit(), f"unified_id {uid} should have numeric suffix"
        assert len(num_part) >= 4, f"unified_id {uid} should have at least 4 digits"


def test_no_garbled_characters():
    """Test that no mojibake/garbled characters remain in the output."""
    with open("/app/customers_unified.csv", newline="", encoding="utf-8") as f:
        content = f.read()

    garbled_indicators = [
        "Ã©", "Ã¨", "Ã¼", "Ã¶", "Ã±", "Ã£", "Ã¤", "Ã¥",
    ]
    for pattern in garbled_indicators:
        assert pattern not in content, (
            f"Found garbled text pattern '{pattern}' in output — "
            "encoding issue not fixed"
        )


def test_known_correct_names():
    """Test that specific names with special characters are correct."""
    with open("/app/customers_unified.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    jose_found = False
    francois_found = False
    soren_found = False

    for row in rows:
        fn = row["first_name"]
        ln = row["last_name"]
        if fn == "José" and ln == "Ramírez":
            jose_found = True
        if fn == "François" and ln == "Bélanger":
            francois_found = True
        if fn == "Sören" and ln == "Müller":
            soren_found = True

    assert jose_found, "José Ramírez not found with correct encoding"
    assert francois_found, "François Bélanger not found with correct encoding"
    assert soren_found, "Sören Müller not found with correct encoding"


def test_country_codes_normalized():
    """Test that country codes are normalized to 2-letter codes."""
    with open("/app/customers_unified.csv", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    valid_countries = {"US", "CA", "DE", "FR", "SE", "MX", "PT"}
    for row in rows:
        country = row["country"].strip()
        assert country in valid_countries, (
            f"Country '{country}' for {row['email']} is not a valid "
            f"2-letter code. Expected one of {valid_countries}"
        )


def test_source_systems_field():
    """Test that source_systems field contains valid system names."""
    valid_systems = {"mainframe", "crm", "web", "support"}
    with open("/app/customers_unified.csv", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sources = set(s.strip() for s in row["source_systems"].split(","))
            assert sources.issubset(valid_systems), (
                f"Invalid source system for {row['email']}: {sources}"
            )
            assert len(sources) >= 1, (
                f"Empty source_systems for {row['email']}"
            )


def test_james_wilson_merged_from_all_systems():
    """Test that James Wilson is merged from all four source systems."""
    with open("/app/customers_unified.csv", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["email"].strip().lower() == "james.wilson@email.com":
                sources = set(s.strip() for s in row["source_systems"].split(","))
                assert sources == {"mainframe", "crm", "web", "support"}, (
                    f"James Wilson should be from all 4 systems, got {sources}"
                )
                break
        else:
            assert False, "James Wilson not found in unified CSV"


def test_dedup_report_structure():
    """Test that dedup_report.json has correct structure."""
    with open("/app/dedup_report.json") as f:
        report = json.load(f)

    assert isinstance(report, dict), "dedup_report should be a JSON object"
    assert len(report) == 70, (
        f"Expected 70 entries in dedup report, got {len(report)}"
    )

    for uid, info in report.items():
        assert uid.startswith("U"), f"Key {uid} should start with 'U'"
        assert "merged_from" in info, f"Missing 'merged_from' for {uid}"
        assert "email" in info, f"Missing 'email' for {uid}"
        assert isinstance(info["merged_from"], list), (
            f"merged_from for {uid} should be a list"
        )
        assert len(info["merged_from"]) >= 1, (
            f"merged_from for {uid} should have at least 1 entry"
        )
        # Allow the one known invalid email
        if info["email"] == "steven.adams-email.com":
            continue
        assert "@" in info["email"], (
            f"email for {uid} should be a valid email, got {info['email']}"
        )


def test_dedup_report_james_wilson():
    """Test that James Wilson's dedup report entry shows all four sources."""
    with open("/app/dedup_report.json") as f:
        report = json.load(f)

    james_entry = None
    for uid, info in report.items():
        if info["email"] == "james.wilson@email.com":
            james_entry = (uid, info)
            break

    assert james_entry is not None, "James Wilson not found in dedup report"
    uid, info = james_entry
    merged = info["merged_from"]
    assert len(merged) == 4, (
        f"James Wilson should have 4 merged records, got {len(merged)}: {merged}"
    )
    assert "00000101" in merged, f"Mainframe ID 00000101 missing from {merged}"
    assert "CRM-001" in merged, f"CRM ID CRM-001 missing from {merged}"
    assert "W0001" in merged, f"Web ID W0001 missing from {merged}"
    assert "DB-001" in merged, f"Support DB ID DB-001 missing from {merged}"


def test_secondary_dedup_mark_wright():
    """Test secondary dedup: Mark Wright matched by name+address despite different emails."""
    with open("/app/dedup_report.json") as f:
        report = json.load(f)

    # Find the entry containing mark.wright@email.com
    found = None
    for uid, info in report.items():
        if info["email"] in ("mark.wright@email.com", "mark.wright.alt@email.com"):
            found = (uid, info)
            break

    assert found is not None, "Mark Wright not found in dedup report"
    uid, info = found
    merged = info["merged_from"]
    assert len(merged) >= 2, (
        f"Mark Wright should have at least 2 merged records (secondary match), "
        f"got {len(merged)}: {merged}"
    )
    assert "00000125" in merged, f"Mainframe ID 00000125 missing from {merged}"
    assert "CRM-031" in merged, f"CRM ID CRM-031 missing from {merged}"


def test_secondary_dedup_karen_davis():
    """Test secondary dedup: Karen Davis matched across CRM and Web despite different emails."""
    with open("/app/dedup_report.json") as f:
        report = json.load(f)

    found = None
    for uid, info in report.items():
        if info["email"] in ("karen.davis@email.com", "karen.davis.home@email.com"):
            found = (uid, info)
            break

    assert found is not None, "Karen Davis not found in dedup report"
    uid, info = found
    merged = info["merged_from"]
    assert len(merged) >= 2, (
        f"Karen Davis should have at least 2 merged records (secondary match), "
        f"got {len(merged)}: {merged}"
    )
    assert "CRM-011" in merged, f"CRM ID CRM-011 missing from {merged}"
    assert "W0027" in merged, f"Web ID W0027 missing from {merged}"


def test_signup_date_format():
    """Test that all signup_date values are in YYYY-MM-DD format."""
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    with open("/app/customers_unified.csv", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_val = row["signup_date"].strip()
            assert date_pattern.match(date_val), (
                f"signup_date '{date_val}' for {row['email']} "
                "is not in YYYY-MM-DD format"
            )


def test_all_emails_are_valid():
    """Test that all email addresses contain @ and look valid,
    except for the one known invalid email that was flagged in the quality report."""
    with open("/app/customers_unified.csv", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row["email"].strip()
            # The one known invalid email (no @) is intentionally included
            if email == "steven.adams-email.com":
                continue
            assert "@" in email, f"Invalid email '{email}'"
            assert "." in email.split("@")[1], (
                f"Email '{email}' missing domain dot"
            )


def test_csv_is_valid_utf8():
    """Test that the unified CSV is valid UTF-8 (no encoding errors)."""
    with open("/app/customers_unified.csv", "rb") as f:
        content = f.read()
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as e:
        assert False, f"Unified CSV is not valid UTF-8: {e}"


def test_invalid_email_in_quality_report():
    """Test that the invalid email (no @ sign) is flagged in quality report."""
    with open("/app/data_quality_report.json") as f:
        report = json.load(f)

    assert "invalid_emails" in report, (
        "quality report should have invalid_emails"
    )
    invalid = report["invalid_emails"]
    assert isinstance(invalid, list), "invalid_emails should be a list"
    found = any("steven.adams-email.com" in str(e) for e in invalid)
    assert found, (
        f"Expected 'steven.adams-email.com' in invalid_emails, got {invalid}"
    )


def test_theresa_scott_not_merged_across_different_addresses():
    """Test that Theresa Scott with different addresses are separate customers."""
    # There should be TWO distinct entries for Theresa Scott:
    # 1. CRM-026 + CRM-032 (same address: 3301 SE Belmont St, Portland)
    # 2. CRM-034 (different address: 45 Rockefeller Plaza, New York)
    with open("/app/dedup_report.json") as f:
        report = json.load(f)

    theresa_entries = []
    for uid, info in report.items():
        merged = info["merged_from"]
        if "CRM-026" in merged or "CRM-032" in merged or "CRM-034" in merged:
            theresa_entries.append((uid, info))

    # CRM-034 should be in a DIFFERENT entry from CRM-026/CRM-032
    assert len(theresa_entries) == 2, (
        f"Expected 2 separate Theresa Scott entries, got {len(theresa_entries)}: "
        f"{theresa_entries}"
    )

    # CRM-026 and CRM-032 should be together (same address)
    # CRM-034 should be separate (different address)
    found_034_with_026 = False
    for uid, info in theresa_entries:
        merged = info["merged_from"]
        if "CRM-034" in merged and ("CRM-026" in merged or "CRM-032" in merged):
            found_034_with_026 = True

    assert not found_034_with_026, (
        "CRM-034 (45 Rockefeller Plaza) should NOT be merged with "
        "CRM-026/CRM-032 (3301 SE Belmont St) — same name, different address"
    )


def test_corrupt_records_excluded():
    """Test that corrupt records were excluded from the output."""
    with open("/app/dedup_report.json") as f:
        report = json.load(f)

    # Collect all original IDs that appear in the dedup report
    all_ids = set()
    for uid, info in report.items():
        for rid in info["merged_from"]:
            all_ids.add(rid)

    # Corrupt records should NOT appear
    assert "00000999" not in all_ids, "Corrupt mainframe record 00000999 should be excluded"
    assert "00000998" not in all_ids, "Corrupt mainframe record 00000998 should be excluded"
    assert "CRM-998" not in all_ids, "Corrupt CRM record CRM-998 should be excluded"
    assert "W0999" not in all_ids, "Malformed web record W0999 should be excluded"


def test_phone_based_dedup_raymond_chen():
    """Test that Raymond Chen records are merged by phone number."""
    with open("/app/dedup_report.json") as f:
        report = json.load(f)

    found = None
    for uid, info in report.items():
        if info["email"] in ("raymond.chen@email.com", "ray.chen@email.com"):
            found = (uid, info)
            break

    assert found is not None, "Raymond Chen not found in dedup report"
    uid, info = found
    merged = info["merged_from"]
    assert len(merged) >= 2, (
        f"Raymond Chen should have at least 2 merged records (phone match), "
        f"got {len(merged)}: {merged}"
    )
    assert "CRM-028" in merged, f"CRM ID CRM-028 missing from {merged}"
    assert "W0028" in merged, f"Web ID W0028 missing from {merged}"
