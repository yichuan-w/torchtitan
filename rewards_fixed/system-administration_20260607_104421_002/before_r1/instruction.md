I have a web server access log at /app/logs/access.log in combined log format. I need a script at /app/bin/logreport that analyzes this log and generates a report.

The script should:
1. Count total requests
2. Count requests by HTTP method (GET, POST, etc.)
3. Find the top 5 IP addresses by request count
4. Count requests by hour of day (0-23)
5. Count requests by HTTP status code class (2xx, 3xx, 4xx, 5xx)

The script should output to stdout in this exact format (fields separated by pipes):

```
total_requests|12345
method|GET|5000
method|POST|3000
top_ip|192.168.1.1|100
top_ip|10.0.0.1|50
hour|0|200
hour|1|150
status_class|2xx|8000
status_class|3xx|1000
status_class|4xx|2000
status_class|5xx|1345
```

Lines should be sorted: total_requests first, then methods alphabetically, top_ips by count descending, hours numerically, status_class by code.

Make the script executable. It should work on the log file at /app/logs/access.log.
