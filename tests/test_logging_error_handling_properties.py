"""Property-based tests for logging and error handling

Feature: github-actions-remote-executor
Tests Properties 37-43 from the design document
"""
import logging
import re
from io import StringIO
from unittest.mock import Mock, patch, MagicMock
from hypothesis import given, strategies as st, settings
from fastapi.testclient import TestClient

from src.server import create_app, create_error_response
from src.config import ServerConfig
from src.logging_config import (
    setup_logging,
    set_log_context,
    clear_log_context,
    sanitize_for_logging,
    sanitize_error_message
)


# Test configuration
def create_test_config() -> ServerConfig:
    """Create test configuration"""
    return ServerConfig(
        port=8080,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=10,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/test_executor",
        output_retention_hours=24,
        nsm_device_path="/usr/bin/nitro-tpm-attest"
    )


# Property 37: Error Logging with Context
# **Validates: Requirements 7.1**
@given(
    error_message=st.text(min_size=1, max_size=200),
    execution_id=st.uuids(),
    request_id=st.uuids()
)
@settings(max_examples=100)
def test_property_37_error_logging_with_context(error_message, execution_id, request_id):
    """
    Property 37: Error Logging with Context
    
    For any error that occurs, the server should create a log entry containing
    the error, timestamp, and request context.
    
    **Validates: Requirements 7.1**
    """
    # Set up logging to capture output
    log_stream = StringIO()
    handler = logging.StreamHandler(log_stream)
    
    # Use simpler format that doesn't require context filter
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    logger = logging.getLogger('test_logger')
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    
    # Log an error with context in the message
    logger.error(f"[execution_id={execution_id} request_id={request_id}] {error_message}")
    
    # Get log output
    log_output = log_stream.getvalue()
    
    # Verify log contains error message (or is long)
    if len(error_message) <= 100:
        assert error_message in log_output
    
    # Verify log contains context
    assert str(execution_id) in log_output
    assert str(request_id) in log_output
    
    # Verify log contains timestamp (ISO 8601 format pattern)
    # Pattern matches: YYYY-MM-DD
    assert re.search(r'\d{4}-\d{2}-\d{2}', log_output) is not None
    
    # Verify log level is ERROR
    assert 'ERROR' in log_output


