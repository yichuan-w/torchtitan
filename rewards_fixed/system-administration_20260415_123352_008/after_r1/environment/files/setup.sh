#!/bin/bash
# Setup script for the environment
# This creates the initial (broken) state

set -e

# Create required directories
mkdir -p /var/backups/app
mkdir -p /app/data
mkdir -p /var/cache/app/tmp
mkdir -p /var/log/app
mkdir -p /var/run/app

# Create some sample data for backup
echo "Application data v1.0" > /app/data/config.txt
echo "User database" > /app/data/users.txt

# Create some temp files and logs (make them old so they're candidates for cleanup)
touch -d "5 days ago" /var/cache/app/tmp/old_session1.tmp
touch -d "5 days ago" /var/cache/app/tmp/old_session2.tmp
touch /var/cache/app/tmp/recent_session.tmp
touch -d "2 days ago" /var/log/app/old_access.log
touch /var/log/app/access.log
touch /var/log/app/error.log

# Create log files for scripts
touch /var/log/backup.log
touch /var/log/cleanup.log
touch /var/log/healthcheck.log
touch /var/log/cron.log

# Copy scripts to system location
cp /app/backup.sh /usr/local/bin/backup.sh
cp /app/cleanup.sh /usr/local/bin/cleanup.sh
cp /app/healthcheck.sh /usr/local/bin/healthcheck.sh

# Set permissions
chmod +x /usr/local/bin/backup.sh
chmod +x /usr/local/bin/cleanup.sh
chmod +x /usr/local/bin/healthcheck.sh

# Create appuser for the cleanup script permission issue
useradd -r -s /bin/bash appuser 2>/dev/null || true

# Set wrong ownership for cleanup target directories
chown root:root /var/cache/app/tmp
chmod 750 /var/cache/app/tmp
chown root:root /var/log/app
chmod 750 /var/log/app

# Create crontab for root with the jobs
cat > /etc/cron.d/app-jobs << 'CRONTAB'
# Application maintenance cron jobs
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin

# Run backup every 5 minutes (for testing; normally would be daily)
*/5 * * * * root /usr/local/bin/backup.sh >> /var/log/cron.log 2>&1

# Run cleanup every 10 minutes (as appuser, but directories owned by root)
*/10 * * * * appuser /usr/local/bin/cleanup.sh >> /var/log/cron.log 2>&1

# Run health check every minute
* * * * * root /usr/local/bin/healthcheck.sh >> /var/log/cron.log 2>&1
CRONTAB

chmod 644 /etc/cron.d/app-jobs

echo "Setup complete. Cron jobs are configured but may have issues."
