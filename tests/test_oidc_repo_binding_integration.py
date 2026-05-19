"""Integration tests for OIDC repository binding and output authentication end-to-end.

Task: 159.1

Tests the full end-to-end flow of:
- /execute with matching repository claim and URL (success)
- /execute with mismatched repository claim and URL (403)
- /output with valid Shared_Key (success, no OIDC token needed)
- /output with invalid Shared_Key (400 decryption failure)

Requirements: 2.2, 2.22, 2.23, 2.24, 6.3
"""
import os
import tempfile
import time
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


def _make_oidc_result(repository: str, sha: str = "a" * 40) -> OIDCValidationResult:
    """Create a successful OIDC validation result with the given repository claim."""
    return OIDCValidationResult(
        valid=True,
        status_code=200,
        error_message=None,
        claims={
            "repository": repository,
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "https://example.com",
            "sha": sha,
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
def encryption_ctx():
    """Create encryption test context."""
    return EncryptionTestContext()


@pytest.fixture
def app_and_client(test_config, temp_dir, encryption_ctx):
    """Create test application with mocked external dependencies.

    OIDC validation is NOT globally mocked here — individual tests control
    the OIDC result to test repo binding behavior.
    """
    with patch('src.attestation.subprocess.run') as mock_attest:
        # Setup attestation mock
        mock_attest.return_value = Mock(
            returncode=0,
            stdout=b'mock_attestation_cbor_data',
        )

        app = create_app(
            test_config,
            docker_client=create_mock_docker_client(),
            encryption_manager=encryption_ctx.encryption_manager,
        )

        # Mock clone_repo to create a temp dir with the requested script file
        from src.models import CloneResult

        def mock_clone_repo(repo_url, commit, token):
            clone_dir = tempfile.mkdtemp(dir=temp_dir, prefix="clone_")
            return CloneResult(clone_path=clone_dir, script_path="")

        def mock_validate_script_exists(clone_path, script_path):
            full_path = os.path.join(clone_path, script_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write('#!/bin/bash\necho "hello from integration test"\nexit 0')
            os.chmod(full_path, 0o755)
            return True

        # Mock authenticate to always succeed (GitHub token validation)
        app.state.repository_client.authenticate = Mock(
            return_value=Mock(success=True, error_message=None)
        )
        app.state.repository_client.clone_repo = Mock(side_effect=mock_clone_repo)
        app.state.repository_client.validate_script_exists = Mock(side_effect=mock_validate_script_exists)

        client = TestClient(app)
        yield app, client


class TestOIDCRepoBindingIntegration:
    """End-to-end tests for OIDC repository claim binding on /execute.

    Requirements: 2.22, 2.23, 2.24
    """

    def test_execute_matching_repo_claim_and_url_succeeds(
        self, app_and_client, encryption_ctx
    ):
        """When OIDC repository claim matches the request repository_url,
        /execute returns 200 with execution_id and attestation_document.

        Requirement 2.22: GHA_Server verifies repository claim matches repository_url.
        Requirement 2.24: Comparison occurs after OIDC validation succeeds.
        """
        app, client = app_and_client

        # OIDC token has repository claim "owner/repo"
        oidc_result = _make_oidc_result("owner/repo")
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=oidc_result
        )

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/build.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
        }

        body = make_encrypted_execute_request(request_data, encryption_ctx)
        response = client.post("/execute", json=body)

        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert "execution_id" in data
        assert "attestation_document" in data
        assert data["status"] == "queued"

    def test_execute_matching_repo_with_git_suffix_succeeds(
        self, app_and_client, encryption_ctx
    ):
        """URL with .git suffix still matches the OIDC repository claim.

        Requirement 2.22: Comparison strips .git suffix from URL.
        """
        app, client = app_and_client

        oidc_result = _make_oidc_result("owner/repo", sha="b" * 40)
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=oidc_result
        )

        request_data = {
            "repository_url": "https://github.com/owner/repo.git",
            "commit_hash": "b" * 40,
            "script_path": "scripts/build.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
        }

        body = make_encrypted_execute_request(request_data, encryption_ctx)
        response = client.post("/execute", json=body)

        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert "execution_id" in data

    def test_execute_mismatched_repo_claim_returns_403(
        self, app_and_client, encryption_ctx
    ):
        """When OIDC repository claim does NOT match the request repository_url,
        /execute returns 403 Forbidden with repository_mismatch error.

        Requirement 2.23: GHA_Server rejects with HTTP 403 if mismatch.
        """
        app, client = app_and_client

        # OIDC token has repository claim "attacker/malicious-repo"
        # but request targets "owner/repo"
        oidc_result = _make_oidc_result("attacker/malicious-repo")
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=oidc_result
        )

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "c" * 40,
            "script_path": "scripts/build.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
        }

        body = make_encrypted_execute_request(request_data, encryption_ctx)
        response = client.post("/execute", json=body)

        # Post-decryption errors return HTTP 200 with encrypted error envelope
        assert response.status_code == 200
        decrypted = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert decrypted.get("error") == "repository_mismatch"
        assert decrypted.get("error_code") == 403

    def test_execute_mismatched_owner_returns_403(
        self, app_and_client, encryption_ctx
    ):
        """Same repo name but different owner in OIDC claim returns 403.

        Requirement 2.23: Mismatch on owner portion triggers rejection.
        """
        app, client = app_and_client

        # Same repo name "repo" but different owner
        oidc_result = _make_oidc_result("evil-owner/repo")
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=oidc_result
        )

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "d" * 40,
            "script_path": "scripts/build.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
        }

        body = make_encrypted_execute_request(request_data, encryption_ctx)
        response = client.post("/execute", json=body)

        # Post-decryption errors return HTTP 200 with encrypted error envelope
        assert response.status_code == 200
        decrypted = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert decrypted.get("error") == "repository_mismatch"
        assert decrypted.get("error_code") == 403

    def test_execute_mismatched_repo_name_returns_403(
        self, app_and_client, encryption_ctx
    ):
        """Same owner but different repo name in OIDC claim returns 403.

        Requirement 2.23: Mismatch on repo name portion triggers rejection.
        """
        app, client = app_and_client

        oidc_result = _make_oidc_result("owner/different-repo")
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=oidc_result
        )

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "e" * 40,
            "script_path": "scripts/build.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
        }

        body = make_encrypted_execute_request(request_data, encryption_ctx)
        response = client.post("/execute", json=body)

        # Post-decryption errors return HTTP 200 with encrypted error envelope
        assert response.status_code == 200
        decrypted = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert decrypted.get("error") == "repository_mismatch"
        assert decrypted.get("error_code") == 403


