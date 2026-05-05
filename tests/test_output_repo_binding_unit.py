"""Unit tests for execution output authentication via Shared_Key possession.

The /execution/{id}/output endpoint authenticates callers solely by verifying
that they possess the execution-bound Shared_Key (derived during the PQ_Hybrid_KEM
exchange on /execute). No separate OIDC token validation is required.

Requirements: 42.2, 42.3, 42.6, 42.7
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.models import (
    ExecutionRecord,
    ExecutionStatus,
    OutputData,
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


def _create_client():
    ctx = EncryptionTestContext()
    app = create_app(_get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)
    return ctx, app, client


class TestOutputSharedKeyAuth:
    """Tests that Shared_Key possession is the sole authentication for /output."""

    def test_valid_shared_key_allows_output(self):
        """Request with valid Shared_Key (no OIDC token) → 200"""
        ctx, app, client = _create_client()
        execution_id = "test-shared-key-auth"
        ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="scripts/test.sh",
            status=ExecutionStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            exit_code=0,
            timeout_seconds=300,
            repository="owner/repo",
        )

        output_data = OutputData(
            stdout="done\n",
            stderr="",
            stdout_offset=5,
            stderr_offset=0,
            complete=True,
            exit_code=0,
        )

        with patch.object(
            app.state.execution_manager, "get_execution", return_value=record
        ), patch.object(
            app.state.output_collector, "get_output", return_value=output_data
        ), patch.object(
            app.state.attestation_generator,
            "generate_output_attestation",
            return_value=(b"attest", None),
        ):
            # No oidc_token in the payload — Shared_Key is the auth
            body = make_encrypted_output_request(
                {"offset": 0}, ctx.shared_key
            )
            response = client.post(f"/execution/{execution_id}/output", json=body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert data["execution_id"] == execution_id
        assert data["stdout"] == "done\n"

    def test_no_encryption_context_returns_400(self):
        """Request for execution_id with no stored Shared_Key → 400"""
        ctx, app, client = _create_client()
        execution_id = "test-no-context"

        # Create execution record but do NOT store encryption context
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
            body = make_encrypted_output_request(
                {"offset": 0}, ctx.shared_key
            )
            response = client.post(f"/execution/{execution_id}/output", json=body)

        assert response.status_code == 400
        detail = response.json().get("detail", {})
        assert detail.get("error") == "no_encryption_context"

    def test_wrong_shared_key_returns_400(self):
        """Request encrypted with wrong key → decryption fails → 400"""
        ctx, app, client = _create_client()
        execution_id = "test-wrong-key"
        ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

        # Create a different shared key
        wrong_key = b"\x00" * 32

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
            # Encrypt with wrong key — server will fail to decrypt
            body = make_encrypted_output_request(
                {"offset": 0}, wrong_key
            )
            response = client.post(f"/execution/{execution_id}/output", json=body)

        assert response.status_code == 400
        detail = response.json().get("detail", {})
        assert detail.get("error") == "decryption_failed"

    def test_nonexistent_execution_returns_404(self):
        """Request for execution_id that doesn't exist → 404"""
        ctx, app, client = _create_client()
        execution_id = "nonexistent-id"

        body = make_encrypted_output_request(
            {"offset": 0}, ctx.shared_key
        )
        response = client.post(f"/execution/{execution_id}/output", json=body)

        assert response.status_code == 404
        detail = response.json().get("detail", {})
        assert detail.get("error") == "execution_not_found"
