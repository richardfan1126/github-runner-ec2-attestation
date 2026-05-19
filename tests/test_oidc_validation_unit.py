"""Unit tests for OIDC token validation and OIDC-protected endpoints.

Task 63: Write unit tests for OIDC validation
  63.1 – Unit tests for OIDC token validation (RequestValidator)
  63.2 – Unit tests for OIDC-protected endpoints (server integration)

Requirements: 2.1-2.14, 2.20
"""
import base64
import time
from unittest.mock import patch, Mock
from datetime import datetime, timezone

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from src.validation import RequestValidator, GITHUB_OIDC_ISSUER, GITHUB_OIDC_JWKS_URL
from src.config import ServerConfig
from src.models import OIDCValidationResult, ExecutionRecord, ExecutionStatus, OutputData, AttestationDocument, CloneResult
from src.server import create_app


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
    }
    payload.update(claim_overrides)
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


# Module-level key pair
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


# ===========================================================================
# 63.1 – Unit tests for OIDC token validation (RequestValidator)
# ===========================================================================


class TestOIDCTokenValidation:
    """Unit tests for RequestValidator.validate_oidc_token"""

    def test_missing_authorization_header_returns_401(self):
        """Missing Authorization header → 401"""
        validator = _make_validator()
        result = validator.validate_oidc_token(None)

        assert not result.valid
        assert result.status_code == 401
        assert "required" in result.error_message.lower()

    def test_malformed_authorization_header_returns_401(self):
        """Authorization header not in 'Bearer <token>' format → 401"""
        validator = _make_validator()

        for bad_header in ["Basic abc123", "Token xyz", "bearer-only", "", "Bearer"]:
            result = validator.validate_oidc_token(bad_header)
            assert not result.valid, f"Should reject header: {bad_header!r}"
            assert result.status_code == 401

    def test_valid_token_with_correct_claims_returns_success(self):
        """A properly signed token with all correct claims → 200"""
        token = _make_token(_PRIVATE_KEY)
        validator = _make_validator()

        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token(f"Bearer {token}")

        assert result.valid
        assert result.status_code == 200
        assert result.error_message is None
        assert result.claims is not None
        assert result.claims["repository"] == "owner/repo"
        assert result.claims["iss"] == GITHUB_OIDC_ISSUER
        assert result.claims["aud"] == EXPECTED_AUDIENCE

    def test_wrong_issuer_returns_401(self):
        """Token with incorrect `iss` claim → 401"""
        token = _make_token(_PRIVATE_KEY, iss="https://evil.example.com")
        validator = _make_validator()

        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token(f"Bearer {token}")

        assert not result.valid
        assert result.status_code == 401
        assert "issuer" in result.error_message.lower()

    def test_wrong_audience_returns_401(self):
        """Token with incorrect `aud` claim → 401"""
        token = _make_token(_PRIVATE_KEY, aud="https://wrong-audience.example.com")
        validator = _make_validator()

        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token(f"Bearer {token}")

        assert not result.valid
        assert result.status_code == 401
        assert "audience" in result.error_message.lower()

    def test_unauthorized_repository_returns_403(self):
        """Token from a repo not in allowed list → 403"""
        token = _make_token(_PRIVATE_KEY, repository="evil-org/evil-repo")
        validator = _make_validator()

        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token(f"Bearer {token}")

        assert not result.valid
        assert result.status_code == 403
        assert "not authorized" in result.error_message.lower()

    def test_expired_token_returns_401(self):
        """Token with `exp` in the past → 401"""
        token = _make_token(_PRIVATE_KEY, exp=int(time.time()) - 3600)
        validator = _make_validator()

        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token(f"Bearer {token}")

        assert not result.valid
        assert result.status_code == 401
        assert "expired" in result.error_message.lower()

    def test_token_signed_with_wrong_key_returns_401(self):
        """Token signed with a different RSA key → 401"""
        bad_private, _ = _generate_rsa_key_pair()
        token = _make_token(bad_private, kid=_KID)
        validator = _make_validator()

        with patch.object(validator, "_fetch_jwks", return_value=_JWKS):
            result = validator.validate_oidc_token(f"Bearer {token}")

        assert not result.valid
        assert result.status_code == 401

    def test_jwks_cache_refresh_on_unknown_kid(self):
        """When kid is not in cached JWKS, validator refreshes and retries."""
        new_kid = "new-key-id"
        new_private, new_public = _generate_rsa_key_pair()
        new_jwks = _build_jwks(new_public, new_kid)
        token = _make_token(new_private, kid=new_kid)

        validator = _make_validator()
        # First call returns old JWKS (no matching kid), second returns new JWKS
        with patch.object(
            validator,
            "_fetch_jwks",
            side_effect=[_JWKS, new_jwks],
        ):
            result = validator.validate_oidc_token(f"Bearer {token}")

        assert result.valid
        assert result.status_code == 200

    def test_jwks_fetch_failure_returns_401(self):
        """If JWKS endpoint is unreachable, validation fails with 401."""
        token = _make_token(_PRIVATE_KEY)
        validator = _make_validator()

        with patch.object(
            validator,
            "_fetch_jwks",
            side_effect=ConnectionError("network error"),
        ):
            result = validator.validate_oidc_token(f"Bearer {token}")

        assert not result.valid
        assert result.status_code == 401


