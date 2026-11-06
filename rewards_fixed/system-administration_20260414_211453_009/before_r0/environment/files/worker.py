#!/usr/bin/env python3
"""Worker process - processes jobs from Redis queue"""
import os
import sys
import time
import json

# Add library path
sys.path.insert(0, '/opt/myapp/lib')

import redis

def main():
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
    client = redis.from_url(redis_url)
    
    print(f"Worker started, connecting to {redis_url}")
    
    while True:
        # Pop job from queue
        job = client.lpop('job_queue')
        if job:
            data = json.loads(job)
            print(f"Processing job: {data.get('id', 'unknown')}")
            # Simulate work
            time.sleep(0.1)
        else:
            time.sleep(1)

if __name__ == '__main__':
    main()
