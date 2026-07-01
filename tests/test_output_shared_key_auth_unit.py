"""Unit tests for output Shared_Key authentication

Feature: github-actions-remote-executor
Task: 136.4

Tests that the /execution/{id}/output endpoint authenticates callers solely
by verifying possession of the execution-bound Shared_Key. No OIDC token
is required or validated on this endpoint.

Requirements: 2.2, 6.3, 42.6, 42.7
"""
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.models import ExecutionRecord, ExecutionStatus, OutputData, OutputAttestationResult
from src.server import create_app
from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_output_request,
    decrypt_output_response,
)


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
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )


def _setup():
    """Create a fresh test client, app, and encryption context."""
    ctx = EncryptionTestContext()
    app = create_app(_get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)
    return ctx, app, client


def _make_record(execution_id: str, status: ExecutionStatus, exit_code=None):
    """Build an ExecutionRecord for testing."""
    now = datetime.now(timezone.utc)
    return ExecutionRecord(
        execution_id=execution_id,
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="scripts/test.sh",
        status=status,
        created_at=now,
        started_at=now,
        completed_at=now if exit_code is not None else None,
        exit_code=exit_code,
        timeout_seconds=300,
        repository="owner/repo",
    )


class TestOutputValidSharedKey:
    """Test /output with valid Shared_Key (success, no OIDC token needed)."""

    def test_valid_shared_key_returns_200(self):
        """A request encrypted with the correct Shared_Key returns 200."""
        ctx, app, client = _setup()
        eid = "valid-key-test-1"
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)

        record = _make_record(eid, ExecutionStatus.COMPLETED, exit_code=0)
        output = OutputData(
            stdout="hello world",
            stderr="",
            stdout_offset=11,
            stderr_offset=0,
            complete=True,
            exit_code=0,
        )

        with patch.object(app.state.execution_manager, "get_execution", return_value=record), \
             patch.object(app.state.output_collector, "get_output", return_value=output), \
             patch.object(
                 app.state.attestation_generator,
                 "generate_output_attestation",
                 return_value=(OutputAttestationResult(signature=b"attestation-bytes", claims_raw="e30="), None),
             ):
            req_body = make_encrypted_output_request({"offset": 0}, ctx.shared_key)
            response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert data["execution_id"] == eid
        assert data["stdout"] == "hello world"
        assert data["exit_code"] == 0

    def test_no_oidc_token_in_payload_still_succeeds(self):
        """The output endpoint does not require an oidc_token field."""
        ctx, app, client = _setup()
        eid = "no-oidc-test"
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)

        record = _make_record(eid, ExecutionStatus.RUNNING)
        output = OutputData(
            stdout="partial",
            stderr="",
            stdout_offset=7,
            stderr_offset=0,
            complete=False,
            exit_code=None,
        )

        with patch.object(app.state.execution_manager, "get_execution", return_value=record), \
             patch.object(app.state.output_collector, "get_output", return_value=output), \
             patch.object(
                 app.state.attestation_generator,
                 "generate_output_attestation",
                 return_value=(OutputAttestationResult(signature=b"attestation-bytes", claims_raw="e30="), None),
             ):
            # Payload has no oidc_token field at all
            req_body = make_encrypted_output_request({"offset": 0}, ctx.shared_key)
            response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert data["status"] == "running"

    def test_arbitrary_oidc_token_in_payload_is_ignored(self):
        """An arbitrary oidc_token in the payload is ignored (not validated)."""
        ctx, app, client = _setup()
        eid = "ignored-oidc-test"
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)

        record = _make_record(eid, ExecutionStatus.COMPLETED, exit_code=42)
        output = OutputData(
            stdout="done",
            stderr="warn",
            stdout_offset=4,
            stderr_offset=4,
            complete=True,
            exit_code=42,
        )

        with patch.object(app.state.execution_manager, "get_execution", return_value=record), \
             patch.object(app.state.output_collector, "get_output", return_value=output), \
             patch.object(
                 app.state.attestation_generator,
                 "generate_output_attestation",
                 return_value=(OutputAttestationResult(signature=b"attestation-bytes", claims_raw="e30="), None),
             ):
            # Include a completely invalid oidc_token — should be ignored
            req_body = make_encrypted_output_request(
                {"offset": 0, "oidc_token": "totally-invalid-jwt-garbage"},
                ctx.shared_key,
            )
            response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert data["execution_id"] == eid
        assert data["exit_code"] == 42

    def test_valid_shared_key_with_offset(self):
        """Valid Shared_Key with an offset parameter returns output from that offset."""
        ctx, app, client = _setup()
        eid = "offset-test"
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)

        record = _make_record(eid, ExecutionStatus.COMPLETED, exit_code=0)
        output = OutputData(
            stdout="remaining output",
            stderr="",
            stdout_offset=50,
            stderr_offset=0,
            complete=True,
            exit_code=0,
        )

        with patch.object(app.state.execution_manager, "get_execution", return_value=record), \
             patch.object(app.state.output_collector, "get_output", return_value=output), \
             patch.object(
                 app.state.attestation_generator,
                 "generate_output_attestation",
                 return_value=(OutputAttestationResult(signature=b"attestation-bytes", claims_raw="e30="), None),
             ):
            req_body = make_encrypted_output_request({"offset": 34}, ctx.shared_key)
            response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert data["stdout"] == "remaining output"


