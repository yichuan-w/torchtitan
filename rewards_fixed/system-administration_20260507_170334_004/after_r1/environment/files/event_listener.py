#!/usr/bin/env python3
"""
Supervisor event listener for process state changes.
Logs events to stdout which supervisor captures to the events log.
"""

import sys
import os

def write_stdout(s):
    # Write to supervisor's stdout
    sys.stdout.write(s)
    sys.stdout.flush()

def write_stderr(s):
    sys.stderr.write(s)
    sys.stderr.flush()

def main():
    while True:
        # Read header line from supervisor
        header = sys.stdin.readline()
        if not header:
            continue
            
        # Parse header (format: "ver:3.0 server:supervisor serial:...")
        headers = {}
        for part in header.split():
            if ':' in part:
                key, value = part.split(':', 1)
                headers[key] = value
        
        # Read payload
        payload_length = int(headers.get('len', 0))
        payload = ''
        if payload_length > 0:
            payload = sys.stdin.read(payload_length)
        
        # Log the event
        event_type = headers.get('eventname', 'UNKNOWN')
        write_stdout(f"[{event_type}] {payload}\n")
        
        # Acknowledge the event
        write_stdout("RESULT 2\nOK")

if __name__ == '__main__':
    main()
