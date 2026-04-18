"""Unit tests for OIDC repository claim binding on /execute.

Task 131.3: Verify that the `repository` claim from the validated OIDC token
must match the `repository_url` in the Execution_Request.

Requirements: 2.22, 2.23, 2.24
"""
from datetime import datetime, timezone
from unittest.mock import patch, Mock

import pytest
from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.models import OIDCValidationResult, AttestationDocument, CloneResult
from src.server import create_app
from src.validation import GITHUB_OIDC_ISSUER
from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_execute_request,
    decrypt_execute_response,
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
    return TestClient(app), app, ctx


def _execute_with_repo_binding(repo_url: str, oidc_repo: str):
    """Send an /execute request with the given repo URL and OIDC repo claim."""
    client, app, ctx = _create_client()
    request_data = {
        "repository_url": repo_url,
        "commit_hash": "a" * 40,
        "script_path": "scripts/test.sh",
        "github_token": "ghp_test",
        "oidc_token": "valid.oidc.token",
    }
    with patch.object(
        app.state.request_validator,
        "validate_oidc_token_from_body",
        return_value=_make_oidc_result(oidc_repo),
    ):
        body = make_encrypted_execute_request(request_data, ctx)
        response = client.post("/execute", json=body)
    return response, ctx


class TestRepoClaimBindingMatch:
    """Tests where repository claim matches repository_url (should proceed)."""

    def _execute_matching(self, repo_url: str, oidc_repo: str):
        """Execute with matching repos, mocking downstream to allow success."""
        client, app, ctx = _create_client()
        request_data = {
            "repository_url": repo_url,
            "commit_hash": "a" * 40,
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test",
            "oidc_token": "valid.oidc.token",
        }
        with patch.object(
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=_make_oidc_result(oidc_repo),
        ), patch.object(
            app.state.request_validator,
            "validate_execution_request",
            return_value=Mock(valid=True, errors=[]),
        ), patch.object(
            app.state.repository_client, "authenticate",
            return_value=Mock(success=True, error_message=None),
        ), patch.object(
            app.state.repository_client, "clone_repo",
            return_value=CloneResult(clone_path="/tmp/clone", script_path=""),
        ), patch.object(
            app.state.repository_client, "validate_script_exists",
            return_value=True,
        ), patch("os.path.getsize", return_value=50), patch.object(
            app.state.attestation_generator, "generate_attestation",
            return_value=(
                AttestationDocument(
                    repository_url=repo_url,
                    commit_hash="a" * 40,
                    script_path="scripts/test.sh",
                    timestamp=datetime.now(timezone.utc),
                    signature=b"sig",
                ),
                None,
            ),
        ), patch.object(app.state.script_executor, "execute_async"):
            body = make_encrypted_execute_request(request_data, ctx)
            response = client.post("/execute", json=body)
        return response, ctx

    def test_matching_repo_claim_and_url(self):
        """Matching owner/repo claim and https://github.com/owner/repo → 200"""
        response, ctx = self._execute_matching(
            "https://github.com/owner/repo", "owner/repo"
        )
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), ctx.shared_key)
        assert "execution_id" in data

    def test_matching_with_git_suffix(self):
        """URL with .git suffix still matches → 200"""
        response, ctx = self._execute_matching(
            "https://github.com/owner/repo.git", "owner/repo"
        )
        assert response.status_code == 200

    def test_matching_with_trailing_slash(self):
        """URL with trailing slash still matches → 200"""
        response, ctx = self._execute_matching(
            "https://github.com/owner/repo/", "owner/repo"
        )
        assert response.status_code == 200

    def test_matching_with_git_suffix_and_trailing_slash(self):
        """URL with .git/ suffix still matches → 200"""
        response, ctx = self._execute_matching(
            "https://github.com/owner/repo.git/", "owner/repo"
        )
        assert response.status_code == 200


class TestRepoClaimBindingMismatch:
    """Tests where repository claim does NOT match repository_url (should 403)."""

    def test_mismatched_repo_returns_403(self):
        """Different owner/repo in claim vs URL → 403"""
        response, _ = _execute_with_repo_binding(
            "https://github.com/owner/repo", "other-owner/other-repo"
        )
        assert response.status_code == 403

    def test_mismatched_owner_returns_403(self):
        """Same repo name but different owner → 403"""
        response, _ = _execute_with_repo_binding(
            "https://github.com/owner/repo", "evil-owner/repo"
        )
        assert response.status_code == 403

    def test_mismatched_repo_name_returns_403(self):
        """Same owner but different repo name → 403"""
        response, _ = _execute_with_repo_binding(
            "https://github.com/owner/repo", "owner/different-repo"
        )
        assert response.status_code == 403

    def test_mismatch_error_message(self):
        """403 response includes repository_mismatch error code."""
        response, _ = _execute_with_repo_binding(
            "https://github.com/owner/repo", "attacker/malicious"
        )
        assert response.status_code == 403
        detail = response.json().get("detail", {})
        assert detail.get("error") == "repository_mismatch"

    def test_mismatch_with_git_suffix_url(self):
        """Mismatch even when URL has .git suffix → 403"""
        response, _ = _execute_with_repo_binding(
            "https://github.com/owner/repo.git", "other/repo"
        )
        assert response.status_code == 403
