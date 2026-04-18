"""Unit tests for anti-replay nonce cache.

Task 144.5: Unit tests for NonceCache and nonce validation in endpoints.

Requirements: 45.1, 45.2, 45.3, 45.4, 45.5
"""
import time
import threading
from datetime import datetime, timezone
from unittest.mock import patch, Mock

import pytest
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


# ===========================================================================
# NonceCache unit tests
# ===========================================================================


class TestNonceCacheBasic:
    """Basic NonceCache functionality tests."""

    def test_new_nonce_accepted(self):
        """A new nonce should be accepted (returns True)."""
        cache = NonceCache(ttl_seconds=60)
        assert cache.check_and_store("nonce-1") is True

    def test_duplicate_nonce_rejected(self):
        """A duplicate nonce should be rejected (returns False)."""
        cache = NonceCache(ttl_seconds=60)
        assert cache.check_and_store("nonce-1") is True
        assert cache.check_and_store("nonce-1") is False

    def test_different_nonces_both_accepted(self):
        """Different nonces should both be accepted."""
        cache = NonceCache(ttl_seconds=60)
        assert cache.check_and_store("nonce-a") is True
        assert cache.check_and_store("nonce-b") is True

    def test_nonce_expiry_after_ttl(self):
        """Nonce entries should expire after TTL."""
        cache = NonceCache(ttl_seconds=1)
        assert cache.check_and_store("nonce-1") is True
        assert cache.check_and_store("nonce-1") is False

        time.sleep(1.1)

        # After TTL, the nonce should be accepted again
        assert cache.check_and_store("nonce-1") is True

    def test_cleanup_expired_removes_old_entries(self):
        """cleanup_expired should remove entries past TTL."""
        cache = NonceCache(ttl_seconds=1)
        cache.check_and_store("nonce-1")
        cache.check_and_store("nonce-2")
        assert len(cache) == 2

        time.sleep(1.1)

        removed = cache.cleanup_expired()
        assert removed == 2
        assert len(cache) == 0

    def test_cleanup_expired_keeps_fresh_entries(self):
        """cleanup_expired should not remove entries within TTL."""
        cache = NonceCache(ttl_seconds=60)
        cache.check_and_store("nonce-1")
        removed = cache.cleanup_expired()
        assert removed == 0
        assert len(cache) == 1

    def test_invalid_ttl_raises(self):
        """TTL < 1 should raise ValueError."""
        with pytest.raises(ValueError, match="ttl_seconds must be >= 1"):
            NonceCache(ttl_seconds=0)
        with pytest.raises(ValueError, match="ttl_seconds must be >= 1"):
            NonceCache(ttl_seconds=-5)

    def test_ttl_property(self):
        """ttl_seconds property should return configured value."""
        cache = NonceCache(ttl_seconds=42)
        assert cache.ttl_seconds == 42