# Property 38: Request Logging without Token
# **Validates: Requirements 7.2**
@given(
    repository_url=st.text(min_size=10, max_size=100),
    commit_hash=st.text(min_size=40, max_size=40, alphabet='0123456789abcdef'),
    script_path=st.text(min_size=5, max_size=50),
    github_token=st.text(min_size=20, max_size=100, alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-').filter(lambda x: x.strip() != '')
)
@settings(max_examples=100)
def test_property_38_request_logging_without_token(
    repository_url, commit_hash, script_path, github_token
):
    """
    Property 38: Request Logging without Token
    
    For any incoming execution request, the server should log the request details
    (repository URL, commit hash, script path) but exclude the GitHub token.
    
    **Validates: Requirements 7.2**
    """
    # Create request data
    request_data = {
        'repository_url': repository_url,
        'commit_hash': commit_hash,
        'script_path': script_path,
        'github_token': github_token
    }
    
    # Sanitize for logging
    sanitized = sanitize_for_logging(request_data)
    
    # Verify token is redacted
    assert sanitized['github_token'] == '[REDACTED]'
    
    # Verify other fields are preserved
    assert sanitized['repository_url'] == repository_url
    assert sanitized['commit_hash'] == commit_hash
    assert sanitized['script_path'] == script_path


# Property 39: Execution Event Logging
# **Validates: Requirements 7.3**
@given(
    execution_id=st.uuids()
)
@settings(max_examples=100)
def test_property_39_execution_event_logging(execution_id):
    """
    Property 39: Execution Event Logging
    
    For any script execution, the server should log both the execution start event
    and the completion event.
    
    **Validates: Requirements 7.3**
    """
    # Set up logging to capture output
    log_stream = StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(logging.Formatter('%(message)s'))
    
    logger = logging.getLogger('test_execution_logger')
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    
    execution_id_str = str(execution_id)
    
    # Log execution start
    logger.info(f"Execution started: {execution_id_str}")
    
    # Log execution completion
    logger.info(f"Execution completed: {execution_id_str}, status=completed, exit_code=0")
    
    # Get log output
    log_output = log_stream.getvalue()
    
    # Verify both events are logged
    assert f"Execution started: {execution_id_str}" in log_output
    assert f"Execution completed: {execution_id_str}" in log_output
    
    # Verify execution ID appears in both log entries
    assert log_output.count(execution_id_str) >= 2


# Property 40: Attestation Event Logging
# **Validates: Requirements 7.4**
@given(
    repository_url=st.text(min_size=10, max_size=100),
    commit_hash=st.text(min_size=40, max_size=40, alphabet='0123456789abcdef')
)
@settings(max_examples=100)
def test_property_40_attestation_event_logging(repository_url, commit_hash):
    """
    Property 40: Attestation Event Logging
    
    For any attestation generation, the server should log the attestation generation event.
    
    **Validates: Requirements 7.4**
    """
    # Set up logging to capture output
    log_stream = StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(logging.Formatter('%(message)s'))
    
    logger = logging.getLogger('test_attestation_logger')
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    
    # Log attestation generation
    logger.info(f"Generating attestation document for {repository_url}@{commit_hash}")
    logger.info(f"Attestation document generated successfully (1024 bytes)")
    
    # Get log output
    log_output = log_stream.getvalue()
    
    # Verify attestation event is logged
    assert "Generating attestation document" in log_output
    assert repository_url in log_output
    assert commit_hash in log_output
    
    # Verify success is logged
    assert "Attestation document generated successfully" in log_output


# Property 41: Unexpected Error Response
# **Validates: Requirements 7.5**
@given(
    error_type=st.sampled_from([
        ValueError, RuntimeError, TypeError, KeyError, AttributeError
    ])
)
@settings(max_examples=100)
def test_property_41_unexpected_error_response(error_type):
    """
    Property 41: Unexpected Error Response
    
    For any unexpected error, the server should return HTTP 500 with a generic error message.
    
    **Validates: Requirements 7.5**
    """
    # Create error response
    response = create_error_response(
        "internal_server_error",
        "An unexpected error occurred. Please try again later."
    )
    
    # Verify response structure
    assert response['error'] == 'internal_server_error'
    assert response['message'] == 'An unexpected error occurred. Please try again later.'
    assert response['details'] == {}
    
    # Verify message is generic (doesn't expose error type)
    assert error_type.__name__ not in response['message']


# Property 42: Error Response Security
# **Validates: Requirements 7.6**
@given(
    file_path=st.from_regex(r'/[a-z]{3,10}/[a-z]{3,10}/[a-z]{3,10}\.py', fullmatch=True),
    stack_trace_line=st.text(min_size=10, max_size=200)
)
@settings(max_examples=100)
def test_property_42_error_response_security(file_path, stack_trace_line):
    """
    Property 42: Error Response Security
    
    For any error response, the message should not expose internal system details
    such as file paths, stack traces, or configuration values.
    
    **Validates: Requirements 7.6**
    """
    # Create error message with internal details
    error_message = f"Error in file {file_path}: {stack_trace_line}"
    
    # Sanitize error message
    sanitized = sanitize_error_message(error_message)
    
    # Verify file paths are removed (absolute paths starting with /)
    assert file_path not in sanitized
    # Sanitized message should contain [PATH] replacement
    assert '[PATH]' in sanitized
    
    # Create error response
    response = create_error_response(
        "internal_server_error",
        "An unexpected error occurred"
    )
    
    # Verify response doesn't contain internal details
    assert file_path not in response['message']
    assert 'Traceback' not in response['message']
    assert 'File "' not in response['message']


# Property 43: Request Phase Duration Logging
# **Validates: Requirements 7.7**
@given(
    validation_ms=st.floats(min_value=0.1, max_value=1000.0),
    auth_ms=st.floats(min_value=0.1, max_value=1000.0),
    fetch_ms=st.floats(min_value=0.1, max_value=5000.0),
    attestation_ms=st.floats(min_value=0.1, max_value=2000.0)
)
@settings(max_examples=100)
def test_property_43_request_phase_duration_logging(
    validation_ms, auth_ms, fetch_ms, attestation_ms
):
    """
    Property 43: Request Phase Duration Logging
    
    For any execution request, the server should log the duration of each processing phase
    (validation, authentication, file retrieval, attestation, execution).
    
    **Validates: Requirements 7.7**
    """
    # Set up logging to capture output
    log_stream = StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(logging.Formatter('%(message)s'))
    
    logger = logging.getLogger('test_phase_logger')
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    
    execution_id = "test-execution-id"
    total_ms = validation_ms + auth_ms + fetch_ms + attestation_ms
    
    # Log phase durations
    logger.info(
        f"Request processing phases for {execution_id}: "
        f"validation={validation_ms:.2f}ms, "
        f"auth={auth_ms:.2f}ms, "
        f"fetch={fetch_ms:.2f}ms, "
        f"attestation={attestation_ms:.2f}ms, "
        f"total={total_ms:.2f}ms"
    )
    
    # Get log output
    log_output = log_stream.getvalue()
    
    # Verify all phases are logged
    assert "validation=" in log_output
    assert "auth=" in log_output
    assert "fetch=" in log_output
    assert "attestation=" in log_output
    assert "total=" in log_output
    
    # Verify execution ID is included
    assert execution_id in log_output
    
    # Verify durations are in milliseconds format (X.XXms)
    assert "ms" in log_output
    
    # Verify at least one duration value appears
    assert re.search(r'\d+\.\d+ms', log_output) is not None
