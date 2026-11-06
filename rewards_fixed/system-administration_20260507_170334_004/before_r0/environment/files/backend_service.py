#!/usr/bin/env python3
"""
Simple HTTP backend service.
Responds with health status and request info.
"""

import http.server
import socketserver
import sys
import json
import os
import logging

# Get port from environment or command line
PORT = int(os.environ.get('BACKEND_PORT', sys.argv[1] if len(sys.argv) > 1 else 8001))
SERVICE_NAME = os.environ.get('SERVICE_NAME', f'backend-{PORT}')

# Set up logging
log_dir = '/var/log/app'
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f'{SERVICE_NAME}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(SERVICE_NAME)


class BackendHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info("%s - %s", self.address_string(), format % args)
    
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {
                'status': 'healthy',
                'service': SERVICE_NAME,
                'port': PORT
            }
            self.wfile.write(json.dumps(response).encode())
            logger.info("Health check from %s", self.client_address[0])
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {
                'service': SERVICE_NAME,
                'port': PORT,
                'path': self.path,
                'method': 'GET'
            }
            self.wfile.write(json.dumps(response).encode())
            logger.info("Request %s from %s", self.path, self.client_address[0])


if __name__ == '__main__':
    logger.info("Starting %s on port %d", SERVICE_NAME, PORT)
    with socketserver.TCPServer(('', PORT), BackendHandler) as httpd:
        httpd.serve_forever()
