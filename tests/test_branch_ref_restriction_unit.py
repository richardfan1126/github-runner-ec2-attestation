"""Unit tests for branch and protected ref validation

Feature: github-actions-remote-executor
Tests branch restriction and protected ref enforcement in RequestValidator.
"""
import base64
import time
from unittest.mock import patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.validation import RequestValidator, GITHUB_OIDC_ISSUER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_rsa_key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _int_to_base64url(n: int) -> str:
    byte_length = (n.bit_length() + 7) // 8
    raw = n.to_bytes(byte_length, byteorder="big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _build_jwks(public_key, kid: str = "test-key-id") -> dict:
    numbers = public_key.public_numbers()
    return {
        "keys": [{
            "kty": "RSA", "alg": "RS256", "use": "sig", "kid": kid,
            "n": _int_to_base64url(numbers.n),
            "e": _int_to_base64url(numbers.e),
        }]
    }


def _make_token(private_key, kid="test-key-id", **overrides) -> str:
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
    payload.update(overrides)
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


_PRIVATE_KEY, _PUBLIC_KEY = _generate_rsa_key_pair()
_KID = "test-key-id"
_JWKS = _build_jwks(_PUBLIC_KEY, _KID)
ALLOWED_REPOS = ["owner/repo"]
EXPECTED_AUDIENCE = "https://example.com"


# ===========================================================================
# Branch restriction tests
# ===========================================================================

class TestBranchRestriction:
    """Tests for ALLOWED_BRANCHES validation."""

    def test_matching_ref_allowed(self):
        """When ALLOWED_BRANCHES is configured, a matching ref is allowed."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            allowed_branches=["refs/heads/main"],
        )
        token = _make_token(_PRIVATE_KEY, ref="refs/heads/main")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token_from_body(token)
        assert result.valid
        assert result.status_code == 200

    def test_non_matching_ref_rejected(self):
        """When ALLOWED_BRANCHES is configured, a non-matching ref is rejected with 403."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            allowed_branches=["refs/heads/main"],
        )
        token = _make_token(_PRIVATE_KEY, ref="refs/heads/develop")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token_from_body(token)
        assert not result.valid
        assert result.status_code == 403

    def test_wildcard_pattern_matching(self):
        """Wildcard patterns like refs/heads/* should match any branch."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            allowed_branches=["refs/heads/*"],
        )
        token = _make_token(_PRIVATE_KEY, ref="refs/heads/feature-xyz")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token_from_body(token)
        assert result.valid
        assert result.status_code == 200

    def test_not_configured_any_ref_allowed(self):
        """When ALLOWED_BRANCHES is not configured (None), any ref is allowed."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            allowed_branches=None,
        )
        token = _make_token(_PRIVATE_KEY, ref="refs/heads/anything")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token_from_body(token)
        assert result.valid
        assert result.status_code == 200

    def test_empty_list_any_ref_allowed(self):
        """When ALLOWED_BRANCHES is an empty list, any ref is allowed (treated as not configured)."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            allowed_branches=[],
        )
        token = _make_token(_PRIVATE_KEY, ref="refs/heads/anything")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token_from_body(token)
        assert result.valid
        assert result.status_code == 200

    def test_multiple_patterns(self):
        """Multiple branch patterns: token matching any one should be accepted."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            allowed_branches=["refs/heads/main", "refs/heads/release-*"],
        )
        token = _make_token(_PRIVATE_KEY, ref="refs/heads/release-1.0")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token_from_body(token)
        assert result.valid
        assert result.status_code == 200

    def test_validate_oidc_token_header_branch_check(self):
        """Branch restriction also works via validate_oidc_token (Authorization header)."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            allowed_branches=["refs/heads/main"],
        )
        token = _make_token(_PRIVATE_KEY, ref="refs/heads/develop")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token(f"Bearer {token}")
        assert not result.valid
        assert result.status_code == 403


# ===========================================================================
# Protected ref tests
# ===========================================================================

class TestProtectedRefRestriction:
    """Tests for REQUIRE_PROTECTED_REF validation."""

    def test_ref_protected_true_accepted(self):
        """When REQUIRE_PROTECTED_REF=true, ref_protected='true' is accepted."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            require_protected_ref=True,
        )
        token = _make_token(_PRIVATE_KEY, ref_protected="true")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token_from_body(token)
        assert result.valid
        assert result.status_code == 200

    def test_ref_protected_false_rejected(self):
        """When REQUIRE_PROTECTED_REF=true, ref_protected='false' is rejected with 403."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            require_protected_ref=True,
        )
        token = _make_token(_PRIVATE_KEY, ref_protected="false")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token_from_body(token)
        assert not result.valid
        assert result.status_code == 403

    def test_ref_protected_missing_rejected(self):
        """When REQUIRE_PROTECTED_REF=true and ref_protected is missing, rejected with 403."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            require_protected_ref=True,
        )
        # Create token without ref_protected claim
        now = int(time.time())
        payload = {
            "iss": GITHUB_OIDC_ISSUER,
            "aud": EXPECTED_AUDIENCE,
            "repository": "owner/repo",
            "exp": now + 3600,
            "sub": "repo:owner/repo:ref:refs/heads/main",
            "ref": "refs/heads/main",
        }
        token = pyjwt.encode(payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": _KID})
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token_from_body(token)
        assert not result.valid
        assert result.status_code == 403

    def test_require_protected_ref_false_any_accepted(self):
        """When REQUIRE_PROTECTED_REF=false, any ref_protected value is accepted."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            require_protected_ref=False,
        )
        token = _make_token(_PRIVATE_KEY, ref_protected="false")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token_from_body(token)
        assert result.valid
        assert result.status_code == 200

    def test_require_protected_ref_default_any_accepted(self):
        """When REQUIRE_PROTECTED_REF is not set (default False), any value is accepted."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
        )
        token = _make_token(_PRIVATE_KEY, ref_protected="false")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token_from_body(token)
        assert result.valid
        assert result.status_code == 200

    def test_validate_oidc_token_header_protected_ref_check(self):
        """Protected ref restriction also works via validate_oidc_token (Authorization header)."""
        validator = RequestValidator(
            allowed_repositories=ALLOWED_REPOS,
            expected_audience=EXPECTED_AUDIENCE,
            require_protected_ref=True,
        )
        token = _make_token(_PRIVATE_KEY, ref_protected="false")
        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token(f"Bearer {token}")
        assert not result.valid
        assert result.status_code == 403
