import os


def test_cracked_passwords_file_exists():
    """The cracked_passwords.txt file must exist."""
    assert os.path.exists("/app/cracked_passwords.txt"), (
        "cracked_passwords.txt not found"
    )


def test_admin_password_cracked():
    """admin password must be cracked."""
    with open("/app/cracked_passwords.txt", "r") as f:
        content = f.read()
    assert "admin:sunshine" in content, f"Missing admin:sunshine. Content:\n{content}"


def test_root_password_cracked():
    """root password must be cracked."""
    with open("/app/cracked_passwords.txt", "r") as f:
        content = f.read()
    assert "root:letmein" in content, f"Missing root:letmein. Content:\n{content}"


def test_dbadmin_password_cracked():
    """dbadmin password must be cracked (leet-speak of 'password')."""
    with open("/app/cracked_passwords.txt", "r") as f:
        content = f.read()
    assert "dbadmin:p@55w0rd" in content, (
        f"Missing dbadmin:p@55w0rd. Content:\n{content}"
    )
