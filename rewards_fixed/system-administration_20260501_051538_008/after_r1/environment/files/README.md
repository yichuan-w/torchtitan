# Internal Services Server

This server hosts internal services that need to be exposed via SSH reverse tunnels.

## Services Running

- **Internal Dashboard**: A web service running on `http://127.0.0.1:8080` that provides system metrics and status information.
- **API Server**: A REST API running on `http://127.0.0.1:3000` providing data endpoints.
- **Metrics Server**: A Prometheus-style metrics endpoint running on `http://127.0.0.1:5000/metrics`.
- **SSH Server**: OpenSSH server for secure remote access.

## Tunnel Configuration

Three reverse tunnels need to be established:
1. Dashboard tunnel: Forward port 9090 to dashboard on 127.0.0.1:8080
2. API tunnel: Forward port 9091 to API server on 127.0.0.1:3000
3. Metrics tunnel: Forward port 9092 to metrics server on 127.0.0.1:5000

The `start-tunnel.sh` script is configured to create the dashboard tunnel. Additional tunnels will need to be set up.

## Current Issue

The tunnel configuration was recently set up but nothing is working. Multiple issues have been identified:
- The tunnel scripts fail to connect
- SSH configuration may be blocking the tunnels
- Key-based authentication is not properly configured

## Useful Commands

- Check if SSH is running: `ps aux | grep sshd`
- Verify services: `curl http://127.0.0.1:8080`, `curl http://127.0.0.1:3000/health`, `curl http://127.0.0.1:5000/metrics`
- Test SSH connection: `ssh tunnel@localhost`
- Check SSH config: `cat /etc/ssh/sshd_config | grep -E "AllowTcp|Gateway"`

## Expected Outcome

All three tunnels should be established and all services accessible:
- Dashboard at `http://127.0.0.1:9090`
- API server at `http://127.0.0.1:9091/health`
- Metrics at `http://127.0.0.1:9092/metrics`
