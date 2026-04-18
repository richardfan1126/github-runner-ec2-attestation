"""Unit tests for execution output repository binding.

Task 136.4: Verify that the `repository` claim from the validated OIDC token
on /execution/{id}/output must match the repository stored in the execution record.

Requirements: 6.14, 6.15, 6.16
"""
from datetime import datetime, timezone
from unittest.mock import patch, Mock

import pytest
from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.models import (
    OIDCValidationResult,
    ExecutionRecord,
    ExecutionStatus,
    OutputData,
    AttestationDocument,
    CloneResult,
)
from src.server import create_app
from src.validation import GITHUB_OIDC_ISSUER
from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_execute_request,
    make_encrypted_output_request,
    decrypt_execute_response,
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


def _make_oidc_result(repository: str) -> OIDCValidationResult:
    return OIDCValidationResult(
        valid=True,
        status_code=200,
        error_message=None,
        claims={
            "repository": repository,
            "iss": GITHUB_OIDC_ISSUER,
            "aud": EXPECTED_AUDIENCE,
        },
    )


def _create_client():
    ctx = EncryptionTestContext()
    app = create_app(_get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)
    return ctx, app, client


class TestOutputRepoBindingMatch:
    """Tests where OIDC repository claim matches execution record (allowed)."""

    def test_matching_repo_allows_output(self):
        """Matching repository claim and execution record → 200"""
        ctx, app, client = _create_client()
        execution_id = "test-match"
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
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=_make_oidc_result("owner/repo"),
        ), patch.object(
            app.state.execution_manager, "get_execution", return_value=record
        ), patch.object(
            app.state.output_collector, "get_output", return_value=output_data
        ), patch.object(
            app.state.attestation_generator,
            "generate_output_attestation",
            return_value=(b"attest", None),
        ):
            body = make_encrypted_output_request(
                {"oidc_token": "valid.oidc.token", "offset": 0}, ctx.shared_key
            )
            response = client.post(f"/execution/{execution_id}/output", json=body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert data["execution_id"] == execution_id
        assert data["stdout"] == "done\n"


class TestOutputRepoBindingMismatch:
    """Tests where OIDC repository claim does NOT match execution record (403)."""

    def test_mismatched_repo_returns_403(self):
        """Different owner/repo in OIDC claim vs execution record → 403"""
        ctx, app, client = _create_client()
        execution_id = "test-mismatch"
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

        with patch.object(
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=_make_oidc_result("attacker/evil-repo"),
        ), patch.object(
            app.state.execution_manager, "get_execution", return_value=record
        ):
            body = make_encrypted_output_request(
                {"oidc_token": "valid.oidc.token", "offset": 0}, ctx.shared_key
            )
            response = client.post(f"/execution/{execution_id}/output", json=body)

        assert response.status_code == 403
        detail = response.json().get("detail", {})
        assert detail.get("error") == "repository_mismatch"

    def test_mismatched_owner_returns_403(self):
        """Same repo name but different owner → 403"""
        ctx, app, client = _create_client()
        execution_id = "test-mismatch-owner"
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
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=_make_oidc_result("evil-owner/repo"),
        ), patch.object(
            app.state.execution_manager, "get_execution", return_value=record
        ):
            body = make_encrypted_output_request(
                {"oidc_token": "valid.oidc.token", "offset": 0}, ctx.shared_key
            )
            response = client.post(f"/execution/{execution_id}/output", json=body)

        assert response.status_code == 403

    def test_mismatch_error_message(self):
        """403 response includes repository_mismatch error code."""
        ctx, app, client = _create_client()
        execution_id = "test-mismatch-msg"
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

        with patch.object(
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=_make_oidc_result("other/other-repo"),
        ), patch.object(
            app.state.execution_manager, "get_execution", return_value=record
        ):
            body = make_encrypted_output_request(
                {"oidc_token": "valid.oidc.token", "offset": 0}, ctx.shared_key
            )
            response = client.post(f"/execution/{execution_id}/output", json=body)

        assert response.status_code == 403
        detail = response.json().get("detail", {})
        assert detail.get("error") == "repository_mismatch"


class TestRepoStoredAtCreationCheckedAtRetrieval:
    """Test that repository is stored at /execute creation and checked at /output retrieval."""

    def test_repo_stored_at_creation_and_checked_at_retrieval(self):
        """
        End-to-end: create execution via /execute (stores repo claim),
        then retrieve output with matching and mismatching OIDC claims.
        """
        ctx = EncryptionTestContext()
        app = create_app(_get_test_config(), encryption_manager=ctx.encryption_manager)
        client = TestClient(app)

        # Step 1: Create execution via /execute
        execute_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test",
            "oidc_token": "valid.oidc.token",
        }

        with patch.object(
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=_make_oidc_result("owner/repo"),
        ), patch.object(
            app.state.request_validator,
            "validate_execution_request",
            return_value=Mock(valid=True, errors=[]),
        ), patch.object(
            app.state.repository_client,
            "authenticate",
            return_value=Mock(success=True, error_message=None),
        ), patch.object(
            app.state.repository_client,
            "clone_repo",
            return_value=CloneResult(clone_path="/tmp/clone", script_path=""),
        ), patch.object(
            app.state.repository_client,
            "validate_script_exists",
            return_value=True,
        ), patch("os.path.getsize", return_value=50), patch.object(
            app.state.attestation_generator,
            "generate_attestation",
            return_value=(
                AttestationDocument(
                    repository_url="https://github.com/owner/repo",
                    commit_hash="a" * 40,
                    script_path="scripts/test.sh",
                    timestamp=datetime.now(timezone.utc),
                    signature=b"sig",
                ),
                None,
            ),
        ), patch.object(app.state.script_executor, "execute_async"):
            body = make_encrypted_execute_request(execute_data, ctx)
            exec_response = client.post("/execute", json=body)

        assert exec_response.status_code == 200
        exec_data = decrypt_execute_response(exec_response.json(), ctx.shared_key)
        execution_id = exec_data["execution_id"]

        # Verify the execution record has the repository stored
        record = app.state.execution_manager.get_execution(execution_id)
        assert record is not None
        assert record.repository == "owner/repo"

        # Step 2: Poll output with matching OIDC claim → should succeed
        output_data = OutputData(
            stdout="ok\n", stderr="", stdout_offset=3,
            stderr_offset=0, complete=True, exit_code=0,
        )
        app.state.execution_manager.update_status(
            execution_id, ExecutionStatus.COMPLETED, exit_code=0
        )

        with patch.object(
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=_make_oidc_result("owner/repo"),
        ), patch.object(
            app.state.output_collector, "get_output", return_value=output_data
        ), patch.object(
            app.state.attestation_generator,
            "generate_output_attestation",
            return_value=(b"attest", None),
        ):
            body = make_encrypted_output_request(
                {"oidc_token": "valid.oidc.token", "offset": 0}, ctx.shared_key
            )
            output_response = client.post(
                f"/execution/{execution_id}/output", json=body
            )

        assert output_response.status_code == 200

        # Step 3: Poll output with mismatching OIDC claim → should be 403
        with patch.object(
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=_make_oidc_result("attacker/evil"),
        ):
            body = make_encrypted_output_request(
                {"oidc_token": "valid.oidc.token", "offset": 0}, ctx.shared_key
            )
            mismatch_response = client.post(
                f"/execution/{execution_id}/output", json=body
            )

        assert mismatch_response.status_code == 403
        detail = mismatch_response.json().get("detail", {})
        assert detail.get("error") == "repository_mismatch"
