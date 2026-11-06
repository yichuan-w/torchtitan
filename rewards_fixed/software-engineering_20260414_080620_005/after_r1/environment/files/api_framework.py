"""
Request processing framework for handling API requests.
This module provides a simple framework for processing JSON requests
with validation and error handling.
"""

import json
import traceback
from typing import Any, Callable


class APIError(Exception):
    """Base exception for API errors."""
    
    def __init__(self, message: str, code: int = 500):
        super().__init__(message)
        self.message = message
        self.code = code
    
    def to_dict(self) -> dict:
        return {
            "error": self.message,
            "code": self.code
        }


class Errors:
    """
    Pre-defined error instances for common API error cases.
    These are created once and reused throughout the application.
    """
    user_not_found = APIError("User not found", code=404)
    invalid_token = APIError("Invalid or expired token", code=401)
    permission_denied = APIError("Permission denied", code=403)
    validation_failed = APIError("Validation failed", code=400)
    rate_limit_exceeded = APIError("Rate limit exceeded", code=429)
    internal_error = APIError("Internal server error", code=500)
    resource_not_found = APIError("Resource not found", code=404)
    duplicate_entry = APIError("Duplicate entry exists", code=409)


class Request:
    """Represents an incoming API request."""
    
    def __init__(self, data: dict, headers: dict, user_id: str | None = None):
        self.data = data
        self.headers = headers
        self.user_id = user_id


class Response:
    """Represents an API response."""
    
    def __init__(self, data: Any = None, error: APIError | None = None, status: int = 200):
        self.data = data
        self.error = error
        self.status = status
    
    def to_json(self) -> str:
        if self.error:
            return json.dumps(self.error.to_dict())
        return json.dumps({"data": self.data, "status": "success"})


class RequestContext:
    """
    Context object passed through the request processing pipeline.
    Contains request data and accumulates processing metadata.
    """
    
    def __init__(self, request: Request):
        self.request = request
        self.user_id = request.user_id
        self.metadata: dict[str, Any] = {}
        self.processing_log: list[str] = []
        # Simulate some larger objects that might be in the call stack
        self.cache_snapshot: dict[str, Any] = {}
        self.query_results: list[dict] = []


def process_request(
    request: Request,
    handlers: list[Callable[[RequestContext], None]]
) -> Response:
    """
    Process a request through a pipeline of handlers.
    
    Args:
        request: The incoming request
        handlers: List of handler functions to process the request
    
    Returns:
        Response object with result or error
    """
    ctx = RequestContext(request)
    
    try:
        for handler in handlers:
            handler(ctx)
        
        return Response(data={"user_id": ctx.user_id, "metadata": ctx.metadata})
    
    except APIError as e:
        return Response(error=e, status=e.code)
    
    except Exception as e:
        return Response(error=Errors.internal_error, status=500)


# Sample handlers for the request pipeline

def authenticate(ctx: RequestContext) -> None:
    """Validate authentication token from headers."""
    token = ctx.request.headers.get("Authorization", "")
    
    if not token or not token.startswith("Bearer "):
        raise Errors.invalid_token
    
    # Simulate token validation
    token_value = token[7:]  # Remove "Bearer " prefix
    if len(token_value) < 10:
        raise Errors.invalid_token
    
    # Token is valid, extract user_id from token (simulated)
    ctx.user_id = f"user_{token_value[:8]}"


def authorize(ctx: RequestContext) -> None:
    """Check if user has permission to perform the action."""
    if not ctx.user_id:
        raise Errors.invalid_token
    
    # Simulate permission check
    action = ctx.request.data.get("action", "read")
    restricted_actions = ["admin_delete", "system_reset", "config_override"]
    
    if action in restricted_actions:
        raise Errors.permission_denied
    
    ctx.metadata["authorized_action"] = action


def validate_input(ctx: RequestContext) -> None:
    """Validate request input data."""
    data = ctx.request.data
    
    if not isinstance(data, dict):
        raise Errors.validation_failed
    
    required_fields = ["action"]
    for field in required_fields:
        if field not in data:
            raise Errors.validation_failed
    
    # Simulate building up context data (this contributes to traceback size)
    ctx.cache_snapshot = {
        "user_permissions": ["read", "write", "delete"],
        "org_settings": {"max_items": 1000, "rate_limit": 100},
        "feature_flags": {"new_ui": True, "beta_features": False}
    }
    
    ctx.query_results = [
        {"id": i, "name": f"item_{i}", "value": i * 100} 
        for i in range(50)
    ]
    
    ctx.processing_log.append("Input validated successfully")


def check_rate_limit(ctx: RequestContext) -> None:
    """Check if user has exceeded rate limit."""
    # Simulate rate limit check
    user_requests = ctx.request.headers.get("X-Request-Count", "0")
    
    try:
        count = int(user_requests)
        if count > 100:
            raise Errors.rate_limit_exceeded
    except ValueError:
        pass
    
    ctx.metadata["rate_limit_remaining"] = max(0, 100 - count)


def handle_user_lookup(ctx: RequestContext) -> None:
    """Handle user lookup action."""
    if ctx.request.data.get("action") != "lookup_user":
        return
    
    target_user = ctx.request.data.get("user_id")
    if not target_user:
        raise Errors.validation_failed
    
    # Simulate user lookup
    existing_users = ["user_abc12345", "user_def67890", "user_ghi11111"]
    
    if target_user not in existing_users:
        raise Errors.user_not_found
    
    ctx.metadata["found_user"] = target_user
    ctx.processing_log.append(f"Found user: {target_user}")


def handle_resource_access(ctx: RequestContext) -> None:
    """Handle resource access action."""
    if ctx.request.data.get("action") != "access_resource":
        return
    
    resource_id = ctx.request.data.get("resource_id")
    if not resource_id:
        raise Errors.validation_failed
    
    # Simulate resource lookup
    existing_resources = ["res_001", "res_002", "res_003"]
    
    if resource_id not in existing_resources:
        raise Errors.resource_not_found
    
    ctx.metadata["resource_accessed"] = resource_id
    ctx.processing_log.append(f"Accessed resource: {resource_id}")


def handle_duplicate_check(ctx: RequestContext) -> None:
    """Handle duplicate entry check."""
    if ctx.request.data.get("action") != "create_entry":
        return
    
    entry_name = ctx.request.data.get("entry_name")
    if not entry_name:
        raise Errors.validation_failed
    
    # Simulate duplicate check
    existing_entries = ["entry_1", "entry_2", "entry_3"]
    
    if entry_name in existing_entries:
        raise Errors.duplicate_entry
    
    ctx.metadata["entry_created"] = entry_name
    ctx.processing_log.append(f"Created entry: {entry_name}")


# Default pipeline
DEFAULT_HANDLERS = [
    authenticate,
    authorize,
    validate_input,
    check_rate_limit,
    handle_user_lookup,
    handle_resource_access,
    handle_duplicate_check,
]


def run_pipeline(request: Request, handlers: list[Callable] | None = None) -> Response:
    """Run the request through the default or custom pipeline."""
    if handlers is None:
        handlers = DEFAULT_HANDLERS
    return process_request(request, handlers)
