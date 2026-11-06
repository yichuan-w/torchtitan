#!/usr/bin/env python3
"""Database initialization script for the web application."""

import os
import sys
import psycopg2
import time

def get_connection():
    """Create a database connection."""
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=os.environ.get('DB_PORT', '5432'),
        database=os.environ.get('DB_NAME', 'appdb'),
        user=os.environ.get('DB_USER', 'appuser'),
        password=os.environ.get('DB_PASSWORD', 'apppass123')
    )

def init_database():
    """Initialize the database schema."""
    max_retries = 30
    for i in range(max_retries):
        try:
            conn = get_connection()
            cur = conn.cursor()
            
            # Create the required table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS items (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create a task queue table for the worker
            cur.execute('''
                CREATE TABLE IF NOT EXISTS task_queue (
                    id SERIAL PRIMARY KEY,
                    task_type VARCHAR(100) NOT NULL,
                    payload TEXT,
                    status VARCHAR(50) DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
            ''')
            
            conn.commit()
            cur.close()
            conn.close()
            print("Database initialized successfully")
            return True
        except psycopg2.OperationalError as e:
            print(f"Database not ready (attempt {i+1}/{max_retries}): {e}")
            time.sleep(1)
    
    print("Failed to initialize database after max retries")
    return False

if __name__ == '__main__':
    success = init_database()
    sys.exit(0 if success else 1)