class TestNonceCacheConcurrency:
    """Thread-safety tests for NonceCache."""

    def test_concurrent_nonce_checks(self):
        """Only one thread should succeed for the same nonce under concurrency."""
        cache = NonceCache(ttl_seconds=60)
        results = []
        barrier = threading.Barrier(10)

        def check_nonce():
            barrier.wait()
            result = cache.check_and_store("shared-nonce")
            results.append(result)

        threads = [threading.Thread(target=check_nonce) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one thread should have succeeded
        assert results.count(True) == 1
        assert results.count(False) == 9


# ===========================================================================
# Endpoint integration tests for nonce validation
# ===========================================================================


class TestExecuteEndpointNonceValidation:
    """Tests for nonce validation on /execute endpoint."""

    def test_first_nonce_accepted(self):
        """First request with a nonce should succeed."""
        app, client, ctx = _create_app_and_client()
        resp = _send_execute_with_nonce(app, client, ctx, "unique-nonce-1")
        assert resp.status_code == 200

    def test_duplicate_nonce_rejected_with_400(self):
        """Second request with the same nonce should return 400."""
        app, client, ctx = _create_app_and_client()
        resp1 = _send_execute_with_nonce(app, client, ctx, "replay-nonce")
        assert resp1.status_code == 200

        resp2 = _send_execute_with_nonce(app, client, ctx, "replay-nonce")
        assert resp2.status_code == 400
        detail = resp2.json().get("detail", {})
        assert detail.get("error") == "duplicate_nonce"

    def test_request_without_nonce_always_accepted(self):
        """Requests without a nonce field should always be accepted."""
        app, client, ctx = _create_app_and_client()

        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test",
            "oidc_token": "valid.oidc.token",
            # No nonce field
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
            resp1 = client.post("/execute", json=body)
            assert resp1.status_code == 200

            # Send again without nonce — should also succeed
            body2 = make_encrypted_execute_request(request_data, ctx)
            resp2 = client.post("/execute", json=body2)
            assert resp2.status_code == 200


class TestOutputEndpointNonceValidation:
    """Tests for nonce validation on /execution/{id}/output endpoint."""

    def _setup_execution(self):
        """Create an app with a valid execution and return components."""
        app, client, ctx = _create_app_and_client()

        # First, create an execution via /execute
        resp = _send_execute_with_nonce(app, client, ctx, "setup-nonce")
        assert resp.status_code == 200
        data = decrypt_execute_response(resp.json(), ctx.shared_key)
        execution_id = data["execution_id"]
        return app, client, ctx, execution_id

    def test_duplicate_nonce_on_output_rejected(self):
        """Duplicate nonce on /output endpoint should return 400."""
        app, client, ctx, execution_id = self._setup_execution()

        output_payload = {
            "oidc_token": "valid.oidc.token",
            "nonce": "output-nonce-1",
            "offset": 0,
        }

        with patch.object(
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=_make_oidc_result(),
        ), patch.object(
            app.state.attestation_generator,
            "generate_output_attestation",
            return_value=(b"attestation-bytes", None),
        ):
            body1 = make_encrypted_output_request(output_payload, ctx.shared_key)
            resp1 = client.post(f"/execution/{execution_id}/output", json=body1)
            assert resp1.status_code == 200

            body2 = make_encrypted_output_request(output_payload, ctx.shared_key)
            resp2 = client.post(f"/execution/{execution_id}/output", json=body2)
            assert resp2.status_code == 400
            detail = resp2.json().get("detail", {})
            assert detail.get("error") == "duplicate_nonce"

    def test_different_nonces_on_output_accepted(self):
        """Different nonces on /output endpoint should both be accepted."""
        app, client, ctx, execution_id = self._setup_execution()

        with patch.object(
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=_make_oidc_result(),
        ), patch.object(
            app.state.attestation_generator,
            "generate_output_attestation",
            return_value=(b"attestation-bytes", None),
        ):
            payload1 = {"oidc_token": "valid.oidc.token", "nonce": "out-nonce-a", "offset": 0}
            body1 = make_encrypted_output_request(payload1, ctx.shared_key)
            resp1 = client.post(f"/execution/{execution_id}/output", json=body1)
            assert resp1.status_code == 200

            payload2 = {"oidc_token": "valid.oidc.token", "nonce": "out-nonce-b", "offset": 0}
            body2 = make_encrypted_output_request(payload2, ctx.shared_key)
            resp2 = client.post(f"/execution/{execution_id}/output", json=body2)
            assert resp2.status_code == 200

    def test_nonce_shared_between_execute_and_output(self):
        """A nonce used on /execute should also be rejected on /output."""
        app, client, ctx = _create_app_and_client()

        # Use a nonce on /execute
        resp = _send_execute_with_nonce(app, client, ctx, "shared-nonce-x")
        assert resp.status_code == 200
        data = decrypt_execute_response(resp.json(), ctx.shared_key)
        execution_id = data["execution_id"]

        # Try the same nonce on /output — should be rejected
        output_payload = {
            "oidc_token": "valid.oidc.token",
            "nonce": "shared-nonce-x",
            "offset": 0,
        }
        with patch.object(
            app.state.request_validator,
            "validate_oidc_token_from_body",
            return_value=_make_oidc_result(),
        ), patch.object(
            app.state.attestation_generator,
            "generate_output_attestation",
            return_value=(b"attestation-bytes", None),
        ):
            body = make_encrypted_output_request(output_payload, ctx.shared_key)
            resp2 = client.post(f"/execution/{execution_id}/output", json=body)
            assert resp2.status_code == 400
            detail = resp2.json().get("detail", {})
            assert detail.get("error") == "duplicate_nonce"
