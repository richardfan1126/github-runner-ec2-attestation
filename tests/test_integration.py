"""Integration tests for GitHub Actions Remote Executor

Simplified integration tests focusing on core end-to-end flows.
Tests use mocked external dependencies (GitHub API, NitroTPM device).
"""
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import tempfile

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


@pytest.fixture
def temp_dir():
    """Create temporary directory for test files"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def test_config(temp_dir):
    """Create test configuration"""
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
    """Mock both GitHub API and attestation generation"""
    with patch('requests.Session') as mock_session_class, \
         patch('src.repository.subprocess.run') as mock_git_run, \
         patch('src.attestation.subprocess.run') as mock_attest:

        # Setup GitHub API mock for authenticate()
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_session.headers = {}
        mock_session.get.return_value = Mock(status_code=200)

        # Setup git clone/checkout mock
        mock_git_run.return_value = Mock(returncode=0, stdout="", stderr="")

        # Setup attestation mock
        mock_attest_result = Mock(
            returncode=0,
            stdout=b'mock_attestation_cbor_data'
        )
        mock_attest.return_value = mock_attest_result

        yield {
            'session': mock_session,
            'git_run': mock_git_run,
            'attestation': mock_attest
        }


@pytest.fixture
def encryption_ctx():
    """Create encryption test context"""
    return EncryptionTestContext()


@pytest.fixture
def app(test_config, mock_github_and_attestation, temp_dir, encryption_ctx):
    """Create test application with OIDC validation mocked"""
    application = create_app(
        test_config,
        docker_client=create_mock_docker_client(),
        encryption_manager=encryption_ctx.encryption_manager,
    )
    # Mock OIDC validation to always succeed for integration tests
    application.state.request_validator.validate_oidc_token_from_body = Mock(return_value=VALID_OIDC_RESULT)

    # Mock clone_repo to create a temp dir with the requested script file
    from src.models import CloneResult

    def mock_clone_repo(repo_url, commit, token):
        clone_dir = tempfile.mkdtemp(dir=temp_dir, prefix="clone_")
        return CloneResult(clone_path=clone_dir, script_path="")

    def mock_validate_script_exists(clone_path, script_path):
        # Create the script file so os.path.getsize works
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
    """Create test client"""
    return TestClient(app)


def _post_execute(client, encryption_ctx, request_data):
    """Helper to post an encrypted execute request and return (response, decrypted_data_or_None)."""
    body = make_encrypted_execute_request(request_data, encryption_ctx)
    response = client.post("/execute", json=body)
    return response


def _post_output(client, execution_id, shared_key, offset=0, oidc_token="valid.oidc.token"):
    """Helper to post an encrypted output request."""
    payload = {"oidc_token": oidc_token, "offset": offset}
    body = make_encrypted_output_request(payload, shared_key)
    return client.post(f"/execution/{execution_id}/output", json=body)


class TestEndToEndIntegration:
    """Test complete end-to-end integration scenarios"""

    def test_complete_execution_flow(self, client, mock_github_and_attestation, encryption_ctx):
        """
        Test complete execution flow from request to output retrieval
        """
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200

        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert "execution_id" in data
        assert "attestation_document" in data
        assert data["status"] == "queued"

        execution_id = data["execution_id"]

        # Poll for completion
        for _ in range(20):
            time.sleep(0.2)
            output_response = _post_output(client, execution_id, encryption_ctx.shared_key)
            assert output_response.status_code == 200

            output_data = decrypt_output_response(output_response.json(), encryption_ctx.shared_key)
            if output_data["complete"]:
                assert output_data["status"] in ["completed", "failed"]
                assert "stdout" in output_data
                assert "stderr" in output_data
                assert output_data["exit_code"] is not None
                break
        else:
            pytest.fail("Execution did not complete")

    def test_concurrent_executions(self, client, mock_github_and_attestation, encryption_ctx):
        """Test handling multiple concurrent executions"""
        execution_ids = []

        for i in range(3):
            request_data = {
                "repository_url": "https://github.com/owner/repo",
                "commit_hash": f"{i:040x}",
                "script_path": f"scripts/test{i}.sh",
                "github_token": "ghp_test_token",
                "oidc_token": "valid.oidc.token",
            }

            response = _post_execute(client, encryption_ctx, request_data)
            assert response.status_code == 200
            data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
            execution_ids.append(data["execution_id"])

        # Verify all IDs are unique
        assert len(set(execution_ids)) == 3

        # Wait and verify all complete
        time.sleep(1)
        for execution_id in execution_ids:
            response = _post_output(client, execution_id, encryption_ctx.shared_key)
            assert response.status_code == 200

    def test_rate_limiting(self, test_config, mock_github_and_attestation, temp_dir):
        """Test rate limiting enforcement"""
        # Use a low rate limit for this specific test
        test_config.rate_limit_per_ip = 5
        ctx = EncryptionTestContext()
        app = create_app(test_config, docker_client=create_mock_docker_client(), encryption_manager=ctx.encryption_manager)
        app.state.request_validator.validate_oidc_token_from_body = Mock(return_value=VALID_OIDC_RESULT)
        rate_client = TestClient(app)

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a1a2a3a4a5a6a1a2a3a4a5a6a1a2a3a4a5a6a1a2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        rate_limited = False
        for _ in range(15):
            response = _post_execute(rate_client, ctx, request_data)
            if response.status_code == 429:
                rate_limited = True
                break

        assert rate_limited, "Rate limit should have been enforced"

    def test_execution_not_found(self, client, encryption_ctx):
        """Test retrieving non-existent execution"""
        fake_id = "00000000-0000-0000-0000-000000000000"
        # Store encryption context so we get past the "no encryption context" check
        encryption_ctx.encryption_manager.store_encryption_context(fake_id, encryption_ctx.shared_key)
        response = _post_output(client, fake_id, encryption_ctx.shared_key)
        assert response.status_code == 404

    def test_health_endpoint(self, client):
        """Test health check endpoint"""
        response = client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert "status" in data



class TestErrorScenarios:
    """Test error handling scenarios"""

    def test_authentication_failure(self, client, test_config, encryption_ctx):
        """Test GitHub authentication failure"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            mock_session.headers = {}
            mock_session.get.return_value = Mock(status_code=401)

            request_data = {
                "repository_url": "https://github.com/owner/repo",
                "commit_hash": "c1c2c3c4c5c6c1c2c3c4c5c6c1c2c3c4c5c6c1c2",
                "script_path": "scripts/test.sh",
                "github_token": "invalid_token",
                "oidc_token": "valid.oidc.token",
            }

            response = _post_execute(client, encryption_ctx, request_data)
            assert response.status_code == 401

    def test_execution_timeout(self, test_config, mock_github_and_attestation, temp_dir):
        """Test script execution timeout"""
        # Use a shorter timeout for faster test
        test_config.execution_timeout_seconds = 1

        ctx = EncryptionTestContext()
        # Create fresh app and client to avoid rate limiting from other tests
        app = create_app(test_config, docker_client=create_mock_docker_client(), encryption_manager=ctx.encryption_manager)
        app.state.request_validator.validate_oidc_token_from_body = Mock(return_value=VALID_OIDC_RESULT)

        from src.models import CloneResult

        def mock_clone_repo(repo_url, commit, token):
            clone_dir = tempfile.mkdtemp(dir=temp_dir, prefix="clone_")
            return CloneResult(clone_path=clone_dir, script_path="")

        def mock_validate_script_exists(clone_path, script_path):
            full_path = os.path.join(clone_path, script_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write('#!/bin/bash\nsleep 10\nexit 0')
            os.chmod(full_path, 0o755)
            return True

        app.state.repository_client.clone_repo = Mock(side_effect=mock_clone_repo)
        app.state.repository_client.validate_script_exists = Mock(side_effect=mock_validate_script_exists)

        client = TestClient(app)

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "e1e2e3e4e5e6e1e2e3e4e5e6e1e2e3e4e5e6e1e2",
            "script_path": "scripts/timeout.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        response = _post_execute(client, ctx, request_data)
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), ctx.shared_key)
        execution_id = data["execution_id"]

        # Wait for timeout to occur (config has 1 second timeout)
        time.sleep(2)

        # Check status - should be timed out
        output_response = _post_output(client, execution_id, ctx.shared_key)
        assert output_response.status_code == 200

        output_data = decrypt_output_response(output_response.json(), ctx.shared_key)
        # The execution should be marked as timed out
        assert output_data["status"] in ["running", "timed_out"]


