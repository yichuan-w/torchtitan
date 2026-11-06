#!/bin/bash
# Setup script to create the broken logrotate state
# This simulates a scenario where logrotate has multiple issues

set -e

# Create the main application log with content (20MB - should be rotated)
dd if=/dev/urandom of=/var/log/myapp/app.log bs=1M count=20 2>/dev/null || true

# Create the access log (15MB - should be rotated)
dd if=/dev/urandom of=/var/log/myapp/access.log bs=1M count=15 2>/dev/null || true

# Create the error log (10MB - should be rotated)
dd if=/dev/urandom of=/var/log/myapp/error.log bs=1M count=10 2>/dev/null || true

# Create leftover rotated files from a partially-failed previous rotation
# app.log.1 exists but is only partially filled (5MB instead of full)
dd if=/dev/urandom of=/var/log/myapp/app.log.1 bs=1M count=5 2>/dev/null || true

# An empty .gz file from a failed compression attempt
touch /var/log/myapp/app.log.2.gz

# Create a corrupted status file - wrong format that logrotate can't parse
# This will cause "bad top line" error
mkdir -p /var/lib/logrotate
echo '"/var/log/myapp/app.log" 2024-1-15-0:0:0' > /var/lib/logrotate/status

# Remove any existing cron job for logrotate (it comes with the package)
rm -f /etc/cron.daily/logrotate 2>/dev/null || true

# Remove the systemd timer symlink (logrotate.timer comes enabled by default)
rm -f /etc/systemd/system/timers.target.wants/logrotate.timer 2>/dev/null || true

echo "Setup complete: logrotate has broken state files and no scheduling"
