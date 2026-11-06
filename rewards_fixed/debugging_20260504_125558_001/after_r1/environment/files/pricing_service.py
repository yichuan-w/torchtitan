#!/usr/bin/env python3
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class PricingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/rates":
            self.send_error(404)
            return
        time.sleep(2.5)
        body = json.dumps({"tax_multiplier": 1.17, "currency_rate": 0.93}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


ThreadingHTTPServer(("127.0.0.1", 8001), PricingHandler).serve_forever()
