Log rotation stopped working on this server. The application writes logs to `/var/log/myapp/` and logrotate should be rotating them, but the logs keep growing unchecked. We're concerned about disk space.

We had a disk-full incident a while back but that's been resolved - there's plenty of space now. However, rotation still isn't working and we need it fixed. We've noticed the logs are getting quite large (over 50MB each) but nothing seems to be rotating them.

The application has three log files:
- `/var/log/myapp/app.log` - main application log (should rotate when over 10MB)
- `/var/log/myapp/access.log` - HTTP access log (should rotate daily)
- `/var/log/myapp/error.log` - error log (should rotate weekly, keep 4 weeks)

Fix the log rotation so that:
1. `logrotate` can successfully rotate all logs without errors
2. A scheduled job runs logrotate regularly
3. All three logs are properly rotated according to their configured policies
