#!/usr/bin/env python3
"""Flask application with Redis caching and static file serving."""

from flask import Flask, jsonify, send_from_directory
import redis
import os
import socket

app = Flask(__name__)

# Redis configuration via environment variables
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', None)

try:
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=2
    )
except Exception as e:
    redis_client = None
    print(f"Warning: Could not create Redis client: {e}")

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')


@app.route('/')
def index():
    return jsonify({
        "service": "flask-redis-app",
        "version": "1.0.0",
        "hostname": socket.gethostname()
    })


@app.route('/health')
def health():
    redis_status = "disconnected"
    redis_error = None
    if redis_client:
        try:
            redis_client.ping()
            redis_status = "connected"
        except Exception as e:
            redis_error = str(e)

    result = {
        "status": "ok",
        "redis": redis_status
    }
    if redis_error:
        result["redis_error"] = redis_error
    return jsonify(result)


@app.route('/set/<key>/<value>')
def set_key(key, value):
    if not redis_client:
        return jsonify({"error": "Redis not available"}), 503
    try:
        redis_client.set(key, value)
        return jsonify({"set": key, "value": value})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/get/<key>')
def get_key(key):
    if not redis_client:
        return jsonify({"error": "Redis not available"}), 503
    try:
        value = redis_client.get(key)
        return jsonify({"key": key, "value": value})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)

