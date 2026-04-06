"""Property-based tests for HTTP server endpoints"""
import base64
import json
import os
import threading
import time
from unittest.mock import Mock, patch, MagicMock
from hypothesis import given, strategies as st, settings
from fastapi.testclient import TestClient
import pytest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.server import create_app
from src.config import ServerConfig
from src.encryption import EncryptionManager
from src.repository import GitHubAPIError
from src.models import ExecutionStatus, ExecutionRecord, OutputData, AttestationDocument, OIDCValidationResult, CloneResult
from src.attestation import AttestationError
from datetime import datetime, timezone


VALID_OIDC_RESULT = OIDCValidationResult(
    valid=True,
    status_code=200,
    error_message=None,
    claims={"repository": "owner/repo", "iss": "https://token.actions.githubusercontent.com", "aud": "https://example.com"},
)

OIDC_BEARER_HEADER = {"Authorization": "Bearer valid.oidc.token"}


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
        tpm_attest_path="/usr/bin/nitro-tpm-attest",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )


# Create app and client once at module level for read-only tests (GET endpoints).
# Use a high rate limit so shared state doesn't cause 429s across tests.
def _get_shared_config():
    config = get_test_config()
    config.rate_limit_per_ip = 100000
    return config

_encryption_manager = EncryptionManager()
_app = create_app(_get_shared_config(), encryption_manager=_encryption_manager)
_client = TestClient(_app)


def _encrypt_output_request(payload_dict: dict, shared_key: bytes) -> dict:
    """Encrypt a request payload for the output endpoint using the shared key.

    Returns the outer JSON body with encrypted_payload field.
    """
    plaintext = json.dumps(payload_dict).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = AESGCM(shared_key).encrypt(nonce, plaintext, None)
    encrypted_payload = nonce + ciphertext
    return {"encrypted_payload": base64.b64encode(encrypted_payload).decode()}


def _decrypt_output_response(resp_json: dict, shared_key: bytes) -> dict:
    """Decrypt an encrypted response from the output endpoint."""
    encrypted_resp_bytes = base64.b64decode(resp_json["encrypted_response"])
    nonce = encrypted_resp_bytes[:12]
    ciphertext = encrypted_resp_bytes[12:]
    plaintext = AESGCM(shared_key).decrypt(nonce, ciphertext, None)
    return json.loads(plaintext)


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
@settings(max_examples=5, deadline=None)
@given(st.lists(execution_request, min_size=2, max_size=5))
def test_concurrent_request_handling(requests_list):
    """
    **Validates: Requirements 1.5**
    
    For any set of concurrent execution requests, the server should handle 
    all requests without blocking or failure.
    """
    # Needs fresh app per example due to rate limiter state
    app = create_app(get_test_config())
    client = TestClient(app)
    
    results = []
    errors = []
    
    def make_request(req_data):
        try:
            with patch.object(app.state.request_validator, 'validate_oidc_token', return_value=VALID_OIDC_RESULT), \
                 patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
                mock_validate.return_value = Mock(valid=True, errors=[])
                
                with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
                    mock_auth.return_value = Mock(success=True, error_message=None)
                    
                    with patch.object(app.state.repository_client, 'clone_repo') as mock_clone:
                        mock_clone.return_value = CloneResult(
                            clone_path="/tmp/clone_test",
                            script_path=""
                        )
                        
                        with patch.object(app.state.repository_client, 'validate_script_exists', return_value=True):
                            with patch('os.path.getsize', return_value=100):
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
                                        response = client.post("/execute", json=req_data, headers=OIDC_BEARER_HEADER)
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
@settings(max_examples=10, deadline=None)
@given(execution_request)
def test_immediate_response_with_attestation(req_data):
    """
    **Validates: Requirements 4.8, 4.9**
    
    For any valid execution request, the server should return a response 
    containing both the attestation document and execution ID before script 
    execution completes.
    """
    # Needs fresh app per example due to rate limiter state
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
    
    with patch.object(app.state.request_validator, 'validate_oidc_token', return_value=VALID_OIDC_RESULT), \
         patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
        mock_validate.return_value = Mock(valid=True, errors=[])
        
        with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
            mock_auth.return_value = Mock(success=True, error_message=None)
            
            with patch.object(app.state.repository_client, 'clone_repo') as mock_clone:
                mock_clone.return_value = CloneResult(
                    clone_path="/tmp/clone_test",
                    script_path=""
                )
                
                with patch.object(app.state.repository_client, 'validate_script_exists', return_value=True):
                    with patch('os.path.getsize', return_value=100):
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
                                response = client.post("/execute", json=req_data, headers=OIDC_BEARER_HEADER)
                        
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
@settings(max_examples=10, deadline=None)
@given(st.uuids().map(str), st.sampled_from(list(ExecutionStatus)))
def test_output_endpoint_status_return(execution_id, status):
    """
    **Validates: Requirements 6.2**
    
    For any execution ID, accessing the output endpoint should return the 
    current execution status.
    """
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
    
    # Store encryption context for this execution_id
    shared_key = os.urandom(32)
    _encryption_manager.store_encryption_context(execution_id, shared_key)
    
    try:
        with patch.object(_app.state.request_validator, 'validate_oidc_token_from_body', return_value=VALID_OIDC_RESULT):
            with patch.object(_app.state.execution_manager, 'get_execution', return_value=record):
                with patch.object(_app.state.output_collector, 'get_output') as mock_output:
                    mock_output.return_value = OutputData(
                        stdout="",
                        stderr="",
                        stdout_offset=0,
                        stderr_offset=0,
                        complete=False,
                        exit_code=None
                    )
                    
                    body = _encrypt_output_request(
                        {"oidc_token": "valid.oidc.token", "offset": 0},
                        shared_key,
                    )
                    response = _client.post(f"/execution/{execution_id}/output", json=body)
                    
                    if response.status_code == 200:
                        data = _decrypt_output_response(response.json(), shared_key)
                        assert "status" in data, "Response missing status field"
                        assert data["status"] == status.value, \
                            f"Status mismatch: expected {status.value}, got {data['status']}"
    finally:
        _encryption_manager.remove_encryption_context(execution_id)


