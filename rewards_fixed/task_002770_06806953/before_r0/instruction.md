You are a deployment engineer preparing an automated, interactive rollout script for a local development proxy. We need a Bash script that sets up a user-space Nginx reverse proxy with TLS, ensuring developers can securely test local services without requiring root privileges.

Create a Bash script at `/home/user/setup_local_proxy.sh`. The script must satisfy the following requirements:

1. **Interactive Prompt:** The script must interactively prompt the user exactly with the string `"Enter deployment tier: "` (without a newline after the prompt). It should read the input into a variable. 
2. **TLS Certificate Generation:** The script must create a directory `/home/user/certs/`. Inside this directory, use `openssl` to silently generate a self-signed RSA 2048-bit certificate (`cert.pem`) and private key (`key.pem`) valid for 365 days for the Common Name (CN) `localhost`.
3. **Nginx Configuration:** The script must generate an Nginx configuration file at `/home/user/proxy.conf`. The configuration must:
   - Run entirely in user-space (do not set `user` directives that require root).
   - Have a `events {}` block.
   - Have an `http` block containing a `server` block.
   - Set the `pid` file to `/home/user/nginx.pid`.
   - Error log to `/home/user/error.log` and access log to `/home/user/access.log`.
   - Listen on `127.0.0.1:8443` with `ssl` enabled.
   - Use the generated `/home/user/certs/cert.pem` and `/home/user/certs/key.pem`.
   - Proxy all requests (`location /`) to `http://127.0.0.1:8080`.
4. **Validation:** The script must run Nginx's configuration test (`nginx -t -c /home/user/proxy.conf`). 
5. **Logging:** Finally, the script must compute the SHA256 fingerprint of the generated `cert.pem` and append a single line to `/home/user/deploy.log` in this exact format:
   `Tier: <entered_tier>, Cert SHA256: <fingerprint>`
   *(Note: The fingerprint should be the standard OpenSSL SHA256 fingerprint output, e.g., `DA:39:A3:EE:5E:6B:4B:0D:32:55:BF:EF:95:60:18:90:AF:D8:07:09`, without the "SHA256 Fingerprint=" prefix. Extract just the hex string).*

Ensure the script is executable (`chmod +x /home/user/setup_local_proxy.sh`).