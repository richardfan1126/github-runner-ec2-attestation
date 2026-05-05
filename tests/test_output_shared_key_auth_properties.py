"""Property-based tests for Execution Output Shared Key Authentication

Feature: github-actions-remote-executor
Tests Property 147 from the design document

The /execution/{id}/output endpoint authenticates callers solely by verifying
that they possess the execution-bound Shared_Key (derived during the PQ_Hybrid_KEM
exchange on /execute). No separate OIDC token validation is required or performed.

Validates: Requirements 2.2, 6.3
"""
import os
from datetime import datetime, timezone
from unittest.mock import patch

from hypothesis import given, strategies as st, settings
from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.models import ExecutionRecord, ExecutionStatus, OutputData
from src.server import create_app
from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_output_request,
    decrypt_output_response,
)


# ---------------------------------------------------------------------------
# Test configuration
# ---------------------------------------------------------------------------

ALLOWED_REPOS = ["owner/repo"]
EXPECTED_AUDIENCE = "https://example.com"


def _get_test_config() -> ServerConfig:
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
        allowed_repositories=ALLOWED_REPOS,
        expected_audience=EXPECTED_AUDIENCE,
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Arbitrary stdout/stderr content
_stdout_strategy = st.text(min_size=0, max_size=200)
_stderr_strategy = st.text(min_size=0, max_size=200)
_exit_code_strategy = st.integers(min_value=0, max_value=255)
_offset_strategy = st.integers(min_value=0, max_value=100)

# Arbitrary OIDC-like token strings (should be ignored by the output endpoint)
_arbitrary_oidc_token = st.text(min_size=1, max_size=500)