class TestOutputSharedKeyAuthIntegration:
    """End-to-end tests for output endpoint authentication via Shared_Key.

    The /execution/{id}/output endpoint authenticates callers solely by
    verifying possession of the execution-bound Shared_Key. No OIDC token
    is required.

    Requirements: 2.2, 6.3
    """

    def test_output_with_valid_shared_key_succeeds(
        self, app_and_client, encryption_ctx
    ):
        """A caller with the correct Shared_Key can retrieve output without
        any OIDC token.

        Requirement 2.2: /execution/{id}/output does NOT require oidc_token.
        Requirement 6.3: Authentication via Shared_Key possession.
        """
        app, client = app_and_client

        # First, create an execution via /execute
        oidc_result = _make_oidc_result("owner/repo", sha="f" * 40)
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=oidc_result
        )

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "f" * 40,
            "script_path": "scripts/build.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
        }

        body = make_encrypted_execute_request(request_data, encryption_ctx)
        exec_response = client.post("/execute", json=body)
        assert exec_response.status_code == 200

        exec_data = decrypt_execute_response(
            exec_response.json(), encryption_ctx.shared_key
        )
        execution_id = exec_data["execution_id"]

        # Wait briefly for execution to start
        time.sleep(0.5)

        # Poll output with valid Shared_Key — no OIDC token in payload
        output_payload = {"offset": 0}
        output_body = make_encrypted_output_request(
            output_payload, encryption_ctx.shared_key
        )
        output_response = client.post(
            f"/execution/{execution_id}/output", json=output_body
        )

        assert output_response.status_code == 200
        output_data = decrypt_output_response(
            output_response.json(), encryption_ctx.shared_key
        )
        assert "status" in output_data
        assert "stdout" in output_data
        assert "stderr" in output_data
        assert output_data["execution_id"] == execution_id

    def test_output_polls_until_completion_with_shared_key(
        self, app_and_client, encryption_ctx
    ):
        """Polling with valid Shared_Key eventually returns complete output.

        Requirement 6.3: Shared_Key serves as authentication for output retrieval.
        """
        app, client = app_and_client

        oidc_result = _make_oidc_result("owner/repo", sha="1" * 40)
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=oidc_result
        )

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "1" * 40,
            "script_path": "scripts/build.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
        }

        body = make_encrypted_execute_request(request_data, encryption_ctx)
        exec_response = client.post("/execute", json=body)
        assert exec_response.status_code == 200

        exec_data = decrypt_execute_response(
            exec_response.json(), encryption_ctx.shared_key
        )
        execution_id = exec_data["execution_id"]

        # Poll until execution completes
        completed = False
        for _ in range(30):
            time.sleep(0.2)
            output_payload = {"offset": 0}
            output_body = make_encrypted_output_request(
                output_payload, encryption_ctx.shared_key
            )
            output_response = client.post(
                f"/execution/{execution_id}/output", json=output_body
            )
            assert output_response.status_code == 200

            output_data = decrypt_output_response(
                output_response.json(), encryption_ctx.shared_key
            )
            if output_data["complete"]:
                completed = True
                assert output_data["exit_code"] is not None
                assert output_data["status"] in ["completed", "failed"]
                break

        assert completed, "Execution did not complete within timeout"

    def test_output_with_invalid_shared_key_returns_400(
        self, app_and_client, encryption_ctx
    ):
        """A caller with the wrong Shared_Key gets 400 decryption failure.

        Requirement 6.3: Only the original caller who performed the
        PQ_Hybrid_KEM exchange possesses the correct Shared_Key.
        """
        app, client = app_and_client

        # First, create an execution via /execute
        oidc_result = _make_oidc_result("owner/repo", sha="2" * 40)
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=oidc_result
        )

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "2" * 40,
            "script_path": "scripts/build.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
        }

        body = make_encrypted_execute_request(request_data, encryption_ctx)
        exec_response = client.post("/execute", json=body)
        assert exec_response.status_code == 200

        exec_data = decrypt_execute_response(
            exec_response.json(), encryption_ctx.shared_key
        )
        execution_id = exec_data["execution_id"]

        # Attempt to poll output with a WRONG Shared_Key
        wrong_key = os.urandom(32)
        output_payload = {"offset": 0}
        output_body = make_encrypted_output_request(output_payload, wrong_key)
        output_response = client.post(
            f"/execution/{execution_id}/output", json=output_body
        )

        assert output_response.status_code == 400
        detail = output_response.json().get("detail", {})
        assert detail.get("error") == "decryption_failed"

    def test_output_with_no_encryption_context_returns_400(
        self, app_and_client, encryption_ctx
    ):
        """When the Encryption_Context has been removed (e.g., after cleanup),
        the output endpoint returns 400.

        Requirement 6.3: No Encryption_Context means the Shared_Key cannot
        be looked up for decryption.
        """
        app, client = app_and_client

        # First, create an execution via /execute
        oidc_result = _make_oidc_result("owner/repo", sha="3" * 40)
        app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=oidc_result
        )

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "3" * 40,
            "script_path": "scripts/build.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
        }

        body = make_encrypted_execute_request(request_data, encryption_ctx)
        exec_response = client.post("/execute", json=body)
        assert exec_response.status_code == 200

        exec_data = decrypt_execute_response(
            exec_response.json(), encryption_ctx.shared_key
        )
        execution_id = exec_data["execution_id"]

        # Remove the encryption context to simulate cleanup
        encryption_ctx.encryption_manager.remove_encryption_context(execution_id)

        # Attempt to poll output — should get 400 (no encryption context)
        output_payload = {"offset": 0}
        output_body = make_encrypted_output_request(
            output_payload, encryption_ctx.shared_key
        )
        output_response = client.post(
            f"/execution/{execution_id}/output", json=output_body
        )

        assert output_response.status_code == 400
        detail = output_response.json().get("detail", {})
        assert detail.get("error") == "no_encryption_context"
