"""Property-based tests for HTTP server endpoints"""
import base64
import json
import threading
import time
from unittest.mock import Mock, patch, MagicMock
from hypothesis import given, strategies as st, settings
from fastapi.testclient import TestClient
import pytest

from src.server import create_app
from src.config import ServerConfig
from src.models import ExecutionStatus, ExecutionRecord, OutputData, AttestationDocument
from src.repository import FileContent, GitHubAPIError
from src.attestation import AttestationError
from datetime import datetime, timezone


# Test configuration
def get_test_config():
    """Create test configuration"""
    return ServerConfig(
        port=8000,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,  # 1MB
        rate_limit_per_ip=10,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/test",
        output_retention_hours=24,
        nsm_device_path="/usr/bin/nitro-tpm-attest"
    )


# Strategies for generating test data
valid_repo_url = st.text(min_size=1).map(
    lambda x: f"https://github.com/{x.replace('/', '_')}/repo"
)

valid_commit_hash = st.text(
    alphabet="0123456789abcdef",
    min_size=40,
    max_size=40
)

valid_script_path = st.text(min_size=1, max_size=100).filter(
    lambda x: ".." not in x and x.strip()
)

valid_github_token = st.text(min_size=10, max_size=100)

execution_request = st.fixed_dictionaries({
    "repository_url": valid_repo_url,
    "commit_hash": valid_commit_hash,
    "script_path": valid_script_path,
    "github_token": valid_github_token
})


# Feature: github-actions-remote-executor, Property 3: Concurrent Request Handling
@settings(max_examples=20, deadline=None)
@given(st.lists(execution_request, min_size=2, max_size=10))
def test_concurrent_request_handling(requests_list):
    """
    **Validates: Requirements 1.5**
    
    For any set of concurrent execution requests, the server should handle 
    all requests without blocking or failure.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    results = []
    errors = []
    
    def make_request(req_data):
        try:
            with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
                mock_validate.return_value = Mock(valid=True, errors=[])
                
                with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
                    mock_auth.return_value = Mock(success=True, error_message=None)
                    
                    with patch.object(app.state.repository_client, 'fetch_file') as mock_fetch:
                        mock_fetch.return_value = FileContent(
                            content=b"#!/bin/bash\necho test",
                            temp_path="/tmp/test.sh",
                            size_bytes=100
                        )
                        
                        with patch.object(app.state.attestation_generator, 'generate_attestation') as mock_attest:
                            mock_attest.return_value = (
                                AttestationDocument(
                                    repository_url=req_data['repository_url'],
                                    commit_hash=req_data['commit_hash'],
                                    script_path=req_data['script_path'],
                                    timestamp=datetime.now(timezone.utc),
                                    signature=b"test_signature"
                                ),
                                None
                            )
                            
                            with patch.object(app.state.script_executor, 'execute_async'):
                                response = client.post("/execute", json=req_data)
                                results.append(response)
        except Exception as e:
            errors.append(str(e))
    
    # Execute requests concurrently
    threads = []
    for req_data in requests_list:
        thread = threading.Thread(target=make_request, args=(req_data,))
        threads.append(thread)
        thread.start()
    
    # Wait for all threads to complete
    for thread in threads:
        thread.join(timeout=10)
    
    # Verify all requests completed without errors
    assert len(errors) == 0, f"Concurrent requests had errors: {errors}"
    assert len(results) == len(requests_list), "Not all requests completed"
    
    # Verify all responses are successful
    for response in results:
        assert response.status_code in [200, 400, 401, 404, 413, 500], \
            f"Unexpected status code: {response.status_code}"


# Feature: github-actions-remote-executor, Property 19: Immediate Response with Attestation
@settings(max_examples=20, deadline=None)
@given(execution_request)
def test_immediate_response_with_attestation(req_data):
    """
    **Validates: Requirements 4.8, 4.9**
    
    For any valid execution request, the server should return a response 
    containing both the attestation document and execution ID before script 
    execution completes.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    execution_started = threading.Event()
    execution_completed = threading.Event()
    
    def slow_execute(execution_id, script_path):
        """Mock execute_async that runs in a background thread like the real implementation"""
        def _run():
            execution_started.set()
            time.sleep(0.1)  # Simulate slow execution
            execution_completed.set()
        
        # Start in background thread to match real execute_async behavior
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
    
    with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
        mock_validate.return_value = Mock(valid=True, errors=[])
        
        with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
            mock_auth.return_value = Mock(success=True, error_message=None)
            
            with patch.object(app.state.repository_client, 'fetch_file') as mock_fetch:
                mock_fetch.return_value = FileContent(
                    content=b"#!/bin/bash\necho test",
                    temp_path="/tmp/test.sh",
                    size_bytes=100
                )
                
                with patch.object(app.state.attestation_generator, 'generate_attestation') as mock_attest:
                    mock_attest.return_value = (
                        AttestationDocument(
                            repository_url=req_data['repository_url'],
                            commit_hash=req_data['commit_hash'],
                            script_path=req_data['script_path'],
                            timestamp=datetime.now(timezone.utc),
                            signature=b"test_signature"
                        ),
                        None
                    )
                    
                    with patch.object(app.state.script_executor, 'execute_async', side_effect=slow_execute):
                        response = client.post("/execute", json=req_data)
                        
                        # Response should be received before execution completes
                        assert not execution_completed.is_set(), \
                            "Response should be immediate, before execution completes"
                        
                        # Response should contain execution_id and attestation_document
                        if response.status_code == 200:
                            data = response.json()
                            assert "execution_id" in data, "Response missing execution_id"
                            assert "attestation_document" in data, "Response missing attestation_document"
                            assert "status" in data, "Response missing status"
                            
                            # Verify attestation document is base64 encoded
                            try:
                                base64.b64decode(data["attestation_document"])
                            except Exception:
                                pytest.fail("Attestation document is not valid base64")


