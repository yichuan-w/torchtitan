#!/bin/bash
# Health check script for the application
# Checks if services are running and reports status

APP_NAME="myapp"
STATUS_DIR="/var/run/ap"
STATUS_FILE="$STATUS_DIR/health.status"

# Create status directory if it doesn't exist
mkdir -p "$STATUS_DIR"

# Check if the application process is running
if pgrep -f "$APP_NAME" > /dev/null; then
    STATUS="healthy"
    EXIT_CODE=0
else
    STATUS="unhealthy"
    EXIT_CODE=1
fi

# Write status with timestamp
echo "$(date '+%Y-%m-%d %H:%M:%S') - $APP_NAME: $STATUS" > "$STATUS_FILE"

# Append to health log
echo "$(date '+%Y-%m-%d %H:%M:%S') - Health check: $STATUS" >> /var/log/healthcheck.log

exit $EXIT_CODE