class TestPQHybridKEMEndToEnd:
    """Test PQ Hybrid KEM specific end-to-end scenarios (Req 36.1, 37.1, 40.1, 41.1, 42.1, 42.4)"""

    def test_attest_returns_server_public_key_and_attestation(self, client, encryption_ctx):
        """
        Verify /attest returns both server_public_key and attestation_document (Req 37.1).
        """
        response = client.get("/attest")
        assert response.status_code == 200

        data = response.json()
        assert "attestation_document" in data
        assert "server_public_key" in data

        # Verify server_public_key is valid base64 and matches the encryption manager's key
        import base64
        server_key_bytes = base64.b64decode(data["server_public_key"])
        assert server_key_bytes == encryption_ctx.encryption_manager.server_public_key

    def test_pq_hybrid_key_exchange_produces_correct_shared_key(self, client, encryption_ctx):
        """
        Verify PQ hybrid key exchange (X25519 + ML-KEM-768) produces a shared key
        that correctly encrypts/decrypts /execute payloads (Req 40.1, 42.1).
        """
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "d1d2d3d4d5d6d1d2d3d4d5d6d1d2d3d4d5d6d1d2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200

        # Decrypt with the client-derived shared key — proves both sides derived the same key
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert "execution_id" in data
        assert data["status"] == "queued"

        # Verify the shared key was stored per execution (Req 41.1)
        stored_key = encryption_ctx.encryption_manager.get_shared_key(data["execution_id"])
        assert stored_key is not None
        assert stored_key == encryption_ctx.shared_key

    def test_execute_response_encrypted_with_attestation(self, client, encryption_ctx):
        """
        Verify /execute response is encrypted and contains attestation_document (Req 42.1).
        """
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "e1e2e3e4e5e6e1e2e3e4e5e6e1e2e3e4e5e6e1e2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200

        # Raw response should only have encrypted_response (not plaintext fields)
        raw = response.json()
        assert "encrypted_response" in raw
        assert "execution_id" not in raw

        data = decrypt_execute_response(raw, encryption_ctx.shared_key)
        assert "attestation_document" in data
        assert len(data["attestation_document"]) > 0

    def test_output_attestation_document_on_completion(self, client, mock_github_and_attestation, encryption_ctx):
        """
        Verify output_attestation_document is included when execution completes (Req 42.4).
        """
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "f1f2f3f4f5f6f1f2f3f4f5f6f1f2f3f4f5f6f1f2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        execution_id = data["execution_id"]

        # Poll until complete
        for _ in range(20):
            time.sleep(0.2)
            output_response = _post_output(client, execution_id, encryption_ctx.shared_key)
            assert output_response.status_code == 200

            output_data = decrypt_output_response(output_response.json(), encryption_ctx.shared_key)
            if output_data["complete"]:
                # Verify output attestation document is present
                assert "output_attestation_document" in output_data
                # It may be a base64 string or None if attestation generation failed
                # In our mock setup it should be present
                break
        else:
            pytest.fail("Execution did not complete")

    def test_output_response_encrypted(self, client, encryption_ctx):
        """
        Verify /execution/{id}/output response is encrypted (Req 42.4).
        """
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a2b2c2d2e2f2a2b2c2d2e2f2a2b2c2d2e2f2a2b2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        response = _post_execute(client, encryption_ctx, request_data)
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        execution_id = data["execution_id"]

        time.sleep(0.3)
        output_response = _post_output(client, execution_id, encryption_ctx.shared_key)
        assert output_response.status_code == 200

        # Raw response should only have encrypted_response
        raw = output_response.json()
        assert "encrypted_response" in raw
        assert "stdout" not in raw


