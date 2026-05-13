"""Integration tests for HPKE encrypted communication.

Tests the complete encrypted request/response flow through the
/attest, /execute, and /execution/{id}/output endpoints.
"""
import os
import time
import tempfile
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.models import ExecutionStatus, OIDCValidationResult
from src.server import create_app
from tests.mock_docker import create_mock_docker_client
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


@pytest.fixture
def temp_dir():
    """Create temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def test_config(temp_dir):
    """Create test configuration."""
    return ServerConfig(
        port=8080,
        max_concurrent_executions=10,
        execution_timeout_seconds=5,
        max_script_size_bytes=1024 * 1024,
        rate_limit_per_ip=100,
        rate_limit_window_seconds=60,
        temp_storage_path=temp_dir,
        output_retention_hours=1,
        tpm_attest_path="/usr/bin/nitro-tpm-attest",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )


@pytest.fixture
def mock_github_and_attestation():
    """Mock both GitHub API and attestation generation."""
    with patch("requests.Session") as mock_session_class, \
         patch("src.repository.subprocess.run") as mock_git_run, \
         patch("src.attestation.subprocess.run") as mock_attest:

        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_session.headers = {}
        mock_session.get.return_value = Mock(status_code=200)

        mock_git_run.return_value = Mock(returncode=0, stdout="", stderr="")

        mock_attest.return_value = Mock(
            returncode=0,
            stdout=b"mock_attestation_cbor_data",
        )

        yield {
            "session": mock_session,
            "git_run": mock_git_run,
            "attestation": mock_attest,
        }


@pytest.fixture
def encryption_ctx():
    """Create encryption test context."""
    return EncryptionTestContext()


@pytest.fixture
def app(test_config, mock_github_and_attestation, temp_dir, encryption_ctx):
    """Create test application with OIDC validation mocked."""
    application = create_app(
        test_config,
        docker_client=create_mock_docker_client(),
        encryption_manager=encryption_ctx.encryption_manager,
    )
    application.state.request_validator.validate_oidc_token_from_body = Mock(
        return_value=VALID_OIDC_RESULT
    )

    from src.models import CloneResult

    def mock_clone_repo(repo_url, commit, token):
        clone_dir = tempfile.mkdtemp(dir=temp_dir, prefix="clone_")
        return CloneResult(clone_path=clone_dir, script_path="")

    def mock_validate_script_exists(clone_path, script_path):
        full_path = os.path.join(clone_path, script_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write('#!/bin/bash\necho "Test output"\nexit 0')
        os.chmod(full_path, 0o755)
        return True

    application.state.repository_client.clone_repo = Mock(side_effect=mock_clone_repo)
    application.state.repository_client.validate_script_exists = Mock(
        side_effect=mock_validate_script_exists
    )

    return application


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_execute(client, encryption_ctx, request_data):
    body = make_encrypted_execute_request(request_data, encryption_ctx)
    return client.post("/execute", json=body)


def _post_output(client, execution_id, shared_key, offset=0):
    """Helper to post an encrypted output request.
    
    Authentication is via Shared_Key possession — no OIDC token needed.
    """
    payload = {"offset": offset}
    body = make_encrypted_output_request(payload, shared_key)
    return client.post(f"/execution/{execution_id}/output", json=body)


# ---------------------------------------------------------------------------
# 103.1 – End-to-end encrypted execution flow
# ---------------------------------------------------------------------------

class TestEndToEndEncryptedFlow:
    """Test complete HPKE encrypted communication flow."""

    def test_attest_execute_output_flow(
        self, client, mock_github_and_attestation, encryption_ctx
    ):
        """End-to-end: GET /attest → encrypted POST /execute → encrypted POST /output."""

        # 1. GET /attest – retrieve attestation document with Server_Public_Key
        attest_resp = client.get("/attest")
        assert attest_resp.status_code == 200
        attest_data = attest_resp.json()
        assert "attestation_document" in attest_data
        # attestation_document is base64-encoded CBOR
        assert len(attest_data["attestation_document"]) > 0

        # 2. Build encrypted /execute request using EncryptionTestContext
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        exec_resp = _post_execute(client, encryption_ctx, request_data)
        assert exec_resp.status_code == 200

        # 3. Decrypt the /execute response
        exec_data = decrypt_execute_response(exec_resp.json(), encryption_ctx.shared_key)
        assert "execution_id" in exec_data
        assert "attestation_document" in exec_data
        assert exec_data["status"] == "queued"

        execution_id = exec_data["execution_id"]

        # 4. Poll /execution/{id}/output until complete
        for _ in range(30):
            time.sleep(0.2)
            out_resp = _post_output(client, execution_id, encryption_ctx.shared_key)
            assert out_resp.status_code == 200

            out_data = decrypt_output_response(out_resp.json(), encryption_ctx.shared_key)
            if out_data["complete"]:
                # 5. Verify completed output fields
                assert "stdout" in out_data
                assert "stderr" in out_data
                assert out_data["exit_code"] is not None
                assert out_data["complete"] is True
                # 6. Verify output attestation document is present
                assert "output_attestation_document" in out_data
                break
        else:
            pytest.fail("Execution did not complete within polling window")


# ---------------------------------------------------------------------------
# 103.2 – Error scenarios
# ---------------------------------------------------------------------------

class TestEncryptedErrorScenarios:
    """Test error scenarios for HPKE encrypted endpoints."""

    def test_decryption_failure_wrong_key(
        self, client, encryption_ctx
    ):
        """Sending a request encrypted with a different client key causes 400."""
        # Create a second EncryptionTestContext with a DIFFERENT encryption_manager.
        # Its shared key is derived against its own server keypair, not the app's.
        wrong_ctx = EncryptionTestContext()

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        # Encrypt with wrong_ctx (different server keypair) but send to app
        body = make_encrypted_execute_request(request_data, wrong_ctx)
        resp = client.post("/execute", json=body)

        assert resp.status_code == 400
        detail = resp.json().get("detail", resp.json())
        assert detail["error"] == "decryption_failed"

    def test_missing_encryption_context_on_output(
        self, app, client, encryption_ctx
    ):
        """Polling output for an execution_id with no stored encryption context returns 400."""
        fake_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        # Manually create an execution record so the endpoint doesn't 404 first
        exec_manager = app.state.execution_manager
        exec_manager.create_execution(
            "https://github.com/owner/repo",
            "0" * 40,
            "scripts/test.sh",
            30,
        )
        # Overwrite the record with our known fake_id
        record = list(exec_manager._executions.values())[-1]
        del exec_manager._executions[record.execution_id]
        record.execution_id = fake_id
        exec_manager._executions[fake_id] = record

        # Do NOT store any encryption context for fake_id.
        # Build a valid-looking encrypted output request using the test shared key.
        payload = {"oidc_token": "valid.oidc.token", "offset": 0}
        body = make_encrypted_output_request(payload, encryption_ctx.shared_key)
        resp = client.post(f"/execution/{fake_id}/output", json=body)

        assert resp.status_code == 400
        detail = resp.json().get("detail", resp.json())
        assert detail["error"] == "no_encryption_context"

    def test_expired_oidc_token_in_encrypted_body(
        self, app, client, encryption_ctx, mock_github_and_attestation
    ):
        """Expired OIDC token inside encrypted payload returns encrypted error envelope with HTTP 200."""
        expired_result = OIDCValidationResult(
            valid=False,
            status_code=401,
            error_message="Token has expired",
            claims=None,
        )
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=expired_result
        )

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "expired.oidc.token",
        }

        resp = _post_execute(client, encryption_ctx, request_data)
        # Post-decryption errors return HTTP 200 with encrypted error envelope
        assert resp.status_code == 200
        decrypted = decrypt_execute_response(resp.json(), encryption_ctx.shared_key)
        assert decrypted["error_code"] == 401
        assert "expired" in decrypted["message"].lower() or "authentication" in decrypted["message"].lower()

    def test_unauthorized_repository_in_encrypted_body(
        self, app, client, encryption_ctx, mock_github_and_attestation
    ):
        """OIDC token from unauthorized repo inside encrypted payload returns encrypted error envelope with HTTP 200."""
        forbidden_result = OIDCValidationResult(
            valid=False,
            status_code=403,
            error_message="Repository not allowed",
            claims={"repository": "evil/repo"},
        )
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=forbidden_result
        )

        request_data = {
            "repository_url": "https://github.com/evil/repo",
            "commit_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        resp = _post_execute(client, encryption_ctx, request_data)
        # Post-decryption errors return HTTP 200 with encrypted error envelope
        assert resp.status_code == 200
        decrypted = decrypt_execute_response(resp.json(), encryption_ctx.shared_key)
        assert decrypted["error_code"] == 403
        assert "repository" in decrypted["message"].lower() or "not allowed" in decrypted["message"].lower()
