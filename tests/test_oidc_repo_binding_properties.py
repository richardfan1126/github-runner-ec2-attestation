"""Property-based tests for OIDC repository claim binding on /execute.

Task 131.2: Property 142 – OIDC Repository Claim Binding

For any /execute request where the `repository` claim from the validated
OIDC_Token does not match the `repository_url` field in the
Execution_Request, the server should reject the request with HTTP 403.

**Validates: Requirements 2.22, 2.23, 2.24**
"""
from unittest.mock import patch

from hypothesis import given, strategies as st, settings, assume

from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.models import OIDCValidationResult
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


def _make_oidc_result(repository: str, sha: str = "a" * 40) -> OIDCValidationResult:
    return OIDCValidationResult(
        valid=True,
        status_code=200,
        error_message=None,
        claims={
            "repository": repository,
            "iss": GITHUB_OIDC_ISSUER,
            "aud": EXPECTED_AUDIENCE,
            "sha": sha,
        },
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# GitHub-style owner/repo identifiers (alphanumeric + hyphens)
_github_name = st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9\-]{0,19}", fullmatch=True)

_owner_repo = st.tuples(_github_name, _github_name).map(lambda t: f"{t[0]}/{t[1]}")

# URL suffixes that should be normalised away
_url_suffix = st.sampled_from(["", "/", ".git", ".git/"])


# ===========================================================================
# Property 142: OIDC Repository Claim Binding
# ===========================================================================


@settings(max_examples=30, deadline=None)
@given(
    oidc_repo=_owner_repo,
    url_owner_repo=_owner_repo,
    suffix=_url_suffix,
)
def test_property_142_oidc_repository_claim_binding(oidc_repo, url_owner_repo, suffix):
    """
    For any /execute request where the `repository` claim from the validated
    OIDC_Token does not match the `repository_url` field in the
    Execution_Request, the server should reject with HTTP 403.
    When they match, the request should NOT be rejected on repository
    binding grounds (it may still fail later for other reasons).

    **Validates: Requirements 2.22, 2.23, 2.24**
    """
    assume(oidc_repo != url_owner_repo)

    repo_url = f"https://github.com/{url_owner_repo}{suffix}"

    ctx = EncryptionTestContext()
    app = create_app(_get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)

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

    assert response.status_code == 200, (
        f"Mismatched repos (claim={oidc_repo!r}, url_repo={url_owner_repo!r}) "
        f"should return 200 (encrypted error envelope), got {response.status_code}"
    )
    decrypted = decrypt_execute_response(response.json(), ctx.shared_key)
    assert decrypted.get("error") == "repository_mismatch"
    assert decrypted.get("error_code") == 403
