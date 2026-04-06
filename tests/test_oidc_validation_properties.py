"""Property-based tests for OIDC token validation

Feature: github-actions-remote-executor
Tests Properties 104, 105, 106, 107, 108, 8, 10 from the design document

OIDC tokens are now transmitted inside HPKE-encrypted request bodies
(the ``oidc_token`` field) rather than via the Authorization header.
Validator-level tests therefore call ``validate_oidc_token_from_body``
which accepts the raw JWT string directly.
"""
import base64
import time
from unittest.mock import patch, MagicMock

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from hypothesis import given, strategies as st, settings, assume

from src.validation import RequestValidator, GITHUB_OIDC_ISSUER
from src.config import ServerConfig
from src.models import OIDCValidationResult
from src.server import create_app
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers: RSA key generation and JWKS construction
# ---------------------------------------------------------------------------

def _generate_rsa_key_pair():
    """Generate an RSA private key and return (private_key, public_key)."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return private_key, private_key.public_key()


def _int_to_base64url(n: int) -> str:
    """Encode an integer as a base64url string (no padding)."""
    byte_length = (n.bit_length() + 7) // 8
    raw = n.to_bytes(byte_length, byteorder="big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _build_jwks(public_key, kid: str = "test-key-id") -> dict:
    """Build a JWKS dict from an RSA public key."""
    numbers = public_key.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "kid": kid,
                "n": _int_to_base64url(numbers.n),
                "e": _int_to_base64url(numbers.e),
            }
        ]
    }


def _make_token(private_key, kid: str = "test-key-id", **claim_overrides) -> str:
    """Create a signed JWT with sensible defaults, overridden by kwargs."""
    now = int(time.time())
    payload = {
        "iss": GITHUB_OIDC_ISSUER,
        "aud": "https://example.com",
        "repository": "owner/repo",
        "exp": now + 3600,
        "sub": "repo:owner/repo:ref:refs/heads/main",
    }
    payload.update(claim_overrides)
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


# Shared key pair used by most tests (generated once at module level)
_PRIVATE_KEY, _PUBLIC_KEY = _generate_rsa_key_pair()
_KID = "test-key-id"
_JWKS = _build_jwks(_PUBLIC_KEY, _KID)

ALLOWED_REPOS = ["owner/repo"]
EXPECTED_AUDIENCE = "https://example.com"


def _make_validator() -> RequestValidator:
    return RequestValidator(
        allowed_repositories=ALLOWED_REPOS,
        expected_audience=EXPECTED_AUDIENCE,
    )


# ---------------------------------------------------------------------------
# Test helpers for server-level tests
# ---------------------------------------------------------------------------

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


from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_execute_request,
    make_encrypted_output_request,
)

_server_ctx = EncryptionTestContext()
_server_app = create_app(_get_test_config(), encryption_manager=_server_ctx.encryption_manager)
_server_client = TestClient(_server_app)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Arbitrary issuer strings that are NOT the GitHub OIDC issuer
_wrong_issuer = st.text(min_size=1, max_size=200).filter(
    lambda s: s != GITHUB_OIDC_ISSUER
)

# Arbitrary audience strings that are NOT the expected audience
_wrong_audience = st.text(min_size=1, max_size=200).filter(
    lambda s: s != EXPECTED_AUDIENCE
)

# Repository strings NOT in the allowed list
_wrong_repo = st.text(min_size=1, max_size=200).filter(
    lambda s: s not in ALLOWED_REPOS
)

# Past expiration timestamps (at least 60 s in the past to avoid clock skew)
_past_exp = st.integers(min_value=0, max_value=int(time.time()) - 60)


# ===========================================================================
# Property 104: OIDC Issuer Claim Validation
# ===========================================================================

@settings(max_examples=20, deadline=None)
@given(bad_issuer=_wrong_issuer)
def test_property_104_oidc_issuer_claim_validation(bad_issuer):
    """
    For any OIDC token whose `iss` claim does NOT match
    https://token.actions.githubusercontent.com, the Request Validator
    SHALL reject the request with HTTP 401.

    **Validates: Requirements 2.7, 2.8**
    """
    token = _make_token(_PRIVATE_KEY, iss=bad_issuer)
    validator = _make_validator()

    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert not result.valid, f"Token with iss={bad_issuer!r} should be rejected"
    assert result.status_code == 401, (
        f"Expected 401 for wrong issuer, got {result.status_code}"
    )


# ===========================================================================
# Property 105: OIDC Audience Claim Validation
# ===========================================================================

@settings(max_examples=20, deadline=None)
@given(bad_audience=_wrong_audience)
def test_property_105_oidc_audience_claim_validation(bad_audience):
    """
    For any OIDC token whose `aud` claim does NOT match the configured
    Expected_Audience, the Request Validator SHALL reject with HTTP 401.

    **Validates: Requirements 2.9, 2.10**
    """
    token = _make_token(_PRIVATE_KEY, aud=bad_audience)
    validator = _make_validator()

    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert not result.valid, f"Token with aud={bad_audience!r} should be rejected"
    assert result.status_code == 401, (
        f"Expected 401 for wrong audience, got {result.status_code}"
    )


# ===========================================================================
# Property 106: OIDC Repository Authorization
# ===========================================================================

@settings(max_examples=20, deadline=None)
@given(bad_repo=_wrong_repo)
def test_property_106_oidc_repository_authorization(bad_repo):
    """
    For any OIDC token whose `repository` claim is NOT in the
    Allowed_Repositories list, the Request Validator SHALL reject
    with HTTP 403.

    **Validates: Requirements 2.11, 2.12**
    """
    token = _make_token(_PRIVATE_KEY, repository=bad_repo)
    validator = _make_validator()

    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert not result.valid, f"Token with repository={bad_repo!r} should be rejected"
    assert result.status_code == 403, (
        f"Expected 403 for unauthorized repo, got {result.status_code}"
    )


# ===========================================================================
# Property 107: OIDC Token Expiration Validation
# ===========================================================================

@settings(max_examples=20, deadline=None)
@given(expired_exp=_past_exp)
def test_property_107_oidc_token_expiration_validation(expired_exp):
    """
    For any OIDC token with an `exp` claim in the past, the Request
    Validator SHALL reject with HTTP 401.

    **Validates: Requirements 2.13, 2.14**
    """
    token = _make_token(_PRIVATE_KEY, exp=expired_exp)
    validator = _make_validator()

    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert not result.valid, f"Expired token (exp={expired_exp}) should be rejected"
    assert result.status_code == 401, (
        f"Expected 401 for expired token, got {result.status_code}"
    )


# ===========================================================================
# Property 108: Health Endpoint No Authentication
# ===========================================================================

@settings(max_examples=20, deadline=None)
@given(st.just(True))  # dummy strategy to satisfy @given; each run hits /health
def test_property_108_health_endpoint_no_authentication(_dummy):
    """
    Requests to /health SHALL receive HTTP 200 without requiring
    authentication (no OIDC token needed).

    **Validates: Requirements 2.20**
    """
    with patch.object(
        _server_app.state.attestation_generator,
        "verify_tpm_available",
        return_value=False,
    ), patch("shutil.disk_usage") as mock_disk, patch.object(
        _server_app.state.execution_manager,
        "get_active_count",
        return_value=0,
    ):
        mock_disk.return_value = MagicMock(free=1024 * 1024 * 1024)
        response = _server_client.get("/health")

    assert response.status_code == 200, (
        f"Health endpoint should return 200 without auth, got {response.status_code}"
    )
    data = response.json()
    assert "status" in data


# ===========================================================================
# Property 8: OIDC Token Required on Protected Endpoints
# ===========================================================================

@settings(max_examples=20, deadline=None)
@given(execution_id=st.uuids().map(str))
def test_property_008_oidc_token_required_on_protected_endpoints(execution_id):
    """
    Requests to /execute and /execution/{id}/output WITHOUT an
    oidc_token in the encrypted request body SHALL be rejected with HTTP 401.

    **Validates: Requirements 2.1, 2.2, 2.3**
    """
    # POST /execute without oidc_token in encrypted body
    req_data = {
        "repository_url": "https://github.com/owner/repo",
        "commit_hash": "a" * 40,
        "script_path": "test.sh",
        "github_token": "ghp_fake",
    }
    body = make_encrypted_execute_request(req_data, _server_ctx)
    resp_execute = _server_client.post("/execute", json=body)
    assert resp_execute.status_code == 401, (
        f"/execute without oidc_token should return 401, got {resp_execute.status_code}"
    )

    # POST /execution/{id}/output without oidc_token in encrypted body
    _server_ctx.encryption_manager.store_encryption_context(execution_id, _server_ctx.shared_key)
    output_body = make_encrypted_output_request({"offset": 0}, _server_ctx.shared_key)
    resp_output = _server_client.post(f"/execution/{execution_id}/output", json=output_body)
    assert resp_output.status_code in [401, 404], (
        f"/execution/{{id}}/output without oidc_token should return 401 or 404, got {resp_output.status_code}"
    )


# ===========================================================================
# Property 10: OIDC Token Signature Verification
# ===========================================================================

@settings(max_examples=20, deadline=None)
@given(st.just(True))  # dummy strategy
def test_property_010_oidc_token_signature_verification(_dummy):
    """
    For any OIDC token signed with a key that does NOT match the JWKS,
    the Request Validator SHALL reject with HTTP 401.

    **Validates: Requirements 2.4, 2.6**
    """
    # Generate a DIFFERENT key pair for signing
    bad_private, _bad_public = _generate_rsa_key_pair()

    # Sign the token with the bad key but use the same kid
    token = _make_token(bad_private, kid=_KID)

    # The validator's JWKS still contains the ORIGINAL public key
    validator = _make_validator()
    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert not result.valid, "Token signed with wrong key should be rejected"
    assert result.status_code == 401, (
        f"Expected 401 for bad signature, got {result.status_code}"
    )
