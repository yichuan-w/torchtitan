A Python API request processing framework in `/app/` is experiencing gradual memory growth during extended operation in production. The server processes thousands of requests and memory usage continues to increase despite garbage collection.

The framework consists of:
- `api_framework.py` - Core request processing with error handling
- `server.py` - Server simulation for load testing

You can reproduce the issue by running:
```bash
python server.py --requests 5000 --profile
```

Your task:
1. Diagnose the root cause of the memory growth
2. Fix the issue in `api_framework.py` - the framework must continue to raise the same error types with the same messages and codes
3. The fix should result in stable memory usage during extended operation

After fixing, running the server with 5000 requests should show minimal memory growth between the start and end of processing.