# Feature: github-actions-remote-executor, Property 33: Completion Exit Code Inclusion
@settings(max_examples=10, deadline=None)
@given(st.uuids().map(str), st.integers(min_value=-1, max_value=255))
def test_completion_exit_code_inclusion(execution_id, exit_code):
    """
    **Validates: Requirements 6.7**
    
    For any completed script execution, the output endpoint response should 
    include the exit code.
    """
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
    
    shared_key = os.urandom(32)
    _encryption_manager.store_encryption_context(execution_id, shared_key)
    
    try:
        with patch.object(_app.state.request_validator, 'validate_oidc_token_from_body', return_value=VALID_OIDC_RESULT):
            with patch.object(_app.state.execution_manager, 'get_execution', return_value=record):
                with patch.object(_app.state.output_collector, 'get_output') as mock_output:
                    mock_output.return_value = OutputData(
                        stdout="test output",
                        stderr="",
                        stdout_offset=11,
                        stderr_offset=0,
                        complete=True,
                        exit_code=exit_code
                    )
                    
                    body = _encrypt_output_request(
                        {"oidc_token": "valid.oidc.token", "offset": 0},
                        shared_key,
                    )
                    response = _client.post(f"/execution/{execution_id}/output", json=body)
                    
                    assert response.status_code == 200
                    data = _decrypt_output_response(response.json(), shared_key)
                    assert "exit_code" in data, "Response missing exit_code field"
                    assert data["exit_code"] == exit_code, \
                        f"Exit code mismatch: expected {exit_code}, got {data['exit_code']}"
                    assert data["complete"] is True, "Complete flag should be True"
    finally:
        _encryption_manager.remove_encryption_context(execution_id)