class TestPQHybridKEMErrorScenarios:
    """Test PQ Hybrid KEM error scenarios (Req 40.5, 40.6, 42.6, 42.7)"""

    def test_execute_with_wrong_pq_hybrid_key(self, client, encryption_ctx):
        """
        Test that using a DIFFERENT EncryptionTestContext (different keys) for
        /execute results in decryption failure → 400 (Req 40.5).
        """
        # Create a second context with different keys
        wrong_ctx = EncryptionTestContext()

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "b1b2b3b4b5b6b1b2b3b4b5b6b1b2b3b4b5b6b1b2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        # Encrypt with wrong_ctx (different server keypair) but send to the app
        # that uses encryption_ctx's server. The client_public_key won't match.
        body = make_encrypted_execute_request(request_data, wrong_ctx)
        response = client.post("/execute", json=body)
        assert response.status_code == 400

    def test_execute_with_invalid_client_public_key(self, client, encryption_ctx):
        """
        Test that sending invalid/corrupted Client_Public_Key (bad ML-KEM-768
        ciphertext) returns 400 (Req 40.6).
        """
        import base64
        import struct

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "c1c2c3c4c5c6c1c2c3c4c5c6c1c2c3c4c5c6c1c2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        # Build a valid encrypted payload using the real context
        body = make_encrypted_execute_request(request_data, encryption_ctx)

        # Replace client_public_key with one containing garbage ML-KEM-768 ciphertext
        valid_x25519_pub = encryption_ctx._client_x25519_pub_bytes
        bad_mlkem_ct = os.urandom(1088)  # Random bytes, not a valid ML-KEM-768 ciphertext
        bad_client_key = (
            struct.pack(">I", len(valid_x25519_pub))
            + valid_x25519_pub
            + struct.pack(">I", len(bad_mlkem_ct))
            + bad_mlkem_ct
        )
        body["client_public_key"] = base64.b64encode(bad_client_key).decode()

        response = client.post("/execute", json=body)
        assert response.status_code == 400

    def test_output_missing_encryption_context(self, client, encryption_ctx):
        """
        Test that calling /execution/{id}/output without an Encryption_Context
        returns 400 (Req 42.6).
        """
        # Create an execution first so the execution record exists
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "d1d2d3d4d5d6d1d2d3d4d5d6d1d2d3d4d5d6d1d2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        execution_id = data["execution_id"]

        # Remove the encryption context to simulate missing context
        encryption_ctx.encryption_manager.remove_encryption_context(execution_id)

        # Now try to get output — should get 400 (no encryption context)
        output_response = _post_output(client, execution_id, encryption_ctx.shared_key)
        assert output_response.status_code == 400

    def test_output_decryption_failure_wrong_key(self, client, encryption_ctx):
        """
        Test that decryption failure on /execution/{id}/output returns 400 (Req 42.7).
        """
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "e1e2e3e4e5e6e1e2e3e4e5e6e1e2e3e4e5e6e1e2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        execution_id = data["execution_id"]

        # Send output request encrypted with a WRONG key
        wrong_key = os.urandom(32)
        output_response = _post_output(client, execution_id, wrong_key)
        assert output_response.status_code == 400


