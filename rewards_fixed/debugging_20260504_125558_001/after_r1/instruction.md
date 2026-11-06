The inventory API server at `/app` becomes completely unresponsive under concurrent load. It's a Django async application that should handle multiple requests efficiently, but something is causing requests to timeout when the server receives concurrent requests. Fix the application in `/app` without changing the pricing service or the load test.

When I run `python load_test.py http://127.0.0.1:8000 15` with the server running, more than 30% of requests timeout after 5 seconds, even though the database queries are simple and there are only 11 products. Under serial access everything works fine - it's only when multiple requests hit the server simultaneously that it hangs.

The async views are being used (you can see `async def` in the views), and the database queries are wrapped with `sync_to_async`. Individual requests return correct responses and complete in about 0.5 seconds.

The server should handle at least 15 concurrent requests with every API call completing successfully and no timeouts. Product responses must continue to use the tax multiplier and currency rate returned by the pricing service. Each API call has a 5-second timeout in the load test, and under normal circumstances these endpoints should respond in well under 1 second.

Start the server with `./start_server.sh` (it runs on port 8000) and run the load test to see the issue. The load test exits with code 1 if more than 30% of requests timeout, and code 0 otherwise.
