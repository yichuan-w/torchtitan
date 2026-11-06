import re
from pathlib import Path


def test_incident_report_exists():
    """Verify the incident report file was created."""
    report_path = Path("/app/incident_report.txt")
    assert report_path.exists(), "Incident report file not found at /app/incident_report.txt"


def test_attack_vector_identified():
    """Verify the attack vector was correctly identified."""
    report_path = Path("/app/incident_report.txt")
    content = report_path.read_text().lower()
    
    # Multiple valid attack vectors exist:
    # 1. SSH brute force against deploy user
    # 2. File upload vulnerability leading to webshell upload
    
    ssh_indicators = any(term in content for term in ['ssh', 'brute force', 'bruteforce', 'password'])
    webshell_indicators = any(term in content for term in ['webshell', 'file upload', 'upload vulnerability', 'update.php', 'malicious php'])
    credential_indicators = any(term in content for term in ['deploy', 'credential', 'compromised'])
    
    has_valid_vector = ssh_indicators or webshell_indicators or credential_indicators
    
    assert has_valid_vector, \
        "Attack vector not correctly identified. Should mention SSH brute force, compromised credentials, or file upload/webshell"


def test_persistence_mechanisms_found():
    """Verify multiple persistence mechanisms were identified."""
    report_path = Path("/app/incident_report.txt")
    content = report_path.read_text().lower()
    
    # Count identified persistence mechanisms
    mechanisms_found = []
    
    # 1. Malicious cron job (system-health or health_check)
    if any(term in content for term in ['cron', 'scheduled task', 'health_check', 'system-health']):
        mechanisms_found.append('cron')
    
    # 2. Hidden script in /tmp/.hidden
    if any(term in content for term in ['hidden', '/tmp', '.sync', 'hidden script']):
        mechanisms_found.append('hidden_script')
    
    # 3. SSH authorized_keys
    if any(term in content for term in ['ssh key', 'authorized_keys', 'ssh backdoor', 'root ssh']):
        mechanisms_found.append('ssh_key')
    
    assert len(mechanisms_found) >= 2, \
        f"Expected at least 2 persistence mechanisms, found {len(mechanisms_found)}: {mechanisms_found}"


def test_timestamp_identified():
    """Verify the initial compromise timestamp was identified."""
    report_path = Path("/app/incident_report.txt")
    content = report_path.read_text()
    
    # Look for the timestamp format and the correct date
    # Valid timestamps:
    # - 2024-11-15 03:22:20 (SSH login success - initial access)
    # - 2024-11-14 14:30 (webshell upload)
    # - Nov 15, 2024 or 2024-11-15 with time around 03:22
    
    # Check for date presence (Nov 14 or Nov 15 are valid)
    date_pattern = r'2024-11-1[45]|Nov.*1[45].*2024|November.*1[45].*2024'
    assert re.search(date_pattern, content), \
        "Timestamp not found - should indicate November 14 or 15, 2024"
    
    # Check for reasonable time
    # Webshell upload: 14:30 on Nov 14
    # SSH access: 03:22 on Nov 15
    time_pattern = r'03:2[0-9]|14:30|3:2[0-9]|03:[0-9]{2}|14:[0-9]{2}'
    assert re.search(time_pattern, content), \
        "Time not correctly identified - should be around 03:22 (SSH access) or 14:30 (webshell upload)"


def test_exfiltration_target_recovered():
    """Verify the exfiltration target password was recovered."""
    report_path = Path("/app/incident_report.txt")
    content = report_path.read_text()
    
    # The database password staged for exfiltration
    password_found = 'Xk9#mP2$vL5@nQ8&hR4*wT7!' in content

    assert password_found, \
        "Exfiltration target not found. Should contain the recovered password from staged credentials"


def test_report_format():
    """Verify the report follows the required format."""
    report_path = Path("/app/incident_report.txt")
    content = report_path.read_text()
    
    # Check for required fields
    assert 'ATTACK_VECTOR:' in content, "Missing ATTACK_VECTOR field"
    assert 'PERSISTENCE:' in content, "Missing PERSISTENCE field"
    assert 'TIMESTAMP:' in content, "Missing TIMESTAMP field"
    assert 'EXFIL_TARGET:' in content, "Missing EXFIL_TARGET field"