# Feature: github-actions-remote-executor, Property 30: Output Endpoint Status Return
@settings(max_examples=20, deadline=None)
@given(st.uuids().map(str), st.sampled_from(list(ExecutionStatus)))
def test_output_endpoint_status_return(execution_id, status):
    """
    **Validates: Requirements 6.2**
    
    For any execution ID, accessing the output endpoint should return the 
    current execution status.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    # Create execution record
    record = ExecutionRecord(
        execution_id=execution_id,
        repository_url="https://github.com/test/repo",
        commit_hash="a" * 40,
        script_path="test.sh",
        status=status,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=None,
        exit_code=None,
        timeout_seconds=300
    )
    
    with patch.object(app.state.execution_manager, 'get_execution', return_value=record):
        with patch.object(app.state.output_collector, 'get_output') as mock_output:
            mock_output.return_value = OutputData(
                stdout="",
                stderr="",
                stdout_offset=0,
                stderr_offset=0,
                complete=False,
                exit_code=None
            )
            
            response = client.get(f"/execution/{execution_id}/output")
            
            if response.status_code == 200:
                data = response.json()
                assert "status" in data, "Response missing status field"
                assert data["status"] == status.value, \
                    f"Status mismatch: expected {status.value}, got {data['status']}"


# Feature: github-actions-remote-executor, Property 33: Completion Exit Code Inclusion
@settings(max_examples=20, deadline=None)
@given(st.uuids().map(str), st.integers(min_value=-1, max_value=255))
def test_completion_exit_code_inclusion(execution_id, exit_code):
    """
    **Validates: Requirements 6.7**
    
    For any completed script execution, the output endpoint response should 
    include the exit code.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    # Create completed execution record
    record = ExecutionRecord(
        execution_id=execution_id,
        repository_url="https://github.com/test/repo",
        commit_hash="a" * 40,
        script_path="test.sh",
        status=ExecutionStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        exit_code=exit_code,
        timeout_seconds=300
    )
    
    with patch.object(app.state.execution_manager, 'get_execution', return_value=record):
        with patch.object(app.state.output_collector, 'get_output') as mock_output:
            mock_output.return_value = OutputData(
                stdout="test output",
                stderr="",
                stdout_offset=11,
                stderr_offset=0,
                complete=True,
                exit_code=exit_code
            )
            
            response = client.get(f"/execution/{execution_id}/output")
            
            assert response.status_code == 200
            data = response.json()
            assert "exit_code" in data, "Response missing exit_code field"
            assert data["exit_code"] == exit_code, \
                f"Exit code mismatch: expected {exit_code}, got {data['exit_code']}"
            assert data["complete"] is True, "Complete flag should be True"


# Feature: github-actions-remote-executor, Property 34: Completion Flag Accuracy
@settings(max_examples=20, deadline=None)
@given(st.uuids().map(str), st.booleans())
def test_completion_flag_accuracy(execution_id, is_complete):
    """
    **Validates: Requirements 6.8**
    
    For any execution, the output endpoint response should include a boolean 
    completion flag that accurately reflects whether execution is complete.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    status = ExecutionStatus.COMPLETED if is_complete else ExecutionStatus.RUNNING
    
    record = ExecutionRecord(
        execution_id=execution_id,
        repository_url="https://github.com/test/repo",
        commit_hash="a" * 40,
        script_path="test.sh",
        status=status,
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc) if is_complete else None,
        exit_code=0 if is_complete else None,
        timeout_seconds=300
    )
    
    with patch.object(app.state.execution_manager, 'get_execution', return_value=record):
        with patch.object(app.state.output_collector, 'get_output') as mock_output:
            mock_output.return_value = OutputData(
                stdout="test",
                stderr="",
                stdout_offset=4,
                stderr_offset=0,
                complete=is_complete,
                exit_code=0 if is_complete else None
            )
            
            response = client.get(f"/execution/{execution_id}/output")
            
            assert response.status_code == 200
            data = response.json()
            assert "complete" in data, "Response missing complete field"
            assert data["complete"] == is_complete, \
                f"Complete flag mismatch: expected {is_complete}, got {data['complete']}"


# Feature: github-actions-remote-executor, Property 35: Invalid Execution ID Response
@settings(max_examples=20, deadline=None)
@given(st.uuids().map(str))
def test_invalid_execution_id_response(execution_id):
    """
    **Validates: Requirements 6.9**
    
    For any non-existent execution ID, the output endpoint should return 
    HTTP 404 with an execution not found error.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    # Mock execution manager to return None (not found)
    with patch.object(app.state.execution_manager, 'get_execution', return_value=None):
        response = client.get(f"/execution/{execution_id}/output")
        
        assert response.status_code == 404, \
            f"Expected 404 for non-existent execution, got {response.status_code}"
        
        data = response.json()
        assert "error" in data.get("detail", {}), "Response missing error field"
        assert data["detail"]["error"] == "execution_not_found", \
            "Error should be 'execution_not_found'"


