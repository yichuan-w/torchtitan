#!/usr/bin/env python3
"""Simple Flask application for the web service stack."""

from flask import Flask, jsonify
import redis
import psycopg2

app = Flask(__name__)

def get_db_connection():
    """Create a database connection."""
    import os
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=os.environ.get('DB_PORT', '5432'),
        database=os.environ.get('DB_NAME', 'appdb'),
        user=os.environ.get('DB_USER', 'appuser'),
        password=os.environ.get('DB_PASSWORD', 'apppass123')
    )

def get_redis_connection():
    """Create a Redis connection."""
    import os
    return redis.Redis(
        host=os.environ.get('REDIS_HOST', 'localhost'),
        port=int(os.environ.get('REDIS_PORT', 6379)),
        password=os.environ.get('REDIS_PASSWORD'),
        decode_responses=True
    )

@app.route('/')
def index():
    """Health check endpoint."""
    return jsonify({
        'status': 'ok',
        'service': 'webapp',
        'message': 'Application is running'
    })

@app.route('/health')
def health():
    """Detailed health check."""
    health_status = {'database': 'unknown', 'cache': 'unknown'}
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT 1')
        cur.close()
        conn.close()
        health_status['database'] = 'connected'
    except Exception as e:
        health_status['database'] = f'error: {str(e)}'
    
    try:
        r = get_redis_connection()
        r.ping()
        health_status['cache'] = 'connected'
    except Exception as e:
        health_status['cache'] = f'error: {str(e)}'
    
    return jsonify(health_status)

@app.route('/api/data')
def get_data():
    """Example API endpoint."""
    try:
        r = get_redis_connection()
        count = r.incr('visit_count')
        return jsonify({'visit_count': count})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
