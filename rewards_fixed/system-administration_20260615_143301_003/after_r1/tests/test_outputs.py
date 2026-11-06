import os
import pwd
import socket
import smtplib
import ssl
import time
import subprocess
import tempfile
from email.mime.text import MIMEText
from pathlib import Path


MAIL_ROOT = Path("/var/mail/vhosts")


def _clean_maildirs():
    """Remove messages without assuming a particular Maildir nesting."""
    for directory in MAIL_ROOT.rglob("*"):
        if directory.is_dir() and directory.name in {"new", "cur"}:
            for message in directory.iterdir():
                if message.is_file():
                    message.unlink()


def _delivered_messages():
    return [
        message
        for directory in MAIL_ROOT.rglob("new")
        if directory.is_dir()
        for message in directory.iterdir()
        if message.is_file()
    ]


def test_smtp_port25_delivery():
    """Test that mail can be delivered to virtual users via port 25."""
    _clean_maildirs()

    subject = "SMTP Delivery Test"
    body = "This message was delivered via SMTP port 25."

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = "sender@example.org"
    msg["To"] = "bob@example.com"

    try:
        server = smtplib.SMTP("localhost", 25, timeout=15)
        server.sendmail("sender@example.org", ["bob@example.com"], msg.as_string())
        server.quit()
        print("SMTP 25: Email accepted for delivery")
    except Exception as e:
        raise AssertionError(
            f"Failed to deliver via port 25: {type(e).__name__}: {e}. "
            f"Ensure Postfix accepts mail on port 25 for example.com domain."
        ) from e

    time.sleep(1)

    mail_files = _delivered_messages()
    assert mail_files, (
        f"No mail delivered to bob's Maildir via port 25. "
        f"Check virtual_mailbox_domains and virtual_mailbox_maps."
    )

    content = "\n".join(message.read_text(errors="replace") for message in mail_files)
    assert subject in content, f"Subject not found in delivered mail"
    assert body in content, f"Body not found in delivered mail"
    print("TEST PASSED: SMTP port 25 delivery to virtual Maildir")


def test_imap_port993_tls():
    """Test IMAP access on port 993 with TLS."""
    # Clean slate and send a fresh test message
    _clean_maildirs()

    msg = MIMEText("IMAP test message content.")
    msg["Subject"] = "IMAP Test"
    msg["From"] = "test@example.org"
    msg["To"] = "bob@example.com"

    try:
        server = smtplib.SMTP("localhost", 25, timeout=15)
        server.sendmail("test@example.org", ["bob@example.com"], msg.as_string())
        server.quit()
    except Exception as e:
        raise AssertionError(f"Pre-test SMTP delivery failed: {e}") from e

    time.sleep(1)

    imap_commands = "a1 LOGIN bob@example.com bobpass\r\na2 SELECT INBOX\r\na3 FETCH 1 BODY[]\r\na4 LOGOUT\r\n"

    result = subprocess.run(
        ["openssl", "s_client", "-connect", "localhost:993", "-quiet", "-verify_quiet"],
        input=imap_commands, capture_output=True, text=True, timeout=15,
    )

    output = result.stdout + result.stderr
    print("IMAP 993:", output[-600:] if len(output) > 600 else output)

    assert "a1 OK" in output, (
        f"IMAP login on port 993 failed. Check Dovecot TLS, auth, and /etc/dovecot/users."
    )
    assert "a2 OK" in output, (
        f"IMAP SELECT on port 993 failed. Check Dovecot mail_location."
    )
    assert "IMAP test message content" in output, (
        f"Expected email content not found in IMAP FETCH on port 993."
    )
    print("TEST PASSED: IMAP port 993 with TLS")


def test_imap_port143_starttls():
    """Test IMAP access on port 143 with STARTTLS."""
    imap_commands = (
        "a2 LOGIN alice@example.com alicepass\r\n"
        "a3 SELECT INBOX\r\n"
        "a4 LOGOUT\r\n"
    )

    result = subprocess.run(
        ["openssl", "s_client", "-connect", "localhost:143", "-starttls", "imap",
         "-quiet", "-verify_quiet"],
        input=imap_commands, capture_output=True, text=True, timeout=15,
    )

    output = result.stdout + result.stderr
    print("IMAP 143:", output[-600:] if len(output) > 600 else output)

    assert "a2 OK" in output, (
        f"IMAP STARTTLS login on port 143 failed. Check Dovecot STARTTLS configuration."
    )
    assert "a3 OK" in output, (
        f"IMAP SELECT on port 143 failed. Check Dovecot mail_location."
    )
    print("TEST PASSED: IMAP port 143 with STARTTLS")


def test_virtual_user_file_and_ids():
    """Verify the required passwd-file records and virtual identities."""
    users_path = Path("/etc/dovecot/users")
    assert users_path.is_file(), "Required passwd-file /etc/dovecot/users is missing"

    records = {}
    for line in users_path.read_text().splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        fields = line.split(":")
        if len(fields) >= 4:
            records[fields[0]] = fields

    expected = {"alice@example.com": "alicepass", "bob@example.com": "bobpass"}
    config = subprocess.run(
        ["doveconf", "-n"], capture_output=True, text=True, timeout=10, check=True
    ).stdout.lower()
    credentials_valid = all(
        address in records
        and records[address][1] in {password, "{PLAIN}" + password}
        and records[address][2:4] == ["1000", "1000"]
        and (records[address][1].startswith("{PLAIN}") or "scheme=plain" in config)
        for address, password in expected.items()
    )
    assert credentials_valid, (
        "/etc/dovecot/users must contain both required PLAIN credentials with UID/GID 1000"
    )

    system_names = {entry.pw_name for entry in pwd.getpwall()}
    assert not system_names.intersection({"alice", "bob", *expected}), (
        "alice and bob must be virtual users, not system accounts"
    )


def _live_certificates():
    context = ssl._create_unverified_context()
    with smtplib.SMTP("localhost", 25, timeout=10) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp_certificate = smtp.sock.getpeercert(binary_form=True)

    with socket.create_connection(("localhost", 993), timeout=10) as connection:
        with context.wrap_socket(connection, server_hostname="localhost") as tls_socket:
            imap_certificate = tls_socket.getpeercert(binary_form=True)
    return smtp_certificate, imap_certificate


def test_shared_self_signed_tls_certificate():
    """Verify both live services use the generated self-signed certificate."""
    smtp_certificate, imap_certificate = _live_certificates()
    assert smtp_certificate == imap_certificate, (
        "Postfix and Dovecot are not using the same TLS certificate"
    )

    with tempfile.NamedTemporaryFile() as certificate_file:
        certificate_file.write(smtp_certificate)
        certificate_file.flush()
        details = subprocess.run(
            ["openssl", "x509", "-inform", "DER", "-in", certificate_file.name,
             "-noout", "-subject", "-issuer"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.splitlines()
    subject = details[0].split("=", 1)[1].strip()
    issuer = details[1].split("=", 1)[1].strip()
    assert subject == issuer, "The TLS certificate used by the services is not self-signed"
