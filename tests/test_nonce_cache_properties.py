"""Property-based tests for Anti-Replay Nonce Validation.

Task 144.4: Property 154 – Anti-Replay Nonce Validation

For any encrypted /execute or /execution/{id}/output request whose nonce has
been previously seen in the Nonce_Cache, the server should reject the request
with HTTP 400 Bad Request. Nonce cache entries should expire after a
configurable TTL matching the OIDC_Token lifetime.

**Validates: Requirements 45.1, 45.2, 45.3, 45.4, 45.5**
"""
import time
from datetime import datetime, timezone
from unittest.mock import patch, Mock

from hypothesis import given, strategies as st, settings, assume

from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.models import OIDCValidationResult, AttestationDocument, CloneResult
from src.nonce_cache import NonceCache
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


def _get_test_config(nonce_ttl: int = 300) -> ServerConfig:
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
        nonce_cache_ttl_seconds=nonce_ttl,
    )


def _make_oidc_result(repository: str = "owner/repo") -> OIDCValidationResult:
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


def _create_app_and_client(nonce_ttl: int = 300):
    ctx = EncryptionTestContext()
    app = create_app(_get_test_config(nonce_ttl), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)
    return app, client, ctx


def _send_execute_with_nonce(app, client, ctx, nonce: str):
    """Send an /execute request with a given nonce, mocking downstream deps."""
    request_data = {
        "repository_url": "https://github.com/owner/repo",
        "commit_hash": "a" * 40,
        "script_path": "scripts/test.sh",
        "github_token": "ghp_test",
        "oidc_token": "valid.oidc.token",
        "nonce": nonce,
    }
    with patch.object(
        app.state.request_validator,
        "validate_oidc_token_from_body",
        return_value=_make_oidc_result(),
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
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="scripts/test.sh",
                timestamp=datetime.now(timezone.utc),
                signature=b"sig",
            ),
            None,
        ),
    ), patch.object(app.state.script_executor, "execute_async"):
        body = make_encrypted_execute_request(request_data, ctx)
        return client.post("/execute", json=body)


# Nonce strategy: printable strings of reasonable length
_nonce_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=64,
)


# ===========================================================================
# Property 154: Anti-Replay Nonce Validation
# ===========================================================================


@settings(max_examples=30, deadline=None)
@given(nonce=_nonce_strategy)
def test_property_154_duplicate_nonce_rejected_on_execute(nonce):
    """
    For any encrypted /execute request whose nonce has been previously seen
    in the Nonce_Cache, the server should reject the request with an encrypted
    error envelope (HTTP 200 at transport layer, error_code 400 inside envelope).

    **Validates: Requirements 45.1, 45.2, 45.3, 45.4, 45.5**
    """
    app, client, ctx = _create_app_and_client()

    # First request with this nonce should succeed
    resp1 = _send_execute_with_nonce(app, client, ctx, nonce)
    assert resp1.status_code == 200, (
        f"First request with nonce={nonce!r} should succeed, got {resp1.status_code}"
    )

    # Second request with the same nonce should be rejected as duplicate
    # Post-decryption errors return HTTP 200 with encrypted error envelope
    resp2 = _send_execute_with_nonce(app, client, ctx, nonce)
    assert resp2.status_code == 200, (
        f"Duplicate nonce response should be HTTP 200 (encrypted envelope), got {resp2.status_code}"
    )
    decrypted = decrypt_execute_response(resp2.json(), ctx.shared_key)
    assert decrypted.get("error") == "duplicate_nonce", (
        f"Duplicate nonce={nonce!r} should return duplicate_nonce error, got {decrypted}"
    )
    assert decrypted.get("error_code") == 400


@settings(max_examples=30, deadline=None)
@given(nonce1=_nonce_strategy, nonce2=_nonce_strategy)
def test_property_154_distinct_nonces_both_accepted(nonce1, nonce2):
    """
    For any two distinct nonces, both should be accepted on /execute.

    **Validates: Requirements 45.1, 45.2**
    """
    assume(nonce1 != nonce2)

    app, client, ctx = _create_app_and_client()

    resp1 = _send_execute_with_nonce(app, client, ctx, nonce1)
    assert resp1.status_code == 200

    resp2 = _send_execute_with_nonce(app, client, ctx, nonce2)
    assert resp2.status_code == 200


@settings(max_examples=10, deadline=None)
@given(nonce=_nonce_strategy)
def test_property_154_nonce_cache_ttl_expiry(nonce):
    """
    Nonce cache entries should expire after the configured TTL.
    After expiry, the same nonce should be accepted again.

    **Validates: Requirements 45.4**
    """
    # Use a very short TTL for testing
    cache = NonceCache(ttl_seconds=1)

    # Store the nonce
    assert cache.check_and_store(nonce) is True
    # Duplicate should be rejected
    assert cache.check_and_store(nonce) is False

    # Wait for TTL to expire
    time.sleep(1.1)

    # After expiry, the nonce should be accepted again
    assert cache.check_and_store(nonce) is True