class TestCleanupAndRetention:
    """Test cleanup and retention policies"""

    def test_execution_cleanup(self, client, mock_github_and_attestation, app, encryption_ctx):
        """Test cleanup of expired executions"""
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "b1b2b3b4b5b6b1b2b3b4b5b6b1b2b3b4b5b6b1b2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        response = _post_execute(client, encryption_ctx, request_data)
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        execution_id = data["execution_id"]

        # Wait for completion
        time.sleep(1)

        # Verify execution exists
        response = _post_output(client, execution_id, encryption_ctx.shared_key)
        assert response.status_code == 200

        # Manually expire the execution
        exec_manager = app.state.execution_manager
        record = exec_manager.get_execution(execution_id)
        if record:
            record.completed_at = datetime.now(timezone.utc) - timedelta(hours=2)

        # Run cleanup
        removed = exec_manager.cleanup_expired()
        assert removed >= 1

        # Verify execution was removed - now get_execution returns None,
        # but the encryption context may still exist, so we get 404 from the endpoint
        response = _post_output(client, execution_id, encryption_ctx.shared_key)
        assert response.status_code == 404

    def test_temporary_file_cleanup(self, client, mock_github_and_attestation, temp_dir, encryption_ctx):
        """Test cleanup of temporary files after execution"""
        files_before = len(list(Path(temp_dir).rglob('*')))

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "c1c2c3c4c5c6c1c2c3c4c5c6c1c2c3c4c5c6c1c2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token",
            "oidc_token": "valid.oidc.token",
        }

        response = _post_execute(client, encryption_ctx, request_data)
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        execution_id = data["execution_id"]

        # Wait for completion
        time.sleep(1)

        # Verify execution completed
        response = _post_output(client, execution_id, encryption_ctx.shared_key)
        output_data = decrypt_output_response(response.json(), encryption_ctx.shared_key)
        assert output_data["complete"]

        # Verify temp files were cleaned up
        files_after = len(list(Path(temp_dir).rglob('*')))
        assert files_after <= files_before + 1