class TestOutputInvalidSharedKey:
    """Test /output with invalid Shared_Key (400 decryption failure)."""

    def test_wrong_shared_key_returns_400(self):
        """A request encrypted with the wrong key returns 400 decryption_failed."""
        ctx, app, client = _setup()
        eid = "wrong-key-test"
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)

        record = _make_record(eid, ExecutionStatus.COMPLETED, exit_code=0)

        with patch.object(app.state.execution_manager, "get_execution", return_value=record):
            # Encrypt with a completely different key
            wrong_key = os.urandom(32)
            req_body = make_encrypted_output_request({"offset": 0}, wrong_key)
            response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 400
        detail = response.json().get("detail", {})
        assert detail.get("error") == "decryption_failed"

    def test_corrupted_ciphertext_returns_400(self):
        """A request with corrupted ciphertext returns 400 decryption_failed."""
        import base64

        ctx, app, client = _setup()
        eid = "corrupted-test"
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)

        record = _make_record(eid, ExecutionStatus.COMPLETED, exit_code=0)

        with patch.object(app.state.execution_manager, "get_execution", return_value=record):
            # Create a valid-looking but corrupted payload
            corrupted_payload = base64.b64encode(os.urandom(64)).decode()
            req_body = {"encrypted_payload": corrupted_payload}
            response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 400
        detail = response.json().get("detail", {})
        assert detail.get("error") == "decryption_failed"

    def test_empty_encrypted_payload_returns_400(self):
        """An empty encrypted_payload returns 400."""
        import base64

        ctx, app, client = _setup()
        eid = "empty-payload-test"
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)

        record = _make_record(eid, ExecutionStatus.COMPLETED, exit_code=0)

        with patch.object(app.state.execution_manager, "get_execution", return_value=record):
            # Empty payload (too short for nonce + ciphertext)
            req_body = {"encrypted_payload": base64.b64encode(b"short").decode()}
            response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 400
        detail = response.json().get("detail", {})
        assert detail.get("error") == "decryption_failed"


class TestOutputNoEncryptionContext:
    """Test /output with no Encryption_Context (400)."""

    def test_no_encryption_context_returns_400(self):
        """When no Encryption_Context exists for the execution_id, returns 400."""
        ctx, app, client = _setup()
        eid = "no-context-test"

        # Do NOT store any encryption context for this execution_id
        # But the execution record exists
        record = _make_record(eid, ExecutionStatus.COMPLETED, exit_code=0)

        with patch.object(app.state.execution_manager, "get_execution", return_value=record):
            req_body = make_encrypted_output_request({"offset": 0}, ctx.shared_key)
            response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 400
        detail = response.json().get("detail", {})
        assert detail.get("error") == "no_encryption_context"

    def test_removed_encryption_context_returns_400(self):
        """After Encryption_Context is removed (cleanup), returns 400."""
        ctx, app, client = _setup()
        eid = "removed-context-test"

        # Store and then remove the encryption context
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)
        ctx.encryption_manager.remove_encryption_context(eid)

        record = _make_record(eid, ExecutionStatus.COMPLETED, exit_code=0)

        with patch.object(app.state.execution_manager, "get_execution", return_value=record):
            req_body = make_encrypted_output_request({"offset": 0}, ctx.shared_key)
            response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 400
        detail = response.json().get("detail", {})
        assert detail.get("error") == "no_encryption_context"

    def test_nonexistent_execution_with_no_context_returns_404(self):
        """When both execution record and Encryption_Context are missing, returns 404."""
        ctx, app, client = _setup()
        eid = "nonexistent-test"

        # No encryption context AND no execution record
        with patch.object(app.state.execution_manager, "get_execution", return_value=None):
            req_body = make_encrypted_output_request({"offset": 0}, ctx.shared_key)
            response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 404
        detail = response.json().get("detail", {})
        assert detail.get("error") == "execution_not_found"
