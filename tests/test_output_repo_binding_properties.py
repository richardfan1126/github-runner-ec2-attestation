"""Property-based tests for execution output repository binding.

Task 136.3: Property 147 – Execution Output Repository Binding

For any /execution/{id}/output request where the `repository` claim from
the validated OIDC_Token does not match the repository stored in the
execution record, the server should reject the request with HTTP 403.

**Validates: Requirements 6.14, 6.15, 6.16**
"""
from datetime import datetime, timezone
from unittest.mock import patch, Mock

from hypothesis import given, strategies as st, settings, assume

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


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_github_name = st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9\-]{0,19}", fullmatch=True)
_owner_repo = st.tuples(_github_name, _github_name).map(lambda t: f"{t[0]}/{t[1]}")


# ===========================================================================
# Property 147: Execution Output Repository Binding
# ===========================================================================


@settings(max_examples=30, deadline=None)
@given(
    oidc_repo=_owner_repo,
    record_repo=_owner_repo,
)
def test_property_147_execution_output_repository_binding(oidc_repo, record_repo):
    """
    For any /execution/{id}/output request where the `repository` claim
    from the validated OIDC_Token does not match the repository stored in
    the execution record, the server should reject with HTTP 403.
    When they match, the request should NOT be rejected on repository
    binding grounds.

    **Validates: Requirements 6.14, 6.15, 6.16**
    """
    assume(oidc_repo != record_repo)

    ctx = EncryptionTestContext()
    app = create_app(_get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)

    execution_id = "test-prop-147"
    ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

    record = ExecutionRecord(
        execution_id=execution_id,
        repository_url=f"https://github.com/{record_repo}",
        commit_hash="a" * 40,
        script_path="scripts/test.sh",
        status=ExecutionStatus.RUNNING,
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        exit_code=None,
        timeout_seconds=300,
        repository=record_repo,
    )

    with patch.object(
        app.state.request_validator,
        "validate_oidc_token_from_body",
        return_value=_make_oidc_result(oidc_repo),
    ), patch.object(
        app.state.execution_manager,
        "get_execution",
        return_value=record,
    ):
        body = make_encrypted_output_request(
            {"oidc_token": "valid.oidc.token", "offset": 0}, ctx.shared_key
        )
        response = client.post(f"/execution/{execution_id}/output", json=body)

    assert response.status_code == 403, (
        f"Mismatched repos (claim={oidc_repo!r}, record={record_repo!r}) "
        f"should return 403, got {response.status_code}"
    )
    detail = response.json().get("detail", {})
    assert detail.get("error") == "repository_mismatch"
