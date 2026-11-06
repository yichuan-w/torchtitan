import os
import smtplib
import time
import subprocess
from email.mime.text import MIMEText


def _clean_maildir(user):
    """Remove all mail from a user's Maildir."""
    maildir = f"/var/mail/vhosts/example.com/{user}"
    for d in ["new", "cur", "tmp"]:
        dirpath = os.path.join(maildir, d)
        if os.path.exists(dirpath):
            for f in os.listdir(dirpath):
                os.remove(os.path.join(dirpath, f))


def test_smtp_port25_delivery():
    """Test that mail can be delivered to virtual users via port 25."""
    _clean_maildir("bob")

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

    new_dir = "/var/mail/vhosts/example.com/bob/new"
    assert os.path.isdir(new_dir), (
        f"Maildir 'new' directory missing at {new_dir}. "
        f"Ensure Postfix delivers to Maildir under /var/mail/vhosts/."
    )
    mail_files = os.listdir(new_dir)
    assert len(mail_files) > 0, (
        f"No mail delivered to bob's Maildir via port 25. "
        f"Check virtual_mailbox_domains and virtual_mailbox_maps."
    )

    content = open(os.path.join(new_dir, mail_files[0])).read()
    assert subject in content, f"Subject not found in delivered mail"
    assert body in content, f"Body not found in delivered mail"
    print("TEST PASSED: SMTP port 25 delivery to virtual Maildir")


def test_imap_port993_tls():
    """Test IMAP access on port 993 with TLS."""
    # Clean slate and send a fresh test message
    _clean_maildir("bob")

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
        "a0 CAPABILITY\r\n"
        "a1 STARTTLS\r\n"
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


def test_both_users_maildirs():
    """Verify both users have working Maildir structures."""
    for user in ["alice", "bob"]:
        for subdir in ["new", "cur", "tmp"]:
            path = f"/var/mail/vhosts/example.com/{user}/{subdir}"
            assert os.path.isdir(path), (
                f"Missing Maildir directory: {path}. "
                f"Ensure Maildir structure is created for {user}@example.com."
            )
    print("TEST PASSED: Both users have complete Maildir structures")