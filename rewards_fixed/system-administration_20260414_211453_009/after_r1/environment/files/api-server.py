#!/usr/bin/env python3
"""API server for myapp"""
import os
import sys
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

# Add library path
sys.path.insert(0, '/opt/myapp/lib')

import redis

redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
port = int(os.environ.get('PORT', '8000'))

class APIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'healthy'}).encode())
        elif self.path == '/jobs':
            try:
                client = redis.from_url(redis_url)
                count = client.llen('job_queue')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'pending_jobs': count}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"[API] {args[0]}")

if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', port), APIHandler)
    print(f"API server started on port {port}")
    server.serve_forever()
