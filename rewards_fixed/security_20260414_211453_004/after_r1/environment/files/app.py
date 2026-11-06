"""
URL Fetcher Service - A microservice for fetching URLs on behalf of clients.

This service provides a REST API for fetching content from URLs,
with SSRF protection based on hostname validation.
"""

import re
from urllib.parse import urlparse
from flask import Flask, request, jsonify
import requests
from requests.exceptions import RequestException

from config import (
    HOST,
    PORT,
    DEBUG,
    REQUEST_TIMEOUT,
    MAX_REDIRECTS,
    MAX_RESPONSE_SIZE,
    BLOCKED_HOSTNAMES,
    ALLOWED_PROTOCOLS,
    RATE_LIMIT_PER_MINUTE,
)


app = Flask(__name__)


def validate_url(url):
    """
    Validate a URL for SSRF protection.
    
    Uses urlparse to extract and validate the hostname against
    a blocklist of internal/reserved addresses.
    
    Args:
        url: The URL string to validate
        
    Returns:
        tuple: (is_valid: bool, parsed_url: ParseResult or None, error_message: str or None)
    """
    if not url:
        return False, None, "URL is required"
    
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, None, f"Invalid URL format: {str(e)}"
    
    # Check protocol
    if parsed.scheme.lower() not in ALLOWED_PROTOCOLS:
        return False, parsed, f"Protocol '{parsed.scheme}' not allowed. Use: {', '.join(ALLOWED_PROTOCOLS)}"
    
    # Extract hostname
    hostname = parsed.hostname
    
    # SECURITY ISSUE: Only blocks exact hostname matches (case-sensitive)
    # Vulnerable to: case variations, credentials bypass, URL parsing differentials, encoded chars
    if hostname and hostname in BLOCKED_HOSTNAMES:
        return False, parsed, f"Access to '{hostname}' is blocked for security reasons"
    
    return True, parsed, None


def fetch_url(url):
    """
    Fetch content from a URL.
    
    Args:
        url: The URL to fetch
        
    Returns:
        dict: Response data including status, headers, and content
    """
    # Validate URL first
    is_valid, parsed, error = validate_url(url)
    
    if not is_valid:
        return {
            "success": False,
            "error": error,
            "url": url
        }
    
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            stream=True
        )
        
        # Check response size
        content_length = response.headers.get('content-length')
        if content_length and int(content_length) > MAX_RESPONSE_SIZE:
            return {
                "success": False,
                "error": f"Response too large (max {MAX_RESPONSE_SIZE} bytes)",
                "url": url
            }
        
        # Read content with size limit
        content = b""
        for chunk in response.iter_content(chunk_size=8192):
            content += chunk
            if len(content) > MAX_RESPONSE_SIZE:
                return {
                    "success": False,
                    "error": f"Response too large (max {MAX_RESPONSE_SIZE} bytes)",
                    "url": url
                }
        
        # Try to decode as text
        try:
            text_content = content.decode('utf-8')
        except UnicodeDecodeError:
            text_content = None
        
        return {
            "success": True,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "content": text_content,
            "content_length": len(content),
            "final_url": response.url,
            "url": url
        }
        
    except requests.Timeout:
        return {
            "success": False,
            "error": f"Request timed out after {REQUEST_TIMEOUT} seconds",
            "url": url
        }
    except requests.TooManyRedirects:
        return {
            "success": False,
            "error": f"Too many redirects (max {MAX_REDIRECTS})",
            "url": url
        }
    except RequestException as e:
        return {
            "success": False,
            "error": f"Request failed: {str(e)}",
            "url": url
        }


@app.route('/')
def index():
    """API documentation endpoint."""
    return jsonify({
        "service": "URL Fetcher",
        "version": "1.2.0",
        "endpoints": {
            "/fetch": {
                "method": "GET",
                "description": "Fetch content from a URL",
                "parameters": {
                    "url": "The URL to fetch (required)"
                }
            },
            "/health": {
                "method": "GET",
                "description": "Health check endpoint"
            }
        }
    })


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "URL Fetcher"
    })


@app.route('/fetch')
def fetch():
    """
    Fetch content from a URL.
    
    Query Parameters:
        url: The URL to fetch (required)
    
    Returns:
        JSON response with fetch results
    """
    url = request.args.get('url')
    
    if not url:
        return jsonify({
            "success": False,
            "error": "URL parameter is required"
        }), 400
    
    result = fetch_url(url)
    
    if result["success"]:
        return jsonify(result)
    else:
        return jsonify(result), 400


# Simple in-memory rate limiting (for demonstration)
request_counts = {}


@app.before_request
def rate_limit():
    """Simple rate limiting middleware."""
    # This is a basic implementation - production would use Redis, etc.
    client_ip = request.remote_addr
    
    # Reset counter every minute (simplified)
    # In production, use proper sliding window
    pass  # Rate limiting disabled for this demo


if __name__ == '__main__':
    app.run(host=HOST, port=PORT, debug=DEBUG)
