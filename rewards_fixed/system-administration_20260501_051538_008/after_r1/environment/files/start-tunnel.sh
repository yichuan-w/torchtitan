#!/bin/bash
# Start SSH tunnel for internal dashboard
# Usage: ./start-tunnel.sh

# This script creates a reverse SSH tunnel that exposes the dashboard on port 9090
# The tunnel forwards connections from 127.0.0.1:9090 to 127.0.0.1:8080

# Need to add strict host key checking to avoid prompts
ssh -N -R 9090:127.0.0.1:8080 tunnel@localhost
