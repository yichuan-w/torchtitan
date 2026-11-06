#!/usr/bin/env python
"""
Load testing script to demonstrate the async event loop blocking issue.
Sends concurrent requests to trigger the hang.
"""
import asyncio
import aiohttp
import time
import sys


async def fetch_product(session, url, product_id):
    """Fetch a single product."""
    try:
        # 5 second timeout - with 0.5s blocking per request, concurrent requests will stack
        async with session.get(f"{url}/api/products/{product_id}/", timeout=aiohttp.ClientTimeout(total=5)) as response:
            if response.status == 200:
                return await response.json()
            else:
                return {"error": f"http_{response.status}", "product_id": product_id}
    except asyncio.TimeoutError:
        return {"error": "timeout", "product_id": product_id}
    except Exception as e:
        return {"error": str(e), "product_id": product_id}


async def fetch_all_products(session, url):
    """Fetch all products."""
    try:
        # 8 second timeout
        async with session.get(f"{url}/api/products/", timeout=aiohttp.ClientTimeout(total=8)) as response:
            if response.status == 200:
                return await response.json()
            else:
                return {"error": f"http_{response.status}", "endpoint": "product-list"}
    except asyncio.TimeoutError:
        return {"error": "timeout", "endpoint": "product-list"}
    except Exception as e:
        return {"error": str(e), "endpoint": "product-list"}


async def create_order(session, url, order_data):
    """Create a new order."""
    try:
        # 5 second timeout
        async with session.post(f"{url}/api/orders/", json=order_data, timeout=aiohttp.ClientTimeout(total=5)) as response:
            if response.status in (200, 201):
                return await response.json()
            else:
                return {"error": f"http_{response.status}", "endpoint": "order-create"}
    except asyncio.TimeoutError:
        return {"error": "timeout", "endpoint": "order-create"}
    except Exception as e:
        return {"error": str(e), "endpoint": "order-create"}


async def concurrent_test(base_url, num_requests=10):
    """Run concurrent requests to trigger the blocking issue."""
    print(f"Running {num_requests} concurrent requests...")
    start_time = time.time()
    
    async with aiohttp.ClientSession() as session:
        # Mix of different endpoint requests
        tasks = []
        
        # Product list requests
        for i in range(3):
            tasks.append(fetch_all_products(session, base_url))
        
        # Individual product requests
        for i in range(1, num_requests + 1):
            tasks.append(fetch_product(session, base_url, i))
        
        # Order creation requests
        for i in range(2):
            order_data = {
                "order_number": f"ORD-TEST-{i:04d}",
                "customer_email": f"customer{i}@example.com",
                "notes": "Test order",
                "items": [
                    {"product": 1, "quantity": 2},
                    {"product": 2, "quantity": 1}
                ]
            }
            tasks.append(create_order(session, base_url, order_data))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    elapsed = time.time() - start_time
    
    # Analyze results
    successes = 0
    timeouts = 0
    errors = 0
    
    for result in results:
        if isinstance(result, Exception):
            errors += 1
        elif isinstance(result, dict):
            if "error" in result:
                if result["error"] == "timeout":
                    timeouts += 1
                else:
                    errors += 1
            else:
                successes += 1
    
    print(f"Completed in {elapsed:.2f}s")
    print(f"  Successes: {successes}")
    print(f"  Timeouts: {timeouts}")
    print(f"  Errors: {errors}")
    
    return {
        "elapsed": elapsed,
        "successes": successes,
        "timeouts": timeouts,
        "errors": errors,
        "total": len(results)
    }


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    num_requests = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    result = asyncio.run(concurrent_test(url, num_requests))
    
    # Exit with error if too many timeouts
    if result["timeouts"] > result["total"] * 0.3:
        print("\nWARNING: More than 30% of requests timed out!")
        print("This indicates the event loop is being blocked.")
        sys.exit(1)
    else:
        print("\nAll requests completed successfully!")
        sys.exit(0)
