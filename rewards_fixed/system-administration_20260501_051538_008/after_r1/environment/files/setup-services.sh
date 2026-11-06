#!/bin/bash
# Setup script for internal services
# This runs once at container startup

set -e

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
/usr/sbin/sshd

# Start the internal dashboard service in the background
cd /app
nohup python3 /app/dashboard.py > /var/log/dashboard.log 2>&1 &

echo "Services started: sshd, dashboard"
