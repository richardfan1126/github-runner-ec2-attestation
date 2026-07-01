"""Property-based tests for execution output Shared_Key authentication.

The /execution/{id}/output endpoint authenticates callers solely by verifying
that they possess the execution-bound Shared_Key (derived during the PQ_Hybrid_KEM
exchange on /execute). No separate OIDC token validation is required.

Property 147 (revised): For any /execution/{id}/output request where the caller
does NOT possess the correct Shared_Key, the server should reject the request
with HTTP 400 (decryption failure or no encryption context).

**Validates: Requirements 42.2, 42.3, 42.6, 42.7**
"""
from datetime import datetime, timezone
from unittest.mock import patch

from hypothesis import given, strategies as st, settings

from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.models import (
    ExecutionRecord,
    ExecutionStatus,
    OutputData,
    OutputAttestationResult,
)
from src.server import create_app
from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_output_request,
    decrypt_output_response,
)

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

_offset = st.integers(min_value=0, max_value=10000)


# ===========================================================================
# Property 147 (revised): Output Endpoint Shared_Key Authentication
# ===========================================================================


@settings(max_examples=30, deadline=None)
@given(offset=_offset)
def test_property_147_output_shared_key_auth_succeeds(offset):
    """
    For any /execution/{id}/output request where the caller possesses the
    correct Shared_Key, the server should return HTTP 200 with output data
    (no OIDC token required).

    **Validates: Requirements 42.2, 42.3, 42.6, 42.7**
    """
    ctx = EncryptionTestContext()
    app = create_app(_get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)

    execution_id = "test-prop-147-success"
    ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

    record = ExecutionRecord(
        execution_id=execution_id,
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="scripts/test.sh",
        status=ExecutionStatus.RUNNING,
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        exit_code=None,
        timeout_seconds=300,
        repository="owner/repo",
    )

    output_data = OutputData(
        stdout="output\n",
        stderr="",
        stdout_offset=7,
        stderr_offset=0,
        complete=False,
        exit_code=None,
    )

    with patch.object(
        app.state.execution_manager, "get_execution", return_value=record
    ), patch.object(
        app.state.output_collector, "get_output", return_value=output_data
    ), patch.object(
        app.state.attestation_generator,
        "generate_output_attestation",
        return_value=(OutputAttestationResult(signature=b"attest", claims_raw="e30="), None),
    ):
        # No oidc_token — Shared_Key is the sole auth mechanism
        body = make_encrypted_output_request(
            {"offset": offset}, ctx.shared_key
        )
        response = client.post(f"/execution/{execution_id}/output", json=body)

    assert response.status_code == 200, (
        f"Valid Shared_Key should yield 200, got {response.status_code}"
    )
    data = decrypt_output_response(response.json(), ctx.shared_key)
    assert data["execution_id"] == execution_id


@settings(max_examples=30, deadline=None)
@given(offset=_offset)
def test_property_147_output_wrong_shared_key_rejected(offset):
    """
    For any /execution/{id}/output request where the caller does NOT possess
    the correct Shared_Key, the server should reject with HTTP 400
    (decryption failure).

    **Validates: Requirements 42.2, 42.3, 42.6, 42.7**
    """
    ctx = EncryptionTestContext()
    app = create_app(_get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)

    execution_id = "test-prop-147-wrong-key"
    ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

    record = ExecutionRecord(
        execution_id=execution_id,
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="scripts/test.sh",
        status=ExecutionStatus.RUNNING,
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        exit_code=None,
        timeout_seconds=300,
        repository="owner/repo",
    )

    with patch.object(
        app.state.execution_manager, "get_execution", return_value=record
    ):
        # Encrypt with a WRONG key — server should fail to decrypt
        wrong_key = b"\x00" * 32
        body = make_encrypted_output_request(
            {"offset": offset}, wrong_key
        )
        response = client.post(f"/execution/{execution_id}/output", json=body)

    assert response.status_code == 400, (
        f"Wrong Shared_Key should yield 400, got {response.status_code}"
    )
    detail = response.json().get("detail", {})
    assert detail.get("error") == "decryption_failed"
