# API Request Processing Framework

A simple framework for processing API requests with authentication, authorization, and validation.

## Overview

The framework consists of:

- `api_framework.py` - Core request processing logic with error handling
- `server.py` - Server simulation for load testing

## Running the Server

```bash
# Run with 1000 requests
python server.py --requests 1000

# Run with memory profiling
python server.py --requests 1000 --profile

# Save results to file
python server.py --requests 1000 --profile --output results.json
```

## Current Issue

The server has been experiencing gradual memory growth in production during extended operation. Initial investigation shows:

1. Memory usage increases over time even with garbage collection
2. The growth correlates with request processing volume
3. Standard memory profiling tools show allocations but the source is unclear

The issue appears after processing thousands of requests with various error conditions.
