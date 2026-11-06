import json
import os
import sys
from pathlib import Path


def test_report_file_exists():
    """Test whether the report.jsonl file was successfully created."""
    report_path = Path("/app/report.jsonl")
    assert report_path.exists(), f"File {report_path} does not exist"


def test_vulnerability_file_identified():
    """Test whether the vulnerable file is correctly identified."""
    report_path = Path("/app/report.jsonl")
    
    file_paths = []
    with open(report_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                item = json.loads(line)
                file_path = item.get("file_path", "")
                file_paths.append(file_path)
    
    assert "/app/app.py" in file_paths, "'/app/app.py' not found in report"


def test_cwe_id_identified():
    """Test whether the correct CWE ID is identified."""
    report_path = Path("/app/report.jsonl")
    
    report = {}
    with open(report_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                item = json.loads(line)
                file_path = item.get("file_path", "")
                cwe_ids = item.get("cwe_id", [])
                cwe_ids = [str(cwe_id).lower() for cwe_id in cwe_ids]
                
                if file_path in report.keys():
                    report[file_path] = report[file_path] + cwe_ids
                else:
                    report[file_path] = cwe_ids
    
    # CWE-918 is SSRF
    assert "cwe-918" in report.get("/app/app.py", []), "'cwe-918' not found in /app/app.py report"


def test_bypass_url_with_credentials():
    """Test that URL parsing bypass with credentials is blocked.
    
    This tests the CVE-2025-0454 style bypass where urlparse and requests
    interpret URLs differently due to @ in credentials.
    """
    sys.path.insert(0, "/app")
    from app import validate_url
    
    # This URL has urlparse seeing google.com as hostname
    # but requests would connect to localhost after processing
    bypass_url = "http://localhost:\\@google.com/../metadata"
    
    is_valid, parsed, error = validate_url(bypass_url)
    assert not is_valid, f"Bypass URL should be blocked: {bypass_url}"


def test_bypass_url_with_user_password():
    """Test bypass with user:password@host pattern."""
    sys.path.insert(0, "/app")
    from app import validate_url
    
    # urlparse extracts hostname after @, but requests connects to the host before @
    # This allows bypassing the check
    bypass_variants = [
        "http://localhost:password@google.com/../metadata",
        "http://localhost:@google.com/../metadata",
    ]
    
    for url in bypass_variants:
        is_valid, parsed, error = validate_url(url)
        assert not is_valid, f"Bypass URL should be blocked: {url}"


def test_blocked_aws_metadata():
    """Test that AWS metadata endpoint is blocked."""
    sys.path.insert(0, "/app")
    from app import validate_url
    
    is_valid, parsed, error = validate_url("http://169.254.169.254/latest/meta-data/")
    assert not is_valid, "AWS metadata endpoint should be blocked"


def test_ipv6_localhost_blocked():
    """Test that IPv6 localhost is blocked."""
    sys.path.insert(0, "/app")
    from app import validate_url
    
    is_valid, parsed, error = validate_url("http://[::1]:8081/metadata")
    assert not is_valid, "IPv6 localhost ::1 should be blocked"


def test_zero_ip_blocked():
    """Test that 0.0.0.0 is blocked."""
    sys.path.insert(0, "/app")
    from app import validate_url
    
    is_valid, parsed, error = validate_url("http://0.0.0.0:8081/metadata")
    assert not is_valid, "0.0.0.0 should be blocked"


def test_subdomain_of_blocked_host():
    """Test that subdomains of blocked hosts are also blocked."""
    sys.path.insert(0, "/app")
    from app import validate_url
    
    blocked_subdomains = [
        "http://sub.localhost/metadata",
        "http://test.localhost/test",
    ]
    
    for url in blocked_subdomains:
        is_valid, parsed, error = validate_url(url)
        assert not is_valid, f"Subdomain of blocked host should be blocked: {url}"
