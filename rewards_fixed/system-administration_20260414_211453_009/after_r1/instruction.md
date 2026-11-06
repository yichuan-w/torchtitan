An application stack managed by supervisord is failing to start properly. The configuration is at `/opt/myapp/config/supervisord.conf`. All four services (redis, worker, api, nginx) should be running and healthy.

Start supervisord with the existing configuration. Use `supervisorctl` to diagnose which services are failing and why, then fix the issues so all services start correctly.

After fixes are applied, the health endpoint at http://localhost:8080/health should return `{"status": "healthy"}`, and the Redis-backed endpoint at http://localhost:8080/api/jobs should return a `pending_jobs` count.
