# Internal Metrics Server
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import random

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/metrics':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            # Prometheus-style metrics
            metrics = f"""# HELP cpu_usage Current CPU usage percentage
# TYPE cpu_usage gauge
cpu_usage {random.uniform(10, 80):.2f}

# HELP memory_usage Current memory usage in MB
# TYPE memory_usage gauge
memory_usage {random.uniform(100, 500):.2f}

# HELP request_count Total request count
# TYPE request_count counter
request_count {random.randint(1000, 10000)}

# HELP service_info Service information
# TYPE service_info gauge
service_info{{name="metrics-server",version="1.0.0"}} 1
"""
            self.wfile.write(metrics.encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', 5000), MetricsHandler)
    print("Metrics server running on http://127.0.0.1:5000")
    server.serve_forever()
