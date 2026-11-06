The SSH reverse tunnel configuration for exposing internal services is broken. Three tunnels need to be established:
- Dashboard tunnel: `127.0.0.1:9090` → `127.0.0.1:8080`
- API tunnel: `127.0.0.1:9091` → `127.0.0.1:3000`
- Metrics tunnel: `127.0.0.1:9092` → `127.0.0.1:5000`

The `start-tunnel.sh` script at `/app/start-tunnel.sh` is configured for the dashboard tunnel but doesn't work. Fix the configuration issues and establish all three tunnels so that:
- Dashboard is accessible at `http://127.0.0.1:9090`
- API server is accessible at `http://127.0.0.1:9091/health`
- Metrics server is accessible at `http://127.0.0.1:9092/metrics`

All tunnels should use key-based SSH authentication to the `tunnel` user.
The SSH server restricts remote forwards to these three loopback listeners; preserve that restriction while repairing the tunnels.

The internal services (dashboard on port 8080, API on port 3000, metrics on port 5000) and SSH server should already be running.
