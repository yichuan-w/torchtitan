"""
Tests for the API framework memory leak fix.
Verifies that the framework functions correctly and doesn't leak memory.
"""

import subprocess
import sys
import json
import gc
import tracemalloc

sys.path.insert(0, '/app')


def test_errors_return_fresh_instances():
    """
    Test that each access to an error returns a new instance.
    This ensures exceptions are not reused, preventing traceback accumulation.
    
    The fix can be implemented via:
    - Factory methods (callable attributes)
    - Metaclass (__getattribute__ or __getattr__)
    - Property descriptors
    - Any approach that returns fresh instances
    """
    from api_framework import Errors
    
    for attr_name in ['user_not_found', 'invalid_token', 'permission_denied',
                       'validation_failed', 'rate_limit_exceeded', 'internal_error',
                       'resource_not_found', 'duplicate_entry']:
        
        # Get the attribute twice
        attr1 = getattr(Errors, attr_name)
        attr2 = getattr(Errors, attr_name)
        
        # If it's callable (factory method pattern), call it
        if callable(attr1) and not isinstance(attr1, type):
            instance1 = attr1()
            instance2 = attr2()
        else:
            # Otherwise, assume it's already an instance (metaclass/property pattern)
            instance1 = attr1
            instance2 = attr2
        
        # Each access must return a fresh instance
        assert instance1 is not instance2, (
            f"Errors.{attr_name} must return fresh instances each time, "
            f"but got the same object twice. This causes traceback accumulation."
        )


def test_errors_have_correct_properties():
    """
    Test that errors have the correct message and code.
    Also verifies that fresh instances are returned.
    """
    from api_framework import Errors
    
    errors_to_check = [
        ('user_not_found', 'User not found', 404),
        ('invalid_token', 'Invalid or expired token', 401),
        ('permission_denied', 'Permission denied', 403),
        ('validation_failed', 'Validation failed', 400),
        ('rate_limit_exceeded', 'Rate limit exceeded', 429),
        ('internal_error', 'Internal server error', 500),
        ('resource_not_found', 'Resource not found', 404),
        ('duplicate_entry', 'Duplicate entry exists', 409),
    ]
    
    for attr_name, expected_msg, expected_code in errors_to_check:
        error_attr = getattr(Errors, attr_name)
        
        # If callable (factory method), call it
        if callable(error_attr) and not isinstance(error_attr, type):
            error = error_attr()
        else:
            error = error_attr
        
        assert error.message == expected_msg, (
            f"Errors.{attr_name} should have message '{expected_msg}', "
            f"got '{error.message}'"
        )
        assert error.code == expected_code, (
            f"Errors.{attr_name} should have code {expected_code}, "
            f"got {error.code}"
        )
        
        # Also verify fresh instances are returned
        error_attr2 = getattr(Errors, attr_name)
        if callable(error_attr2) and not isinstance(error_attr2, type):
            error2 = error_attr2()
        else:
            error2 = error_attr2
        
        assert error is not error2, (
            f"Errors.{attr_name} must return fresh instances each time"
        )


def test_errors_are_api_errors():
    """Test that errors are instances of APIError and fresh instances are returned."""
    from api_framework import Errors, APIError
    
    for attr_name in ['user_not_found', 'invalid_token', 'permission_denied', 
                       'validation_failed', 'rate_limit_exceeded', 'internal_error',
                       'resource_not_found', 'duplicate_entry']:
        error_attr = getattr(Errors, attr_name)
        
        # If callable (factory method), call it
        if callable(error_attr) and not isinstance(error_attr, type):
            error = error_attr()
        else:
            error = error_attr
        
        assert isinstance(error, APIError), (
            f"Errors.{attr_name} should be an APIError instance"
        )
        
        # Also verify fresh instances are returned
        error_attr2 = getattr(Errors, attr_name)
        if callable(error_attr2) and not isinstance(error_attr2, type):
            error2 = error_attr2()
        else:
            error2 = error_attr2
        
        assert error is not error2, (
            f"Errors.{attr_name} must return fresh instances each time"
        )


def test_handlers_raise_fresh_errors():
    """
    Test that handlers raise fresh error instances, not singletons.
    """
    from api_framework import Request, run_pipeline, Errors, APIError
    
    # Test invalid_token
    request = Request(
        data={"action": "read"},
        headers={"Authorization": "Bearer short"},
        user_id=None
    )
    response = run_pipeline(request)
    
    assert response.error is not None, "Invalid token should cause error"
    assert isinstance(response.error, APIError), "Error should be APIError"
    assert response.error.code == 401, "Should return 401 for invalid token"
    
    # Get a fresh instance to compare
    error_attr = Errors.invalid_token
    if callable(error_attr) and not isinstance(error_attr, type):
        fresh_instance = error_attr()
    else:
        fresh_instance = error_attr
    
    # The error from handler should be different from a fresh instance
    # (because the handler raised its own fresh instance)
    assert response.error is not fresh_instance, (
        "Handler should raise a fresh instance, not reuse the same object"
    )


def test_memory_leak_fixed():
    """
    Test that the memory leak is fixed.
    Process many requests and verify memory doesn't grow unboundedly.
    """
    from api_framework import Request, run_pipeline, Errors
    
    tracemalloc.start()
    
    # Generate requests that trigger various errors
    def create_error_requests():
        return [
            # Invalid token
            Request(
                data={"action": "read"},
                headers={"Authorization": "Bearer short"},
                user_id=None
            ),
            # Permission denied
            Request(
                data={"action": "admin_delete", "target": "system"},
                headers={"Authorization": "Bearer validtoken12345678"},
                user_id=None
            ),
            # User not found
            Request(
                data={"action": "lookup_user", "user_id": "nonexistent_user"},
                headers={"Authorization": "Bearer validtoken12345678"},
                user_id=None
            ),
            # Resource not found
            Request(
                data={"action": "access_resource", "resource_id": "nonexistent"},
                headers={"Authorization": "Bearer validtoken12345678"},
                user_id=None
            ),
            # Duplicate entry
            Request(
                data={"action": "create_entry", "entry_name": "entry_1"},
                headers={"Authorization": "Bearer validtoken12345678"},
                user_id=None
            ),
        ]
    
    # Process initial batch and get baseline memory
    for _ in range(100):
        for request in create_error_requests():
            try:
                run_pipeline(request)
            except Exception:
                pass
    
    gc.collect()
    baseline_memory = tracemalloc.get_traced_memory()[0]
    
    # Process many more requests
    for _ in range(500):
        for request in create_error_requests():
            try:
                run_pipeline(request)
            except Exception:
                pass
    
    gc.collect()
    final_memory = tracemalloc.get_traced_memory()[0]
    
    tracemalloc.stop()
    
    # Memory growth should be minimal (less than 1MB for 2500 error requests)
    # With the bug, memory would grow by many MB due to traceback accumulation
    memory_growth = final_memory - baseline_memory
    max_acceptable_growth = 1 * 1024 * 1024  # 1 MB
    
    assert memory_growth < max_acceptable_growth, (
        f"Memory grew by {memory_growth / 1024:.1f} KB after processing requests. "
        f"This indicates the memory leak is not fixed. "
        f"Maximum acceptable growth is {max_acceptable_growth / 1024:.1f} KB."
    )
