#!/bin/bash
# Tunnel Monitor Script
# Monitors SSH tunnel health and restarts if needed

TUNNEL_PORT=9090
TARGET_PORT=8080
LOG_FILE=/var/log/tunnel-monitor.log

check_tunnel() {
    if pgrep -f "ssh.*$TUNNEL_PORT.*$TARGET_PORT" > /dev/null; then
        echo "$(date): Tunnel is running" >> $LOG_FILE
        return 0
    else
        echo "$(date): Tunnel not running, attempting restart" >> $LOG_FILE
        return 1
    fi
}

restart_tunnel() {
    # Kill any existing tunnel processes
    pkill -f "ssh.*$TUNNEL_PORT" 2>/dev/null || true
    
    # Start new tunnel
    /app/start-tunnel.sh >> $LOG_FILE 2>&1 &
    sleep 2
    
    if check_tunnel; then
        echo "$(date): Tunnel restarted successfully" >> $LOG_FILE
    else
        echo "$(date): Failed to restart tunnel" >> $LOG_FILE
    fi
}

# Main loop
while true; do
    if ! check_tunnel; then
        restart_tunnel
    fi
    sleep 30
done