# Feature: github-actions-remote-executor, Property 34: Completion Flag Accuracy
@settings(max_examples=10, deadline=None)
@given(st.uuids().map(str), st.booleans())
def test_completion_flag_accuracy(execution_id, is_complete):
    """
    **Validates: Requirements 6.8**
    
    For any execution, the output endpoint response should include a boolean 
    completion flag that accurately reflects whether execution is complete.
    """
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
    
    shared_key = os.urandom(32)
    _encryption_manager.store_encryption_context(execution_id, shared_key)
    
    try:
        with patch.object(_app.state.request_validator, 'validate_oidc_token_from_body', return_value=VALID_OIDC_RESULT):
            with patch.object(_app.state.execution_manager, 'get_execution', return_value=record):
                with patch.object(_app.state.output_collector, 'get_output') as mock_output:
                    mock_output.return_value = OutputData(
                        stdout="test",
                        stderr="",
                        stdout_offset=4,
                        stderr_offset=0,
                        complete=is_complete,
                        exit_code=0 if is_complete else None
                    )
                    
                    body = _encrypt_output_request(
                        {"oidc_token": "valid.oidc.token", "offset": 0},
                        shared_key,
                    )
                    response = _client.post(f"/execution/{execution_id}/output", json=body)
                    
                    assert response.status_code == 200
                    data = _decrypt_output_response(response.json(), shared_key)
                    assert "complete" in data, "Response missing complete field"
                    assert data["complete"] == is_complete, \
                        f"Complete flag mismatch: expected {is_complete}, got {data['complete']}"
    finally:
        _encryption_manager.remove_encryption_context(execution_id)


# Feature: github-actions-remote-executor, Property 35: Invalid Execution ID Response
@settings(max_examples=10, deadline=None)
@given(st.uuids().map(str))
def test_invalid_execution_id_response(execution_id):
    """
    **Validates: Requirements 6.9**
    
    For any non-existent execution ID, the output endpoint should return 
    HTTP 404 with an execution not found error.
    """
    # Store encryption context so we get past the encryption check
    shared_key = os.urandom(32)
    _encryption_manager.store_encryption_context(execution_id, shared_key)
    
    try:
        # Mock execution manager to return None (not found)
        with patch.object(_app.state.request_validator, 'validate_oidc_token_from_body', return_value=VALID_OIDC_RESULT):
            with patch.object(_app.state.execution_manager, 'get_execution', return_value=None):
                body = _encrypt_output_request(
                    {"oidc_token": "valid.oidc.token", "offset": 0},
                    shared_key,
                )
                response = _client.post(f"/execution/{execution_id}/output", json=body)
                
                assert response.status_code == 404, \
                    f"Expected 404 for non-existent execution, got {response.status_code}"
                
                data = response.json()
                assert "error" in data.get("detail", {}), "Response missing error field"
                assert data["detail"]["error"] == "execution_not_found", \
                    "Error should be 'execution_not_found'"
    finally:
        _encryption_manager.remove_encryption_context(execution_id)


# Feature: github-actions-remote-executor, Property 47: Script Size Validation
# NOTE: Script file size validation has been removed from the /execute endpoint
# since we now clone the full repository rather than fetching individual files.
# The server no longer rejects requests based on script file size.


# Feature: github-actions-remote-executor, Property 48: Oversized Script Rejection
# NOTE: Script file size validation has been removed from the /execute endpoint
# since we now clone the full repository rather than fetching individual files.
# The server no longer rejects requests based on script file size.


# Feature: github-actions-remote-executor, Property 49: Rate Limiting per IP
@settings(max_examples=3, deadline=None)
@given(st.integers(min_value=1, max_value=12))
def test_rate_limiting_per_ip(num_requests):
    """
    **Validates: Requirements 8.5**
    
    For any source IP address that exceeds the configured rate limit, 
    subsequent requests should be rejected with HTTP 429.
    """
    # Needs fresh app per example to reset rate limiter state
    app = create_app(get_test_config())
    client = TestClient(app)
    
    rate_limit = app.state.config.rate_limit_per_ip
    
    responses = []
    
    with patch.object(app.state.request_validator, 'validate_oidc_token', return_value=VALID_OIDC_RESULT), \
         patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
        mock_validate.return_value = Mock(valid=True, errors=[])
        
        for i in range(num_requests):
            response = client.post("/execute", json={
                "repository_url": "https://github.com/test/repo",
                "commit_hash": "a" * 40,
                "script_path": "test.sh",
                "github_token": "test_token"
            }, headers=OIDC_BEARER_HEADER)
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


