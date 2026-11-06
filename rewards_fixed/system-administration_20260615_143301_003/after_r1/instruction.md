Set up a mail server using Postfix and Dovecot that handles email for the virtual domain `example.com`.

**Requirements:**

- Postfix must accept mail on port 25 for `example.com` addresses and deliver to Maildir storage under `/var/mail/vhosts/`
- Dovecot must serve IMAP on port 993 with TLS and on port 143 with STARTTLS
- All users are virtual (no system accounts). Store credentials in `/etc/dovecot/users` using the Dovecot passwd-file format with PLAIN scheme and UID/GID 1000
- Create two users: alice@example.com (password: alicepass) and bob@example.com (password: bobpass)
- Generate a self-signed TLS certificate and configure both Postfix and Dovecot to use it