#!/usr/bin/env python3
"""
Simulated API server for load testing and memory analysis.
This server processes requests using the api_framework module.
"""

import sys
import json
import tracemalloc
import gc
from typing import Any

# Add the current directory to path for imports
sys.path.insert(0, '/app')

from api_framework import (
    Request,
    run_pipeline,
    Errors,
    APIError,
)


def generate_test_requests(count: int) -> list[Request]:
    """Generate a batch of test requests."""
    requests = []
    
    # Valid requests
    for i in range(count // 2):
        requests.append(Request(
            data={"action": "read", "item_id": f"item_{i}"},
            headers={"Authorization": f"Bearer validtoken{i:08d}"},
            user_id=None
        ))
    
    # Requests that will trigger various errors
    error_requests = [
        # Invalid token errors
        Request(
            data={"action": "read"},
            headers={"Authorization": "Bearer short"},
            user_id=None
        ),
        # Permission denied
        Request(
            data={"action": "admin_delete", "target": "system"},
            headers={"Authorization": "Bearer validtoken12345678"},
            user_id=None
        ),
        # User not found
        Request(
            data={"action": "lookup_user", "user_id": "nonexistent_user"},
            headers={"Authorization": "Bearer validtoken12345678"},
            user_id=None
        ),
        # Resource not found
        Request(
            data={"action": "access_resource", "resource_id": "nonexistent"},
            headers={"Authorization": "Bearer validtoken12345678"},
            user_id=None
        ),
        # Duplicate entry
        Request(
            data={"action": "create_entry", "entry_name": "entry_1"},
            headers={"Authorization": "Bearer validtoken12345678"},
            user_id=None
        ),
        # Rate limit
        Request(
            data={"action": "read"},
            headers={"Authorization": "Bearer validtoken12345678", "X-Request-Count": "150"},
            user_id=None
        ),
    ]
    
    # Repeat error requests to fill remaining count
    while len(requests) < count:
        requests.extend(error_requests)
    
    return requests[:count]


def get_memory_usage_mb() -> float:
    """Get current memory usage in MB."""
    import os
    try:
        with open(f'/proc/{os.getpid()}/status', 'r') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return float(line.split()[1]) / 1024
    except Exception:
        pass
    return tracemalloc.get_traced_memory()[0] / 1024 / 1024


def run_server(requests_count: int = 1000, profile: bool = False) -> dict[str, Any]:
    """
    Run the server simulation.
    
    Args:
        requests_count: Number of requests to process
        profile: Whether to enable memory profiling
    
    Returns:
        Dictionary with results and memory statistics
    """
    if profile:
        tracemalloc.start()
    
    # Generate requests
    requests = generate_test_requests(requests_count)
    
    # Process requests
    results = {
        "total": requests_count,
        "success": 0,
        "errors": {},
    }
    
    memory_samples = []
    
    for i, request in enumerate(requests):
        response = run_pipeline(request)
        
        if response.error:
            error_type = type(response.error).__name__
            error_msg = response.error.message
            key = f"{error_type}: {error_msg}"
            results["errors"][key] = results["errors"].get(key, 0) + 1
        else:
            results["success"] += 1
        
        # Sample memory periodically
        if i % 100 == 0:
            memory_samples.append(get_memory_usage_mb())
    
    # Force garbage collection
    gc.collect()
    
    # Get final memory
    final_memory = get_memory_usage_mb()
    
    results["memory_samples"] = memory_samples
    results["final_memory_mb"] = final_memory
    
    if profile:
        current, peak = tracemalloc.get_traced_memory()
        results["peak_memory_mb"] = peak / 1024 / 1024
        results["current_memory_mb"] = current / 1024 / 1024
        
        # Get top memory allocations
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics('lineno')
        results["top_allocations"] = [
            {"file": str(stat.traceback[0].filename), 
             "line": stat.traceback[0].lineno,
             "size_kb": stat.size / 1024}
            for stat in top_stats[:10]
        ]
        tracemalloc.stop()
    
    return results


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="API Server Simulator")
    parser.add_argument("--requests", type=int, default=1000,
                        help="Number of requests to process")
    parser.add_argument("--profile", action="store_true",
                        help="Enable memory profiling")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file for results (JSON)")
    
    args = parser.parse_args()
    
    print(f"Processing {args.requests} requests...")
    print(f"Memory profiling: {'enabled' if args.profile else 'disabled'}")
    
    results = run_server(args.requests, args.profile)
    
    print(f"\nResults:")
    print(f"  Total requests: {results['total']}")
    print(f"  Successful: {results['success']}")
    print(f"  Errors: {sum(results['errors'].values())}")
    
    if args.profile:
        print(f"\nMemory Statistics:")
        print(f"  Peak memory: {results['peak_memory_mb']:.2f} MB")
        print(f"  Current memory: {results['current_memory_mb']:.2f} MB")
        print(f"\nTop allocations:")
        for alloc in results["top_allocations"][:5]:
            print(f"  {alloc['file']}:{alloc['line']} - {alloc['size_kb']:.1f} KB")
    
    if args.output:
        with open(args.output, 'w') as f:
            # Convert non-serializable items
            output = {
                "total": results["total"],
                "success": results["success"],
                "errors": results["errors"],
                "final_memory_mb": results["final_memory_mb"],
            }
            if args.profile:
                output["peak_memory_mb"] = results["peak_memory_mb"]
                output["current_memory_mb"] = results["current_memory_mb"]
            json.dump(output, f, indent=2)
        print(f"\nResults saved to {args.output}")
    
    return results


if __name__ == "__main__":
    main()
