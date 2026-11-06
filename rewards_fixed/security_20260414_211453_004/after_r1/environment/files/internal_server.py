"""
Internal metadata server - simulates an internal service that should not be 
accessible from external requests.

This server runs on localhost and provides sensitive metadata that should 
be protected from SSRF attacks.
"""

from flask import Flask, jsonify

app = Flask(__name__)


@app.route('/')
def index():
    return jsonify({
        "service": "Internal Metadata Server",
        "version": "1.0.0",
        "status": "running"
    })


@app.route('/metadata')
def metadata():
    """Sensitive metadata endpoint."""
    return jsonify({
        "service": "internal-metadata",
        "secrets": {
            "api_key": "INTERNAL_SECRET_KEY_12345",
            "database_password": "db_password_secret",
            "admin_token": "admin_token_confidential"
        },
        "internal_endpoints": [
            "http://localhost:8081/admin",
            "http://localhost:8081/config",
            "http://169.254.169.254/latest/meta-data/"
        ]
    })


@app.route('/admin')
def admin():
    """Admin panel - should not be externally accessible."""
    return jsonify({
        "admin_panel": True,
        "users": ["admin", "root", "system"],
        "permissions": "full_access"
    })


@app.route('/config')
def config():
    """Configuration endpoint."""
    return jsonify({
        "debug_mode": True,
        "database_host": "localhost:5432",
        "redis_host": "localhost:6379",
        "secret_key": "SUPER_SECRET_KEY_DO_NOT_SHARE"
    })


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8081, debug=False)
