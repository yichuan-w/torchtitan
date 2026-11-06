"""
Test suite for SSRF vulnerability fix in URL fetcher.
Tests verify that the fix properly blocks both direct and redirect-based SSRF attacks.
"""

import json
import os
import sys
import ipaddress
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from urllib.error import URLError


def test_report_file_exists():
    """Test whether the report.jsonl file was successfully created."""
    report_path = Path("/app/report.jsonl")
    assert report_path.exists(), f"File {report_path} does not exist"


def test_vulnerability_file_identified():
    """Test whether vulnerable files are successfully identified in report."""
    report_path = Path("/app/report.jsonl")

    file_paths = []
    with open(report_path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            file_path = item.get("file_path", "")
            file_paths.append(file_path)

    assert "/app/url_fetcher.py" in file_paths, "'/app/url_fetcher.py' not found in report"


def test_cwe_id_identified():
    """Test whether SSRF vulnerability type (CWE-918) is identified."""
    report_path = Path("/app/report.jsonl")

    report = {}
    with open(report_path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            file_path = item.get("file_path", "")
            cwe_ids = item.get("cwe_id", [])
            cwe_ids = [str(cwe_id).lower() for cwe_id in cwe_ids]

            if file_path in report.keys():
                report[file_path] = report[file_path] + cwe_ids
            else:
                report[file_path] = cwe_ids

    assert "/app/url_fetcher.py" in report, "url_fetcher.py not in report"
    assert "cwe-918" in report["/app/url_fetcher.py"], "'cwe-918' not found for url_fetcher.py"


def test_direct_localhost_blocked():
    """Test that direct access to localhost is blocked."""
    sys.path.insert(0, '/app')
    from url_fetcher import secure_url_fetcher, SSFRSecurityPolicy
    
    policy = SSFRSecurityPolicy()
    
    # Test that localhost is blocked before making network request
    try:
        secure_url_fetcher("http://localhost/admin", policy=policy)
        assert False, "Direct localhost access should be blocked"
    except PermissionError:
        pass  # Expected - localhost blocked


def test_direct_private_ip_blocked():
    """Test that direct access to private IPs is blocked."""
    sys.path.insert(0, '/app')
    from url_fetcher import secure_url_fetcher, SSFRSecurityPolicy
    
    policy = SSFRSecurityPolicy()
    
    # Test that cloud metadata IP is blocked
    try:
        secure_url_fetcher("http://169.254.169.254/latest/meta-data/", policy=policy)
        assert False, "Direct cloud metadata access should be blocked"
    except PermissionError:
        pass  # Expected - private IP blocked


def test_redirect_to_localhost_blocked():
    """Test that redirects to localhost are blocked - the core SSRF bypass fix."""
    sys.path.insert(0, '/app')
    from url_fetcher import secure_url_fetcher, SSFRSecurityPolicy
    
    policy = SSFRSecurityPolicy()
    
    # Mock a response that redirects to localhost
    with patch('url_fetcher.urlopen', create=True) as mock_urlopen, \
         patch('urllib.request.OpenerDirector.open') as mock_opener_open:
        mock_response = Mock()
        mock_response.url = "http://localhost/admin/secret"  # Final URL after redirect
        mock_response.headers.get_content_type = Mock(return_value='text/html')
        mock_response.read = Mock(return_value=b'secret data')
        
        mock_urlopen.return_value = mock_response
        mock_opener_open.return_value = mock_response
        
        initial_url = "http://example.com/image.png"
        
        try:
            result = secure_url_fetcher(initial_url, policy=policy)
            assert False, "Should have blocked redirect to localhost"
        except PermissionError:
            pass  # Expected


def test_redirect_to_private_ip_blocked():
    """Test that redirects to private IPs are blocked."""
    sys.path.insert(0, '/app')
    from url_fetcher import secure_url_fetcher, SSFRSecurityPolicy
    
    policy = SSFRSecurityPolicy()
    
    redirect_targets = [
        "http://10.0.0.1/internal",
        "http://169.254.169.254/latest/meta-data/",
    ]
    
    for target in redirect_targets:
        with patch('url_fetcher.urlopen', create=True) as mock_urlopen, \
             patch('urllib.request.OpenerDirector.open') as mock_opener_open:
            mock_response = Mock()
            mock_response.url = target
            mock_response.headers.get_content_type = Mock(return_value='text/html')
            mock_response.read = Mock(return_value=b'internal data')
            
            mock_urlopen.return_value = mock_response
            mock_opener_open.return_value = mock_response
            
            try:
                result = secure_url_fetcher("http://example.com/redirect", policy=policy)
                assert False, f"Should have blocked redirect to: {target}"
            except PermissionError:
                pass  # Expected


def test_redirect_to_allowed_host_works_and_blocked_hosts_blocked():
    """Test that the fix works correctly: blocked hosts blocked, allowed hosts allowed."""
    sys.path.insert(0, '/app')
    from url_fetcher import secure_url_fetcher, SSFRSecurityPolicy
    
    policy = SSFRSecurityPolicy()
    
    # First, verify that a redirect to a blocked host is still blocked
    with patch('url_fetcher.urlopen', create=True) as mock_urlopen, \
         patch('urllib.request.OpenerDirector.open') as mock_opener_open:
        mock_response = Mock()
        mock_response.url = "http://localhost/secret"
        mock_response.headers.get_content_type = Mock(return_value='text/html')
        mock_response.read = Mock(return_value=b'secret')
        mock_urlopen.return_value = mock_response
        mock_opener_open.return_value = mock_response
        
        try:
            secure_url_fetcher("http://example.com/test", policy=policy)
            assert False, "Should block redirect to localhost"
        except PermissionError:
            pass  # Expected
    
    # Second, verify that allowed external redirects work
    with patch('url_fetcher.urlopen', create=True) as mock_urlopen, \
         patch('urllib.request.OpenerDirector.open') as mock_opener_open:
        mock_response = Mock()
        mock_response.url = "http://another-example.com/final"
        mock_response.headers.get_content_type = Mock(return_value='text/html')
        mock_response.read = Mock(return_value=b'content')
        
        mock_urlopen.return_value = mock_response
        mock_opener_open.return_value = mock_response
        
        result = secure_url_fetcher("http://example.com/redirect", policy=policy)
        
        assert result['string'] == b'content'
        assert result['redirected_url'] == "http://another-example.com/final"


def test_multiple_redirect_chain_blocked():
    """Test that redirect chains leading to blocked hosts are detected."""
    sys.path.insert(0, '/app')
    from url_fetcher import secure_url_fetcher, SSFRSecurityPolicy
    
    policy = SSFRSecurityPolicy()
    
    with patch('url_fetcher.urlopen', create=True) as mock_urlopen, \
         patch('urllib.request.OpenerDirector.open') as mock_opener_open:
        mock_response = Mock()
        mock_response.url = "http://127.0.0.1:8080/admin"
        mock_response.headers.get_content_type = Mock(return_value='text/html')
        mock_response.read = Mock(return_value=b'admin data')
        
        mock_urlopen.return_value = mock_response
        mock_opener_open.return_value = mock_response
        
        try:
            result = secure_url_fetcher("http://attacker.com/malicious", policy=policy)
            assert False, "Should have blocked redirect chain to localhost"
        except PermissionError:
            pass  # Expected


def test_valid_external_requests_work_and_security_applied():
    """Test that valid external requests work and security is properly applied."""
    sys.path.insert(0, '/app')
    from url_fetcher import secure_url_fetcher, SSFRSecurityPolicy
    
    policy = SSFRSecurityPolicy()
    
    # First verify security is working by testing a blocked host
    try:
        secure_url_fetcher("http://127.0.0.1/test", policy=policy)
        assert False, "Security policy not being applied - localhost should be blocked"
    except PermissionError:
        pass  # Expected
    
    # Then verify valid external requests work
    with patch('url_fetcher.urlopen', create=True) as mock_urlopen, \
         patch('urllib.request.OpenerDirector.open') as mock_opener_open:
        mock_response = Mock()
        mock_response.url = "http://example.com/image.png"
        mock_response.headers.get_content_type = Mock(return_value='image/png')
        mock_response.read = Mock(return_value=b'image data')
        
        mock_urlopen.return_value = mock_response
        mock_opener_open.return_value = mock_response
        
        result = secure_url_fetcher("http://example.com/image.png", policy=policy)
        
        assert result['string'] == b'image data'
        assert result['mime_type'] == 'image/png'
