A production server has three cron jobs that should be running maintenance tasks: backups, cleanup, and health checks. The system administrator who set them up left, and now we're noticing the jobs aren't working properly.

The cron jobs are configured in `/etc/cron.d/app-jobs` and should be:
- **backup.sh**: Creates daily backups of `/app/data` to `/var/backups/app`
- **cleanup.sh**: Removes old temp files and compresses logs
- **healthcheck.sh**: Monitors application health and writes status

Each job logs to `/var/log/cron.log`. Diagnose why each job is failing and fix them so they all run successfully. The fixes should be persistent - don't just run the scripts manually, fix the underlying configuration issues.

After fixing, verify each job works by running it manually and checking the results.
