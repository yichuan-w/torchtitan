#!/bin/bash
# Entrypoint script - sets up the environment

set -e

if [ "${1:-}" = "/app/entrypoint.sh" ]; then
    set -- tail -f /dev/null
fi

# Create privilege separation directory for sshd
mkdir -p /run/sshd

# Generate SSH host keys if they don't exist
if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
    ssh-keygen -t rsa -f /etc/ssh/ssh_host_rsa_key -N '' -q
fi

# Create tunnel user if it doesn't exist
if ! id -u tunnel &>/dev/null; then
    useradd -m -s /bin/bash tunnel
fi

# Ensure .ssh directory exists for tunnel user
mkdir -p /home/tunnel/.ssh
chmod 700 /home/tunnel/.ssh
touch /home/tunnel/.ssh/authorized_keys
chmod 600 /home/tunnel/.ssh/authorized_keys
chown -R tunnel:tunnel /home/tunnel/.ssh

# Start SSH daemon
pgrep -x sshd >/dev/null || /usr/sbin/sshd

# Start the internal services in the background
cd /app
pgrep -f '/app/dashboard.py' >/dev/null || nohup python3 /app/dashboard.py > /var/log/dashboard.log 2>&1 &
pgrep -f '/app/api_server.py' >/dev/null || nohup python3 /app/api_server.py > /var/log/api_server.log 2>&1 &
pgrep -f '/app/metrics_server.py' >/dev/null || nohup python3 /app/metrics_server.py > /var/log/metrics_server.log 2>&1 &

exec "$@"
