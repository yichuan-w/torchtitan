We need a centralized logging setup on this server for audit compliance.

Here's what's required:

1. **TLS-encrypted syslog receiver**: Configure rsyslog to accept syslog messages over TLS on TCP port 6514. Received messages should be written to `/var/log/remote.log`.

2. **Local log forwarding**: Configure rsyslog to forward all local syslog messages to the TLS receiver on `localhost:6514`. The forwarded messages should use TLS encryption. Make sure forwarding doesn't create an infinite loop.

3. **Log analyzer**: Create a Python script at `/app/analyze.py` that reads `/var/log/remote.log` and generates an HTML report at `/app/report.html`. The report should show:
   - Total number of log messages received
   - Count of messages per facility (kern, user, daemon, syslog, auth, local0 through local7, and any others found)
   - Count of messages per severity (emerg, alert, crit, err, warning, notice, info, debug)
   - Timestamp of when the report was generated

   The facility and severity information must be extracted from the syslog messages themselves — you'll need to configure rsyslog to preserve this metadata in the received log file so the analyzer can parse it.

4. **Web access**: Configure nginx to serve the report at `http://localhost:8088/report.html`.

A self-signed CA, server certificate, and server key are already prepared in `/etc/rsyslog/certs/`; inspect and use them for the TLS setup. The server certificate's CN is `localhost`. If rsyslog reports that this required certificate location is inaccessible, copies are also staged in its configuration tree.

After setup, the system should be in a state where:
- rsyslog is listening for TLS connections on port 6514
- Local syslog messages flow through TLS to the receiver and land in `/var/log/remote.log`
- Running `python3 /app/analyze.py` generates `/app/report.html`
- `curl http://localhost:8088/report.html` returns the report