# Execution statuses that can appear on the output endpoint
_execution_status_strategy = st.sampled_from([
    ExecutionStatus.RUNNING,
    ExecutionStatus.COMPLETED,
    ExecutionStatus.FAILED,
    ExecutionStatus.TIMED_OUT,
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_test_env():
    """Create a fresh test client, app, and encryption context."""
    ctx = EncryptionTestContext()
    app = create_app(_get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)
    return ctx, app, client


def _build_record(execution_id: str, exec_status: ExecutionStatus, exit_code: int):
    """Build an ExecutionRecord for the given status."""
    now = datetime.now(timezone.utc)
    effective_exit_code = None
    completed_at = None

    if exec_status == ExecutionStatus.COMPLETED:
        effective_exit_code = exit_code
        completed_at = now
    elif exec_status == ExecutionStatus.FAILED:
        effective_exit_code = exit_code if exit_code != 0 else 1
        completed_at = now
    elif exec_status == ExecutionStatus.TIMED_OUT:
        completed_at = now

    return ExecutionRecord(
        execution_id=execution_id,
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="scripts/test.sh",
        status=exec_status,
        created_at=now,
        started_at=now,
        completed_at=completed_at,
        exit_code=effective_exit_code,
        timeout_seconds=300,
        repository="owner/repo",
    )


def _build_output(stdout: str, stderr: str, exit_code: int, complete: bool):
    """Build an OutputData instance."""
    return OutputData(
        stdout=stdout,
        stderr=stderr,
        stdout_offset=len(stdout),
        stderr_offset=len(stderr),
        complete=complete,
        exit_code=exit_code if complete else None,
    )


# ===========================================================================
# Property 147: Execution Output Shared Key Authentication
# ===========================================================================

@settings(max_examples=30, deadline=None)
@given(
    stdout=_stdout_strategy,
    stderr=_stderr_strategy,
    exit_code=_exit_code_strategy,
    offset=_offset_strategy,
    exec_status=_execution_status_strategy,
)
def test_property_147_output_shared_key_auth_no_oidc_required(
    stdout, stderr, exit_code, offset, exec_status
):
    """
    Property 147: For any request to /execution/{id}/output that is encrypted
    with the correct execution-bound Shared_Key, the endpoint SHALL return
    HTTP 200 with execution output WITHOUT requiring an oidc_token field in
    the decrypted request body.

    This verifies that Shared_Key possession alone is sufficient authentication
    for the output endpoint — no OIDC token is required or validated.

    **Validates: Requirements 2.2, 6.3**
    """
    ctx, app, client = _create_test_env()
    execution_id = f"prop147-{os.urandom(4).hex()}"

    # Store encryption context (simulates what /execute would do)
    ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

    complete = exec_status != ExecutionStatus.RUNNING
    record = _build_record(execution_id, exec_status, exit_code)
    output_data = _build_output(stdout, stderr, exit_code, complete)

    with patch.object(
        app.state.execution_manager, "get_execution", return_value=record
    ), patch.object(
        app.state.output_collector, "get_output", return_value=output_data
    ), patch.object(
        app.state.attestation_generator,
        "generate_output_attestation",
        return_value=(b"attestation-bytes", None),
    ):
        # Send request WITHOUT oidc_token — only offset in the payload
        req_body = make_encrypted_output_request(
            {"offset": offset}, ctx.shared_key
        )
        response = client.post(f"/execution/{execution_id}/output", json=req_body)

    assert response.status_code == 200, (
        f"Output endpoint should return 200 with valid Shared_Key (no OIDC token), "
        f"got {response.status_code} for status={exec_status.value}"
    )

    data = decrypt_output_response(response.json(), ctx.shared_key)
    assert data["execution_id"] == execution_id
    assert data["status"] == exec_status.value


@settings(max_examples=30, deadline=None)
@given(
    oidc_token=_arbitrary_oidc_token,
    stdout=_stdout_strategy,
    stderr=_stderr_strategy,
    exit_code=_exit_code_strategy,
    exec_status=_execution_status_strategy,
)
def test_property_147_output_ignores_oidc_token_in_payload(
    oidc_token, stdout, stderr, exit_code, exec_status
):
    """
    Property 147 (corollary): For any arbitrary string passed as oidc_token
    in the decrypted request body of /execution/{id}/output, the endpoint
    SHALL NOT validate or reject based on that token. The oidc_token field
    is simply ignored — authentication is solely via Shared_Key possession.

    **Validates: Requirements 2.2, 6.3**
    """
    ctx, app, client = _create_test_env()
    execution_id = f"prop147-oidc-{os.urandom(4).hex()}"

    ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

    complete = exec_status != ExecutionStatus.RUNNING
    record = _build_record(execution_id, exec_status, exit_code)
    output_data = _build_output(stdout, stderr, exit_code, complete)

    with patch.object(
        app.state.execution_manager, "get_execution", return_value=record
    ), patch.object(
        app.state.output_collector, "get_output", return_value=output_data
    ), patch.object(
        app.state.attestation_generator,
        "generate_output_attestation",
        return_value=(b"attestation-bytes", None),
    ):
        # Include an arbitrary oidc_token — it should be ignored
        req_body = make_encrypted_output_request(
            {"offset": 0, "oidc_token": oidc_token}, ctx.shared_key
        )
        response = client.post(f"/execution/{execution_id}/output", json=req_body)

    assert response.status_code == 200, (
        f"Output endpoint should return 200 regardless of oidc_token content, "
        f"got {response.status_code} with oidc_token={oidc_token!r:.50}"
    )

    data = decrypt_output_response(response.json(), ctx.shared_key)
    assert data["execution_id"] == execution_id
    assert data["stdout"] == stdout
    assert data["stderr"] == stderr


@settings(max_examples=30, deadline=None)
@given(
    stdout=_stdout_strategy,
    stderr=_stderr_strategy,
    exit_code=_exit_code_strategy,
)
def test_property_147_wrong_shared_key_rejected(stdout, stderr, exit_code):
    """
    Property 147 (negative): For any request to /execution/{id}/output
    encrypted with a Shared_Key that does NOT match the execution-bound key,
    the endpoint SHALL reject with HTTP 400 (decryption failure), proving
    that Shared_Key possession is the authentication mechanism.

    **Validates: Requirements 2.2, 6.3**
    """
    ctx, app, client = _create_test_env()
    execution_id = f"prop147-wrong-{os.urandom(4).hex()}"

    # Store the correct shared key
    ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

    record = _build_record(execution_id, ExecutionStatus.COMPLETED, exit_code)

    with patch.object(
        app.state.execution_manager, "get_execution", return_value=record
    ):
        # Encrypt with a WRONG key — server cannot decrypt
        wrong_key = os.urandom(32)
        req_body = make_encrypted_output_request(
            {"offset": 0}, wrong_key
        )
        response = client.post(f"/execution/{execution_id}/output", json=req_body)

    assert response.status_code == 400, (
        f"Output endpoint should return 400 when Shared_Key is wrong, "
        f"got {response.status_code}"
    )
    detail = response.json().get("detail", {})
    assert detail.get("error") == "decryption_failed"