# ===========================================================================
# 63.2 – Unit tests for OIDC-protected endpoints
# ===========================================================================

VALID_OIDC_RESULT = OIDCValidationResult(
    valid=True,
    status_code=200,
    error_message=None,
    claims={
        "repository": "owner/repo",
        "iss": GITHUB_OIDC_ISSUER,
        "aud": EXPECTED_AUDIENCE,
        "sha": "a" * 40,
    },
)

UNAUTHORIZED_OIDC_RESULT = OIDCValidationResult(
    valid=False,
    status_code=401,
    error_message="Authorization header is required",
    claims=None,
)

INVALID_TOKEN_OIDC_RESULT = OIDCValidationResult(
    valid=False,
    status_code=401,
    error_message="Token signature verification failed",
    claims=None,
)

FORBIDDEN_OIDC_RESULT = OIDCValidationResult(
    valid=False,
    status_code=403,
    error_message="Repository not authorized: evil-org/evil-repo",
    claims=None,
)


class TestOIDCProtectedEndpoints:
    """Unit tests for OIDC enforcement on server endpoints"""

    def _create_client(self):
        from tests.encryption_test_helpers import EncryptionTestContext
        ctx = EncryptionTestContext()
        app = create_app(_get_test_config(), encryption_manager=ctx.encryption_manager)
        return TestClient(app), app, ctx

    # ---- POST /execute ----

    def test_execute_without_auth_header_returns_401(self):
        """POST /execute with no oidc_token in encrypted body → encrypted 401 error envelope"""
        from tests.encryption_test_helpers import make_encrypted_execute_request, decrypt_execute_response
        client, app, ctx = self._create_client()

        # Send encrypted request WITHOUT oidc_token field
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "test.sh",
            "github_token": "ghp_fake",
        }
        body = make_encrypted_execute_request(request_data, ctx)
        response = client.post("/execute", json=body)
        # Post-decryption errors return HTTP 200 with encrypted error envelope
        assert response.status_code == 200
        decrypted = decrypt_execute_response(response.json(), ctx.shared_key)
        assert decrypted["error_code"] == 401

    def test_execute_with_invalid_token_returns_401(self):
        """POST /execute with an invalid/bad-signature token → encrypted 401 error envelope"""
        from tests.encryption_test_helpers import make_encrypted_execute_request, decrypt_execute_response
        client, app, ctx = self._create_client()

        with patch.object(
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=INVALID_TOKEN_OIDC_RESULT,
        ):
            request_data = {
                "repository_url": "https://github.com/owner/repo",
                "commit_hash": "a" * 40,
                "script_path": "test.sh",
                "github_token": "ghp_fake",
                "oidc_token": "bad.token.here",
            }
            body = make_encrypted_execute_request(request_data, ctx)
            response = client.post("/execute", json=body)

        # Post-decryption errors return HTTP 200 with encrypted error envelope
        assert response.status_code == 200
        decrypted = decrypt_execute_response(response.json(), ctx.shared_key)
        assert decrypted["error_code"] == 401

    def test_execute_with_unauthorized_repo_returns_403(self):
        """POST /execute with token from unauthorized repo → encrypted 403 error envelope"""
        from tests.encryption_test_helpers import make_encrypted_execute_request, decrypt_execute_response
        client, app, ctx = self._create_client()

        with patch.object(
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=FORBIDDEN_OIDC_RESULT,
        ):
            request_data = {
                "repository_url": "https://github.com/owner/repo",
                "commit_hash": "a" * 40,
                "script_path": "test.sh",
                "github_token": "ghp_fake",
                "oidc_token": "valid.but.forbidden",
            }
            body = make_encrypted_execute_request(request_data, ctx)
            response = client.post("/execute", json=body)

        # Post-decryption errors return HTTP 200 with encrypted error envelope
        assert response.status_code == 200
        decrypted = decrypt_execute_response(response.json(), ctx.shared_key)
        assert decrypted["error_code"] == 403

    def test_execute_with_valid_token_proceeds(self):
        """POST /execute with valid OIDC token proceeds to execution flow"""
        from tests.encryption_test_helpers import make_encrypted_execute_request, decrypt_execute_response
        client, app, ctx = self._create_client()

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test",
            "oidc_token": "valid.oidc.token",
        }

        with patch.object(
            app.state.request_validator, "validate_oidc_token_from_body", return_value=VALID_OIDC_RESULT
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
            return_value=CloneResult(clone_path="/tmp/clone_oidc", script_path=""),
        ), patch.object(
            app.state.repository_client,
            "validate_script_exists",
            return_value=True,
        ), patch(
            "os.path.getsize",
            return_value=50,
        ), patch.object(
            app.state.attestation_generator,
            "generate_attestation",
            return_value=(
                AttestationDocument(
                    repository_url=request_data["repository_url"],
                    commit_hash=request_data["commit_hash"],
                    script_path=request_data["script_path"],
                    timestamp=datetime.now(timezone.utc),
                    signature=b"sig",
                ),
                None,
            ),
        ), patch.object(app.state.script_executor, "execute_async"):
            body = make_encrypted_execute_request(request_data, ctx)
            response = client.post("/execute", json=body)

        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), ctx.shared_key)
        assert "execution_id" in data
        assert data["status"] == "queued"

    # ---- POST /execution/{id}/output ----

    def test_output_without_oidc_token_succeeds_with_shared_key(self):
        """POST /execution/{id}/output without oidc_token succeeds — Shared_Key is the auth"""
        from tests.encryption_test_helpers import make_encrypted_output_request, decrypt_output_response
        client, app, ctx = self._create_client()
        execution_id = "some-id"
        ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

        exec_record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=None,
            exit_code=None,
            timeout_seconds=300,
            repository="owner/repo",
        )
        output_data = OutputData(
            stdout="hello",
            stderr="",
            stdout_offset=5,
            stderr_offset=0,
            complete=False,
            exit_code=None,
        )

        with patch.object(
            app.state.execution_manager, "get_execution", return_value=exec_record
        ), patch.object(
            app.state.output_collector, "get_output", return_value=output_data
        ):
            # Send encrypted request WITHOUT oidc_token — should succeed
            req_body = make_encrypted_output_request({"offset": 0}, ctx.shared_key)
            response = client.post(f"/execution/{execution_id}/output", json=req_body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert data["execution_id"] == execution_id
        assert data["stdout"] == "hello"

    def test_output_with_valid_token_returns_output(self):
        """POST /execution/{id}/output with valid shared key returns execution output"""
        from tests.encryption_test_helpers import make_encrypted_output_request, decrypt_output_response
        client, app, ctx = self._create_client()
        execution_id = "test-exec-id"
        ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

        exec_record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=None,
            exit_code=None,
            timeout_seconds=300,
            repository="owner/repo",
        )
        output_data = OutputData(
            stdout="hello",
            stderr="",
            stdout_offset=5,
            stderr_offset=0,
            complete=False,
            exit_code=None,
        )

        with patch.object(
            app.state.execution_manager, "get_execution", return_value=exec_record
        ), patch.object(
            app.state.output_collector, "get_output", return_value=output_data
        ):
            req_body = make_encrypted_output_request(
                {"offset": 0}, ctx.shared_key
            )
            response = client.post(f"/execution/{execution_id}/output", json=req_body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert data["execution_id"] == execution_id
        assert data["stdout"] == "hello"
        assert data["status"] == "running"

    # ---- GET /health ----

    def test_health_without_auth_returns_200(self):
        """GET /health without any Authorization header → 200"""
        client, app, _ctx = self._create_client()

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("healthy", "unhealthy")
