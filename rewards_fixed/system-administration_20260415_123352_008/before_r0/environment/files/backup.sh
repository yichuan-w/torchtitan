#!/bin/sh
# Daily backup script for application data
# Created by sysadmin

BACKUP_DIR="/var/backups/app"
SOURCE_DIR="/app/data"
DATE=$(date +%Y%m%d)

# Check if backup directory exists
if [[ ! -d "$BACKUP_DIR" ]]; then
    echo "ERROR: Backup directory does not exist"
    exit 1
fi

# Array for backup file names (bash-specific - this will fail in /bin/sh)
BACKUP_NAMES=("daily" "weekly" "monthly")
BACKUP_TYPE=${BACKUP_NAMES[0]}

# Create backup with timestamp
BACKUP_FILE="${BACKUP_DIR}/backup-${BACKUP_TYPE}-${DATE}.tar.gz"

# Use tar to create compressed backup
tar -czf "$BACKUP_FILE" -C "$SOURCE_DIR" . 2>/dev/null

# Verify backup was created
if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "ERROR: Failed to create backup file"
    exit 1
fi

# Keep only last 7 days of backups
find "$BACKUP_DIR" -name "backup-*.tar.gz" -mtime +7 -delete

# Log success
echo "$(date): Backup completed: $BACKUP_FILE" >> /var/log/backup.log
