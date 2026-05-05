"""Integration tests for security hardening changes.

Tests OIDC repository binding, anti-replay nonce validation,
and concurrency enforcement end-to-end through the encrypted API.
"""
import os
import time
import tempfile
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.models import OIDCValidationResult
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
    claims={"repository": "owner/repo", "iss": "https://token.actions.githubusercontent.com", "aud": "https://example.com"},
)


def _make_oidc_result(repository: str) -> OIDCValidationResult:
    """Create an OIDCValidationResult with a specific repository claim."""
    return OIDCValidationResult(
        valid=True,
        status_code=200,
        error_message=None,
        claims={"repository": repository, "iss": "https://token.actions.githubusercontent.com", "aud": "https://example.com"},
    )


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def test_config(temp_dir):
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
    with patch('requests.Session') as mock_session_class, \
         patch('src.repository.subprocess.run') as mock_git_run, \
         patch('src.attestation.subprocess.run') as mock_attest:

        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_session.headers = {}
        mock_session.get.return_value = Mock(status_code=200)

        mock_git_run.return_value = Mock(returncode=0, stdout="", stderr="")

        mock_attest_result = Mock(returncode=0, stdout=b'mock_attestation_cbor_data')
        mock_attest.return_value = mock_attest_result

        yield {
            'session': mock_session,
            'git_run': mock_git_run,
            'attestation': mock_attest,
        }


@pytest.fixture
def encryption_ctx():
    return EncryptionTestContext()


@pytest.fixture
def app(test_config, mock_github_and_attestation, temp_dir, encryption_ctx):
    application = create_app(
        test_config,
        docker_client=create_mock_docker_client(),
        encryption_manager=encryption_ctx.encryption_manager,
    )
    application.state.request_validator.validate_oidc_token_from_body = Mock(return_value=VALID_OIDC_RESULT)

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
    application.state.repository_client.validate_script_exists = Mock(side_effect=mock_validate_script_exists)

    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def _post_execute(client, encryption_ctx, request_data):
    body = make_encrypted_execute_request(request_data, encryption_ctx)
    return client.post("/execute", json=body)


def _post_output(client, execution_id, shared_key, offset=0, nonce=None):
    """Helper to post an encrypted output request.
    
    Authentication is via Shared_Key possession — no OIDC token needed.
    """
    payload = {"offset": offset}
    if nonce is not None:
        payload["nonce"] = nonce
    body = make_encrypted_output_request(payload, shared_key)
    return client.post(f"/execution/{execution_id}/output", json=body)


def _base_request_data(**overrides):
    data = {
        "repository_url": "https://github.com/owner/repo",
        "commit_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "script_path": "scripts/test.sh",
        "github_token": "ghp_test_token",
        "oidc_token": "valid.oidc.token",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# 159.1 – OIDC Repository Binding Integration Tests
# ---------------------------------------------------------------------------

class TestOIDCRepositoryBindingIntegration:
    """Integration tests for OIDC repository binding (Req 2.22-2.24, 6.14-6.16)"""

    def test_execute_matching_repo_claim_and_url(self, client, encryption_ctx, app):
        """
        /execute succeeds when OIDC repository claim matches the repository_url.
        Validates: Requirements 2.22, 2.24
        """
        # OIDC mock already returns "owner/repo" which matches the URL
        response = _post_execute(client, encryption_ctx, _base_request_data())
        assert response.status_code == 200

        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert "execution_id" in data
        assert data["status"] == "queued"

    def test_execute_mismatched_repo_claim_and_url(self, client, encryption_ctx, app):
        """
        /execute returns 403 when OIDC repository claim does not match repository_url.
        Validates: Requirements 2.22, 2.23
        """
        # Change the OIDC mock to return a different repository claim
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=_make_oidc_result("other-owner/other-repo")
        )

        response = _post_execute(client, encryption_ctx, _base_request_data())
        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["error"] == "repository_mismatch"

    def test_output_matching_repo_claim(self, client, encryption_ctx, app):
        """
        /output succeeds when caller possesses the correct Shared_Key.
        No OIDC validation is performed on the output endpoint.
        Validates: Requirements 42.2, 42.3
        """
        # Create execution with matching repo
        response = _post_execute(client, encryption_ctx, _base_request_data())
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        execution_id = data["execution_id"]

        time.sleep(0.3)

        # Poll output — Shared_Key possession is the sole auth mechanism
        output_response = _post_output(client, execution_id, encryption_ctx.shared_key)
        assert output_response.status_code == 200

    def test_output_without_encryption_context_returns_400(self, client, encryption_ctx, app):
        """
        /output returns 400 when no encryption context exists for the execution_id.
        Validates: Requirements 42.6, 42.7
        """
        # Try to poll output for an execution_id that has no stored encryption context
        # but does have an execution record
        response = _post_execute(client, encryption_ctx, _base_request_data())
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        execution_id = data["execution_id"]

        time.sleep(0.3)

        # Remove the encryption context to simulate expired/cleaned-up state
        encryption_ctx.encryption_manager.remove_encryption_context(execution_id)

        output_response = _post_output(client, execution_id, encryption_ctx.shared_key)
        assert output_response.status_code == 400
        detail = output_response.json()["detail"]
        assert detail["error"] == "no_encryption_context"



