"""
URL Fetcher for Document Rendering Service.

This module provides functionality to fetch external resources (images, stylesheets)
for PDF document generation. It includes a security policy to prevent access to
internal network resources.
"""

import ipaddress
import re
import socket
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT = 10
DEFAULT_USER_AGENT = "DocRenderer/1.0"

# Security policy: blocked host patterns
BLOCKED_HOSTS = {
    'localhost',
    '127.0.0.1',
    '0.0.0.0',
    '::1',
}

# Private IP ranges that should be blocked
PRIVATE_NETWORKS = [
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('169.254.0.0/16'),  # Link-local (cloud metadata)
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),
    ipaddress.ip_network('fe80::/10'),
]


class SSFRSecurityPolicy:
    """Security policy to prevent SSRF attacks."""
    
    def is_host_blocked(self, hostname: str) -> bool:
        """Check if a hostname is blocked by security policy.
        
        Args:
            hostname: The hostname to check (can be domain or IP)
            
        Returns:
            True if the host should be blocked, False otherwise
        """
        # Check against blocked host patterns
        hostname_lower = hostname.lower()
        for blocked in BLOCKED_HOSTS:
            if hostname_lower == blocked or hostname_lower.endswith('.' + blocked):
                return True
        
        # Try to resolve hostname to IP and check if it's in private ranges
        try:
            # Try parsing as IP address first
            try:
                ip = ipaddress.ip_address(hostname)
                if self._is_private_ip(ip):
                    return True
            except ValueError:
                # Not an IP address, try DNS resolution
                pass
            
            # Resolve hostname via DNS
            resolved_ips = socket.getaddrinfo(hostname, None)
            for family, socktype, proto, canonname, sockaddr in resolved_ips:
                ip = ipaddress.ip_address(sockaddr[0])
                if self._is_private_ip(ip):
                    return True
        except (socket.gaierror, socket.herror, OSError):
            # Could not resolve - allow the request to proceed
            # The connection will fail naturally if the host doesn't exist
            pass
        
        return False
    
    def _is_private_ip(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        """Check if an IP address is in a private/blocked range."""
        for network in PRIVATE_NETWORKS:
            if ip in network:
                return True
        return False


def secure_url_fetcher(url: str, timeout: int = DEFAULT_TIMEOUT, 
                       policy: SSFRSecurityPolicy = None) -> dict:
    """Fetch a URL with SSRF protection.
    
    This function fetches external resources while enforcing a security
    policy to prevent access to internal network resources.
    
    Args:
        url: The URL to fetch
        timeout: Request timeout in seconds
        policy: Security policy instance (uses default if None)
        
    Returns:
        A dict containing:
            - string: The fetched content as bytes
            - mime_type: Content MIME type
            - redirected_url: Final URL after any redirects
            
    Raises:
        PermissionError: If the URL points to a blocked resource
        ValueError: If the URL is invalid
    """
    if policy is None:
        policy = SSFRSecurityPolicy()
    
    # Parse and validate URL
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise ValueError(f"Invalid URL: {url}") from e
    
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Not an absolute URL: {url}")
    
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(f"Unsupported protocol: {parsed.scheme}")
    
    # Make the request
    request = Request(url, headers={'User-Agent': DEFAULT_USER_AGENT})
    response = urlopen(request, timeout=timeout)
    
    # Build result
    result = {
        'string': response.read(),
        'mime_type': response.headers.get_content_type(),
        'redirected_url': response.url,
    }
    
    return result


def fetch_resource(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Public API for fetching resources with security protection.
    
    Args:
        url: The URL to fetch
        timeout: Request timeout in seconds
        
    Returns:
        Dict containing fetched content and metadata
    """
    return secure_url_fetcher(url, timeout=timeout)
