"""Property-based tests for nonce passthrough in attestation-producing endpoints.

Feature: github-actions-remote-executor
Tests Property 126 from the design document.
"""
import base64
from datetime import datetime, timezone
from unittest.mock import Mock, patch, call

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st, settings

from src.server import create_app
from src.config import ServerConfig
from src.encryption import EncryptionManager
from src.models import (
    AttestationDocument,
    CloneResult,
    ExecutionRecord,
    ExecutionStatus,
    OIDCValidationResult,
    OutputData,
)
from src.attestation import AttestationGenerator


from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_execute_request,
    decrypt_execute_response,
    make_encrypted_output_request,
    decrypt_output_response,
)


VALID_OIDC_RESULT = OIDCValidationResult(
    valid=True,
    status_code=200,
    error_message=None,
    claims={
        "repository": "owner/repo",
        "iss": "https://token.actions.githubusercontent.com",
        "aud": "https://example.com",
    },
)

OIDC_BEARER_HEADER = {"Authorization": "Bearer valid.oidc.token"}


def get_test_config():
    return ServerConfig(
        port=8000,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=100000,
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


def _make_attestation_doc(signature: bytes = b"test_signature") -> AttestationDocument:
    return AttestationDocument(
        repository_url="",
        commit_hash="",
        script_path="",
        timestamp=datetime.now(timezone.utc),
        signature=signature,
    )


# Nonce strategy: printable ASCII strings or None
nonce_strategy = st.one_of(st.none(), st.text(min_size=1, max_size=64))


# ---------------------------------------------------------------------------
# Property 126: Nonce Passthrough in Attestation
# ---------------------------------------------------------------------------


# Feature: github-actions-remote-executor, Property 126: Nonce Passthrough in Attestation – /attest
@settings(max_examples=100, deadline=None)
@given(nonce=nonce_strategy)
def test_nonce_passthrough_attest_endpoint(nonce):
    """
    **Validates: Requirements 37.5, 38.6**

    For random nonce values on /attest, verify the nonce is passed to
    generate_attestation and would be included in the attestation document.
    """
    encryption_manager = EncryptionManager()
    app = create_app(get_test_config(), encryption_manager=encryption_manager)
    client = TestClient(app)

    with patch.object(
        app.state.attestation_generator, "generate_attestation"
    ) as mock_attest:
        mock_attest.return_value = (_make_attestation_doc(), None)

        params = {}
        if nonce is not None:
            params["nonce"] = nonce

        response = client.get("/attest", params=params)
        assert response.status_code == 200

        mock_attest.assert_called_once()
        call_kwargs = mock_attest.call_args
        assert call_kwargs.kwargs.get("nonce") == nonce, (
            f"Expected nonce={nonce!r} but got {call_kwargs.kwargs.get('nonce')!r}"
        )


# Feature: github-actions-remote-executor, Property 126: Nonce Passthrough in Attestation – /execute
@settings(max_examples=100, deadline=None)
@given(nonce=st.text(min_size=1, max_size=64).filter(lambda s: s.strip()))
def test_nonce_passthrough_execute_endpoint(nonce):
    """
    **Validates: Requirements 38.2, 38.4**

    For random nonce values on /execute, verify the nonce from the request
    body is passed to generate_attestation.
    
    Note: nonce is mandatory on /execute (Req 45.6), so None is not tested here.
    """
    ctx = EncryptionTestContext()
    app = create_app(get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)

    commit = "a" * 40
    req_data = {
        "repository_url": "https://github.com/owner/repo",
        "commit_hash": commit,
        "script_path": "run.sh",
        "github_token": "ghp_testtoken1234567890",
        "oidc_token": "valid.oidc.token",
        "nonce": nonce,
    }

    with patch.object(
        app.state.request_validator, "validate_oidc_token_from_body", return_value=VALID_OIDC_RESULT
    ), patch.object(
        app.state.request_validator, "validate_execution_request",
        return_value=Mock(valid=True, errors=[]),
    ), patch.object(
        app.state.repository_client, "authenticate",
        return_value=Mock(success=True, error_message=None),
    ), patch.object(
        app.state.repository_client, "clone_repo",
        return_value=CloneResult(clone_path="/tmp/clone", script_path="run.sh"),
    ), patch.object(
        app.state.repository_client, "validate_script_exists", return_value=True
    ), patch.object(
        app.state.attestation_generator, "generate_attestation"
    ) as mock_attest, patch.object(
        app.state.script_executor, "execute_async"
    ), patch(
        "src.server.os.path.getsize", return_value=100
    ):
        mock_attest.return_value = (_make_attestation_doc(), None)

        body = make_encrypted_execute_request(req_data, ctx)
        response = client.post("/execute", json=body)
        assert response.status_code == 200, (
            f"Expected 200 but got {response.status_code}: {response.text}"
        )

        mock_attest.assert_called_once()
        call_kwargs = mock_attest.call_args

        # nonce should be passed as keyword arg; if not in body it should be None
        expected_nonce = nonce
        actual_nonce = call_kwargs.kwargs.get("nonce")
        if actual_nonce is None:
            # Also check positional — nonce is the 4th positional arg
            args = call_kwargs.args
            actual_nonce = args[3] if len(args) > 3 else None
        assert actual_nonce == expected_nonce, (
            f"Expected nonce={expected_nonce!r} but got {actual_nonce!r}"
        )


# Feature: github-actions-remote-executor, Property 126: Nonce Passthrough in Attestation – /output
@settings(max_examples=100, deadline=None)
@given(nonce=st.text(min_size=1, max_size=64).filter(lambda s: s.strip()))
def test_nonce_passthrough_output_endpoint(nonce):
    """
    **Validates: Requirements 38.3, 38.4**

    For random nonce values on /execution/{id}/output, verify the nonce
    is passed to generate_output_attestation when generating the
    Output_Attestation_Document.
    
    Note: nonce is mandatory on /output (Req 45.6), so None is not tested here.
    """
    ctx = EncryptionTestContext()
    app = create_app(get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)

    exec_id = "test-exec-id-001"
    ctx.encryption_manager.store_encryption_context(exec_id, ctx.shared_key)

    # Create a completed execution record
    exec_record = ExecutionRecord(
        execution_id=exec_id,
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="run.sh",
        status=ExecutionStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        exit_code=0,
        timeout_seconds=300,
        repository="owner/repo",
    )

    completed_output = OutputData(
        stdout="hello world",
        stderr="",
        stdout_offset=11,
        stderr_offset=0,
        complete=True,
        exit_code=0,
    )

    with patch.object(
        app.state.request_validator, "validate_oidc_token_from_body", return_value=VALID_OIDC_RESULT
    ), patch.object(
        app.state.execution_manager, "get_execution", return_value=exec_record
    ), patch.object(
        app.state.output_collector, "get_output", return_value=completed_output
    ), patch.object(
        app.state.attestation_generator, "generate_output_attestation"
    ) as mock_output_attest:
        mock_output_attest.return_value = (b"output_attestation_bytes", None)

        payload = {"oidc_token": "valid.oidc.token", "offset": 0, "nonce": nonce}

        req_body = make_encrypted_output_request(payload, ctx.shared_key)
        response = client.post(
            f"/execution/{exec_id}/output",
            json=req_body,
        )
        assert response.status_code == 200, (
            f"Expected 200 but got {response.status_code}: {response.text}"
        )

        mock_output_attest.assert_called_once()
        call_kwargs = mock_output_attest.call_args
        actual_nonce = call_kwargs.kwargs.get("nonce")
        assert actual_nonce == nonce, (
            f"Expected nonce={nonce!r} but got {actual_nonce!r}"
        )