# ---------------------------------------------------------------------------
# 159.2 – Anti-Replay Nonce Integration Tests
# ---------------------------------------------------------------------------

class TestAntiReplayNonceIntegration:
    """Integration tests for anti-replay nonce validation (Req 44.1-44.3, 44.5)"""

    def test_execute_with_unique_nonce(self, client, encryption_ctx):
        """
        /execute succeeds when a unique nonce is provided.
        Validates: Requirements 44.1, 44.2
        """
        request_data = _base_request_data(nonce="unique-nonce-execute-1")
        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200

        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert "execution_id" in data

    def test_execute_with_duplicate_nonce(self, client, encryption_ctx):
        """
        /execute returns 400 when a duplicate nonce is submitted.
        Validates: Requirements 44.2, 44.3
        """
        nonce = "duplicate-nonce-execute"
        request_data = _base_request_data(nonce=nonce)

        # First request succeeds
        resp1 = _post_execute(client, encryption_ctx, request_data)
        assert resp1.status_code == 200

        # Second request with same nonce is rejected
        ctx2 = EncryptionTestContext()
        # Need a fresh encryption context because each /execute derives a new shared key
        # But the app's encryption manager is the same, so we need to use the same one
        # Actually we can reuse the same encryption_ctx for the second call
        resp2 = _post_execute(client, encryption_ctx, request_data)
        assert resp2.status_code == 400
        detail = resp2.json()["detail"]
        assert detail["error"] == "duplicate_nonce"

    def test_output_with_duplicate_nonce(self, client, encryption_ctx):
        """
        /output returns 400 when a duplicate nonce is submitted.
        Validates: Requirements 44.3, 44.5
        """
        # Create an execution first
        response = _post_execute(client, encryption_ctx, _base_request_data())
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        execution_id = data["execution_id"]

        time.sleep(0.3)

        nonce = "duplicate-nonce-output"

        # First output request with nonce succeeds
        resp1 = _post_output(client, execution_id, encryption_ctx.shared_key, nonce=nonce)
        assert resp1.status_code == 200

        # Second output request with same nonce is rejected
        resp2 = _post_output(client, execution_id, encryption_ctx.shared_key, nonce=nonce)
        assert resp2.status_code == 400
        detail = resp2.json()["detail"]
        assert detail["error"] == "duplicate_nonce"


# ---------------------------------------------------------------------------
# 159.3 – Concurrency Enforcement Integration Tests
# ---------------------------------------------------------------------------

class TestConcurrencyEnforcementIntegration:
    """Integration tests for concurrency enforcement (Req 8.11, 8.12)"""

    def test_max_concurrent_executions_enforced(self, test_config, mock_github_and_attestation, temp_dir):
        """
        Server returns 503 when MAX_CONCURRENT_EXECUTIONS is reached.
        Validates: Requirements 8.11, 8.12
        """
        # Set max concurrent to 1
        test_config.max_concurrent_executions = 1
        # Use a longer timeout so the first execution stays active
        test_config.execution_timeout_seconds = 30

        ctx = EncryptionTestContext()
        application = create_app(
            test_config,
            docker_client=create_mock_docker_client(),
            encryption_manager=ctx.encryption_manager,
        )
        application.state.request_validator.validate_oidc_token_from_body = Mock(return_value=VALID_OIDC_RESULT)

        from src.models import CloneResult

        def mock_clone_repo(repo_url, commit, token):
            clone_dir = tempfile.mkdtemp(dir=temp_dir, prefix="clone_")
            return CloneResult(clone_path=clone_dir, script_path="")

        def mock_validate_script_exists(clone_path, script_path):
            full_path = os.path.join(clone_path, script_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                # Script that sleeps long enough to stay active
                f.write('#!/bin/bash\nsleep 60\nexit 0')
            os.chmod(full_path, 0o755)
            return True

        application.state.repository_client.clone_repo = Mock(side_effect=mock_clone_repo)
        application.state.repository_client.validate_script_exists = Mock(side_effect=mock_validate_script_exists)

        test_client = TestClient(application)

        # First execution should succeed
        req1 = _base_request_data(commit_hash="1111111111111111111111111111111111111111")
        body1 = make_encrypted_execute_request(req1, ctx)
        resp1 = test_client.post("/execute", json=body1)
        assert resp1.status_code == 200

        # Give the first execution a moment to start
        time.sleep(0.3)

        # Second execution should be rejected with 503
        req2 = _base_request_data(commit_hash="2222222222222222222222222222222222222222")
        body2 = make_encrypted_execute_request(req2, ctx)
        resp2 = test_client.post("/execute", json=body2)
        assert resp2.status_code == 503
        detail = resp2.json()["detail"]
        assert detail["error"] == "at_capacity"
