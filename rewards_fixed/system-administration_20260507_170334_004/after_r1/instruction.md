I've set up supervisord to manage a multi-service application stack with nginx as a reverse proxy and three backend Python services. The whole thing is broken - services keep crashing, nginx can't reach backends, and logs are filling up the disk.

The setup should work like this:
- supervisord manages all processes
- nginx on port 80 load balances to three backend services (ports 8001, 8002, 8003)
- Each backend service returns health status on `/health`
- Logs should go to `/var/log/app/` with rotation configured
- An event listener should send alerts when processes fail

Current state:
- supervisord is running but most services show FATAL or BACKOFF status
- nginx is giving 502 Bad Gateway errors
- Some services can't write to their log files
- Log files are not rotating properly
- The event listener isn't capturing process failures

The supervisord config is at `/etc/supervisor/supervisord.conf` with program configs in `/etc/supervisor/conf.d/`. The nginx config is at `/etc/nginx/nginx.conf`.

I need:
1. All backend services (backend1, backend2, backend3) running and healthy in supervisord
2. nginx successfully proxying requests to backends (curl localhost should work)
3. Log rotation working for all services (logs should rotate when they hit size limit)
4. No services in FATAL or BACKOFF state
5. Event listener working and logging process state changes

When validating the proxy, make sure repeated requests are actually served by multiple backends; if they are not, investigate nginx's runtime behavior rather than relying only on the upstream list.

Check what's failing with `supervisorctl status` and the logs in `/var/log/supervisor/` and `/var/log/app/`.
