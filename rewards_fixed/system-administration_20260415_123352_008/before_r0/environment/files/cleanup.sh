#!/bin/bash
# Cleanup script for temporary files
# Removes old temp files and rotates logs

TEMP_DIR="/var/cache/app/tmp"
LOG_DIR="/var/log/app"
MAX_AGE_DAYS=3

# Clean temp files older than MAX_AGE_DAYS
if [ -d "$TEMP_DIR" ]; then
    find "$TEMP_DIR" -type f -mtime +$MAX_AGE_DAYS -delete
    echo "Cleaned temp files older than $MAX_AGE_DAYS days" >> /var/log/cleanup.log
fi

# Compress old log files
if [ -d "$LOG_DIR" ]; then
    find "$LOG_DIR" -name "*.log" -mtime +1 -exec gzip {} \; 2>/dev/null
    echo "Compressed old logs" >> /var/log/cleanup.log
fi
