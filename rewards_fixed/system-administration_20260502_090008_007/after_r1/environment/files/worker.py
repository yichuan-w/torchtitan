#!/usr/bin/env python3
"""Background worker for processing tasks from the task queue."""

import os
import sys
import time
import json
import redis
import psycopg2

def get_db_connection():
    """Create a database connection."""
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=os.environ.get('DB_PORT', '5432'),
        database=os.environ.get('DB_NAME', 'appdb'),
        user=os.environ.get('DB_USER', 'appuser'),
        password=os.environ.get('DB_PASSWORD', 'apppass123')
    )

def get_redis_connection():
    """Create a Redis connection."""
    return redis.Redis(
        host=os.environ.get('REDIS_HOST', 'localhost'),
        port=int(os.environ.get('REDIS_PORT', 6379)),
        password=os.environ.get('REDIS_PASSWORD'),
        decode_responses=True
    )

def process_task(task_id, task_type, payload):
    """Process a single task."""
    print(f"Processing task {task_id}: {task_type}")
    
    # Simulate some work
    time.sleep(0.1)
    
    # Update task status
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE task_queue SET status = 'completed', processed_at = NOW() WHERE id = %s",
        (task_id,)
    )
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"Task {task_id} completed")

def main():
    """Main worker loop."""
    # Wait for connections to be ready
    max_retries = 30
    for i in range(max_retries):
        try:
            get_db_connection()
            get_redis_connection().ping()
            break
        except Exception as e:
            print(f"Waiting for services to be ready... ({i+1}/{max_retries})")
            time.sleep(1)
    else:
        print("Failed to connect to services")
        sys.exit(1)
    
    print("Worker started, listening for tasks...")
    
    r = get_redis_connection()
    
    while True:
        try:
            # Check for pending tasks in the database
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, task_type, payload FROM task_queue WHERE status = 'pending' ORDER BY created_at LIMIT 1"
            )
            task = cur.fetchone()
            
            if task:
                task_id, task_type, payload = task
                process_task(task_id, task_type, payload)
            else:
                # No tasks, wait a bit
                time.sleep(1)
            
            cur.close()
            conn.close()
        except Exception as e:
            print(f"Error processing tasks: {e}")
            time.sleep(5)

if __name__ == '__main__':
    main()
