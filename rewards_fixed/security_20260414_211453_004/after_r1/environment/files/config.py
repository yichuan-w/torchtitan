# URL Fetcher Service Configuration
SERVICE_NAME = "url-fetcher"
VERSION = "1.2.0"

# Server settings
HOST = "0.0.0.0"
PORT = 5000
DEBUG = False

# Request settings
REQUEST_TIMEOUT = 10  # seconds
MAX_REDIRECTS = 5
MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB

# Blocked hostnames for SSRF protection (minimal list - vulnerable version)
BLOCKED_HOSTNAMES = [
    "localhost",
    "127.0.0.1",
]

# Allowed protocols
ALLOWED_PROTOCOLS = ["http", "https"]

# Rate limiting
RATE_LIMIT_PER_MINUTE = 60
