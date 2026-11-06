A document rendering service at /app uses a custom URL fetcher to fetch external resources while generating PDFs. The service has a security policy to prevent access to internal network resources like localhost and private IP ranges.

However, a recent security audit flagged potential SSRF vulnerabilities in the URL fetching mechanism. Your task is to:

1. Analyze the code in `/app/url_fetcher.py` for SSRF vulnerabilities
2. Create a vulnerability report at `/app/report.jsonl` documenting any SSRF vulnerabilities found. Each entry must include:
   - `file_path`: the vulnerable file path (e.g., "/app/url_fetcher.py")
   - `cwe_id`: a list of relevant CWE identifiers in lowercase (e.g., ["cwe-918"])

3. Fix the vulnerability by modifying `/app/url_fetcher.py` to properly validate all URLs, including redirect destinations, against the security policy

4. Ensure the fix passes the existing test suite. Run `pytest /tests/test_outputs.py -rA` to verify

The security policy must block:
- Localhost and 127.0.0.1
- Private IP ranges (10.x.x.x, 172.16.x.x-172.31.x.x, 192.168.x.x)
- Cloud metadata endpoints (169.254.169.254)

The fix must raise `PermissionError` when access to blocked resources is attempted, including when redirects lead to blocked destinations.
