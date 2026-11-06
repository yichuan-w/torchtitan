A URL fetching microservice is deployed at `/app/app.py` that allows clients to retrieve content from remote URLs. The service implements SSRF protection by blocking requests to internal addresses like `localhost`, `127.0.0.1`, and cloud metadata endpoints.

However, a security audit flagged that the service might be vulnerable to SSRF bypass attacks. An internal metadata server runs on `localhost:8081` and should never be accessible through the fetch endpoint.

Your task:
1. Analyze the URL validation logic in `/app/app.py` to identify the SSRF vulnerability
2. Determine the root cause of the vulnerability and which CWE it corresponds to
3. Create a file `/app/report.jsonl` documenting the vulnerability with the format: `{"file_path": "/app/app.py", "cwe_id": ["cwe-XXX"]}`
4. Fix the vulnerability in `/app/app.py` so that SSRF bypass attempts are properly blocked
5. Ensure your fix handles the edge cases while maintaining normal functionality for legitimate URLs

The service should still allow fetching from external URLs after the fix. Run the tests with `pytest /tests/test_outputs.py -rA` to verify your fix.
