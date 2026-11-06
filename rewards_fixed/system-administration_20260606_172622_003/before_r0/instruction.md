Set up TLS-encrypted syslog forwarding on this machine. All local syslog messages must be forwarded over an encrypted TLS connection to a receiver running on the same machine, which stores them in a structured directory. Additionally, a specific application log file must also be monitored and its contents forwarded over the same TLS channel.

Requirements:

1. Act as your own Certificate Authority. Generate a CA certificate and a host certificate (CN: localhost) using `certtool` from gnutls-bin. Place all certificate files in `/etc/rsyslog/certs/`.

2. Configure a TLS syslog receiver on port 6514. The receiver must present its certificate to connecting clients and clients must verify it.

3. Configure syslog message forwarding so that all locally-generated syslog messages are sent to the TLS receiver at localhost:6514.

4. Monitor the file `/var/log/app/access.log` and forward each new line as a syslog message with the tag `app-access` and facility `local1`, also over the TLS connection to the receiver.

5. The receiver must store incoming messages under `/var/log/remote/` organized as:
   `/var/log/remote/<hostname>/<year>/<month>/<day>/syslog.log`

6. Configure log rotation for the files under `/var/log/remote/`: rotate daily, retain 7 days, compress with gzip.

7. The system must survive an rsyslog restart and continue functioning correctly.

Note: Messages that arrive via the TLS receiver must not be re-forwarded (avoid message loops).