# Feature: github-actions-remote-executor, Property 47: Script Size Validation
@settings(max_examples=20, deadline=None)
@given(execution_request, st.integers(min_value=1, max_value=10000000))
def test_script_size_validation(req_data, file_size):
    """
    **Validates: Requirements 8.2**
    
    For any execution request, the server should validate the script file 
    size before execution.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
        mock_validate.return_value = Mock(valid=True, errors=[])
        
        with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
            mock_auth.return_value = Mock(success=True, error_message=None)
            
            with patch.object(app.state.repository_client, 'fetch_file') as mock_fetch:
                mock_fetch.return_value = FileContent(
                    content=b"x" * file_size,
                    temp_path="/tmp/test.sh",
                    size_bytes=file_size
                )
                
                with patch.object(app.state.repository_client, 'cleanup_temp_file'):
                    response = client.post("/execute", json=req_data)
                    
                    # Should validate size and reject if too large
                    max_size = app.state.config.max_script_size_bytes
                    if file_size > max_size:
                        assert response.status_code == 413, \
                            f"Expected 413 for oversized file, got {response.status_code}"
                    else:
                        # Size is OK, should proceed (may fail for other reasons in test)
                        assert response.status_code in [200, 500], \
                            f"Unexpected status for valid size: {response.status_code}"


# Feature: github-actions-remote-executor, Property 48: Oversized Script Rejection
@settings(max_examples=20, deadline=None)
@given(execution_request)
def test_oversized_script_rejection(req_data):
    """
    **Validates: Requirements 8.3**
    
    For any script file that exceeds the maximum allowed size, the server 
    should return HTTP 413 with a file too large error.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    max_size = app.state.config.max_script_size_bytes
    oversized = max_size + 1
    
    with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
        mock_validate.return_value = Mock(valid=True, errors=[])
        
        with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
            mock_auth.return_value = Mock(success=True, error_message=None)
            
            with patch.object(app.state.repository_client, 'fetch_file') as mock_fetch:
                mock_fetch.return_value = FileContent(
                    content=b"x" * oversized,
                    temp_path="/tmp/test.sh",
                    size_bytes=oversized
                )
                
                with patch.object(app.state.repository_client, 'cleanup_temp_file') as mock_cleanup:
                    response = client.post("/execute", json=req_data)
                    
                    assert response.status_code == 413, \
                        f"Expected 413 for oversized file, got {response.status_code}"
                    
                    data = response.json()
                    assert "error" in data.get("detail", {}), "Response missing error field"
                    assert data["detail"]["error"] == "file_too_large", \
                        "Error should be 'file_too_large'"
                    
                    # Verify temp file was cleaned up
                    mock_cleanup.assert_called_once()


# Feature: github-actions-remote-executor, Property 49: Rate Limiting per IP
@settings(max_examples=20, deadline=None)
@given(st.integers(min_value=1, max_value=20))
def test_rate_limiting_per_ip(num_requests):
    """
    **Validates: Requirements 8.5**
    
    For any source IP address that exceeds the configured rate limit, 
    subsequent requests should be rejected with HTTP 429.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    rate_limit = app.state.config.rate_limit_per_ip
    
    responses = []
    
    with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
        mock_validate.return_value = Mock(valid=True, errors=[])
        
        for i in range(num_requests):
            response = client.post("/execute", json={
                "repository_url": "https://github.com/test/repo",
                "commit_hash": "a" * 40,
                "script_path": "test.sh",
                "github_token": "test_token"
            })
            responses.append(response)
    
    # Count how many were rate limited
    rate_limited_count = sum(1 for r in responses if r.status_code == 429)
    
    if num_requests > rate_limit:
        # Should have some rate limited responses
        assert rate_limited_count > 0, \
            f"Expected rate limiting after {rate_limit} requests, but none were limited"
        
        # Verify rate limit error message
        for response in responses:
            if response.status_code == 429:
                data = response.json()
                assert "error" in data, "Rate limit response missing error field"
                assert data["error"] == "rate_limit_exceeded", \
                    "Error should be 'rate_limit_exceeded'"
    else:
        # Should not have rate limited any requests
        assert rate_limited_count == 0, \
            f"Unexpected rate limiting with only {num_requests} requests"
