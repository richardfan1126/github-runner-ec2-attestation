"""Property-based tests for branch and protected ref restrictions

Feature: github-actions-remote-executor
Tests Properties 143, 144 from the design document

Branch restriction enforcement and protected ref enforcement for OIDC tokens.
"""
import base64
import time
from unittest.mock import patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from hypothesis import given, strategies as st, settings, assume

from src.validation import RequestValidator, GITHUB_OIDC_ISSUER


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_oidc_validation_properties.py)
# ---------------------------------------------------------------------------

def _generate_rsa_key_pair():
    """Generate an RSA private key and return (private_key, public_key)."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return private_key, private_key.public_key()


def _int_to_base64url(n: int) -> str:
    byte_length = (n.bit_length() + 7) // 8
    raw = n.to_bytes(byte_length, byteorder="big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _build_jwks(public_key, kid: str = "test-key-id") -> dict:
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
    now = int(time.time())
    payload = {
        "iss": GITHUB_OIDC_ISSUER,
        "aud": "https://example.com",
        "repository": "owner/repo",
        "exp": now + 3600,
        "sub": "repo:owner/repo:ref:refs/heads/main",
        "ref": "refs/heads/main",
        "ref_protected": "true",
    }
    payload.update(claim_overrides)
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


_PRIVATE_KEY, _PUBLIC_KEY = _generate_rsa_key_pair()
_KID = "test-key-id"
_JWKS = _build_jwks(_PUBLIC_KEY, _KID)

ALLOWED_REPOS = ["owner/repo"]
EXPECTED_AUDIENCE = "https://example.com"


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Branch ref strings that do NOT match "refs/heads/main"
_non_matching_refs = st.text(min_size=1, max_size=100).filter(
    lambda s: s != "refs/heads/main"
)

# Arbitrary ref strings
_any_ref = st.text(min_size=0, max_size=100)

# ref_protected values that are NOT "true"
_non_true_ref_protected = st.text(min_size=0, max_size=50).filter(
    lambda s: s != "true"
)

# Arbitrary ref_protected values
_any_ref_protected = st.text(min_size=0, max_size=50)


# ===========================================================================
# Property 143: Branch Restriction Enforcement
# ===========================================================================

@settings(max_examples=20, deadline=None)
@given(bad_ref=_non_matching_refs)
def test_property_143_branch_restriction_non_matching_rejected(bad_ref):
    """
    When allowed_branches is configured, tokens with non-matching ref claims
    are rejected with 403.

    **Validates: Requirements 2.25, 2.26, 2.27**
    """
    validator = RequestValidator(
        allowed_repositories=ALLOWED_REPOS,
        expected_audience=EXPECTED_AUDIENCE,
        allowed_branches=["refs/heads/main"],
        require_protected_ref=False,
    )
    token = _make_token(_PRIVATE_KEY, ref=bad_ref)

    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert not result.valid, f"Token with ref={bad_ref!r} should be rejected"
    assert result.status_code == 403, (
        f"Expected 403 for non-matching branch, got {result.status_code}"
    )


@settings(max_examples=20, deadline=None)
@given(st.just(True))
def test_property_143_branch_restriction_matching_accepted(_dummy):
    """
    When allowed_branches is configured, tokens with matching ref claims
    are accepted.

    **Validates: Requirements 2.25, 2.26, 2.27**
    """
    validator = RequestValidator(
        allowed_repositories=ALLOWED_REPOS,
        expected_audience=EXPECTED_AUDIENCE,
        allowed_branches=["refs/heads/main", "refs/heads/release-*"],
        require_protected_ref=False,
    )
    token = _make_token(_PRIVATE_KEY, ref="refs/heads/main")

    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert result.valid, f"Token with matching ref should be accepted, got error: {result.error_message}"
    assert result.status_code == 200


@settings(max_examples=20, deadline=None)
@given(any_ref=_any_ref)
def test_property_143_branch_restriction_not_configured_any_accepted(any_ref):
    """
    When allowed_branches is not configured, any ref is accepted.

    **Validates: Requirements 2.31**
    """
    validator = RequestValidator(
        allowed_repositories=ALLOWED_REPOS,
        expected_audience=EXPECTED_AUDIENCE,
        allowed_branches=None,
        require_protected_ref=False,
    )
    token = _make_token(_PRIVATE_KEY, ref=any_ref)

    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert result.valid, f"Token with any ref should be accepted when branches not configured, got error: {result.error_message}"
    assert result.status_code == 200


@settings(max_examples=20, deadline=None)
@given(st.just(True))
def test_property_143_branch_restriction_wildcard_pattern(_dummy):
    """
    When allowed_branches uses wildcard patterns, matching refs are accepted.

    **Validates: Requirements 2.25, 2.26, 2.27**
    """
    validator = RequestValidator(
        allowed_repositories=ALLOWED_REPOS,
        expected_audience=EXPECTED_AUDIENCE,
        allowed_branches=["refs/heads/*"],
        require_protected_ref=False,
    )
    token = _make_token(_PRIVATE_KEY, ref="refs/heads/feature-branch")

    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert result.valid, f"Wildcard pattern should match, got error: {result.error_message}"
    assert result.status_code == 200


# ===========================================================================
# Property 144: Protected Ref Enforcement
# ===========================================================================

@settings(max_examples=20, deadline=None)
@given(bad_ref_protected=_non_true_ref_protected)
def test_property_144_protected_ref_non_true_rejected(bad_ref_protected):
    """
    When require_protected_ref is True, tokens with ref_protected != "true"
    are rejected with 403.

    **Validates: Requirements 2.28, 2.29, 2.30**
    """
    validator = RequestValidator(
        allowed_repositories=ALLOWED_REPOS,
        expected_audience=EXPECTED_AUDIENCE,
        allowed_branches=None,
        require_protected_ref=True,
    )
    token = _make_token(_PRIVATE_KEY, ref_protected=bad_ref_protected)

    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert not result.valid, f"Token with ref_protected={bad_ref_protected!r} should be rejected"
    assert result.status_code == 403, (
        f"Expected 403 for non-true ref_protected, got {result.status_code}"
    )


@settings(max_examples=20, deadline=None)
@given(st.just(True))
def test_property_144_protected_ref_true_accepted(_dummy):
    """
    When require_protected_ref is True, tokens with ref_protected == "true"
    are accepted.

    **Validates: Requirements 2.28, 2.29, 2.30**
    """
    validator = RequestValidator(
        allowed_repositories=ALLOWED_REPOS,
        expected_audience=EXPECTED_AUDIENCE,
        allowed_branches=None,
        require_protected_ref=True,
    )
    token = _make_token(_PRIVATE_KEY, ref_protected="true")

    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert result.valid, f"Token with ref_protected='true' should be accepted, got error: {result.error_message}"
    assert result.status_code == 200


@settings(max_examples=20, deadline=None)
@given(any_ref_protected=_any_ref_protected)
def test_property_144_protected_ref_not_required_any_accepted(any_ref_protected):
    """
    When require_protected_ref is False, any ref_protected value is accepted.

    **Validates: Requirements 2.32**
    """
    validator = RequestValidator(
        allowed_repositories=ALLOWED_REPOS,
        expected_audience=EXPECTED_AUDIENCE,
        allowed_branches=None,
        require_protected_ref=False,
    )
    token = _make_token(_PRIVATE_KEY, ref_protected=any_ref_protected)

    with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
        result = validator.validate_oidc_token_from_body(token)

    assert result.valid, f"Token should be accepted when require_protected_ref is False, got error: {result.error_message}"
    assert result.status_code == 200