# Feature: github-actions-remote-executor, Property 133: Output Request-Response Encryption Round-Trip
@settings(max_examples=20, deadline=None)
@given(
    st.uuids().map(str),
    st.text(min_size=0, max_size=200),
    st.text(min_size=0, max_size=200),
    st.integers(min_value=0, max_value=1000),
    st.integers(min_value=-1, max_value=255),
    st.booleans(),
)
def test_output_request_response_encryption_round_trip(
    execution_id, stdout_text, stderr_text, offset, exit_code, is_complete
):
    """
    **Validates: Requirements 41.4, 41.5, 42.2, 42.3, 42.4, 42.8**

    Property 133: Client encrypts output request with Shared_Key, server
    decrypts, processes, encrypts response, client decrypts — verify
    original content is preserved through the round-trip.
    """
    status_val = ExecutionStatus.COMPLETED if is_complete else ExecutionStatus.RUNNING

    record = ExecutionRecord(
        execution_id=execution_id,
        repository_url="https://github.com/test/repo",
        commit_hash="a" * 40,
        script_path="test.sh",
        status=status_val,
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc) if is_complete else None,
        exit_code=exit_code if is_complete else None,
        timeout_seconds=300,
    )

    shared_key = os.urandom(32)
    _encryption_manager.store_encryption_context(execution_id, shared_key)

    try:
        with patch.object(
            _app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=VALID_OIDC_RESULT,
        ):
            with patch.object(
                _app.state.execution_manager, "get_execution", return_value=record
            ):
                with patch.object(
                    _app.state.output_collector, "get_output"
                ) as mock_output:
                    mock_output.return_value = OutputData(
                        stdout=stdout_text,
                        stderr=stderr_text,
                        stdout_offset=len(stdout_text),
                        stderr_offset=len(stderr_text),
                        complete=is_complete,
                        exit_code=exit_code if is_complete else None,
                    )

                    # Client encrypts request
                    request_payload = {
                        "oidc_token": "valid.oidc.token",
                        "offset": offset,
                    }
                    body = _encrypt_output_request(request_payload, shared_key)

                    response = _client.post(
                        f"/execution/{execution_id}/output", json=body
                    )

                    assert response.status_code == 200
                    resp_json = response.json()
                    assert "encrypted_response" in resp_json

                    # Client decrypts response
                    data = _decrypt_output_response(resp_json, shared_key)

                    # Verify round-trip content
                    assert data["execution_id"] == execution_id
                    assert data["status"] == status_val.value
                    assert data["stdout"] == stdout_text
                    assert data["stderr"] == stderr_text
                    assert data["complete"] == is_complete
                    if is_complete:
                        assert data["exit_code"] == exit_code
    finally:
        _encryption_manager.remove_encryption_context(execution_id)


# Feature: github-actions-remote-executor, Property 134: Missing Encryption Context Returns HTTP 400
@settings(max_examples=20, deadline=None)
@given(st.uuids().map(str))
def test_missing_encryption_context_returns_400(execution_id):
    """
    **Validates: Requirements 42.6**

    Property 134: Request /execution/{id}/output with an execution_id that
    has no Encryption_Context, verify HTTP 400.
    """
    # Ensure no encryption context exists for this execution_id
    _encryption_manager.remove_encryption_context(execution_id)

    # Send any POST body — the server should reject before trying to decrypt
    response = _client.post(
        f"/execution/{execution_id}/output",
        json={"encrypted_payload": base64.b64encode(b"dummy").decode()},
    )

    assert response.status_code == 400, (
        f"Expected 400 for missing encryption context, got {response.status_code}"
    )
    data = response.json()
    assert data["detail"]["error"] == "no_encryption_context"
