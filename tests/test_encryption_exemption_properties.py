"""Property-based tests for encryption exemption on non-context endpoints.

Feature: github-actions-remote-executor
Tests Property 135 from the design document.
"""
from unittest.mock import patch

from hypothesis import given, settings, strategies as st
from fastapi.testclient import TestClient

from src.server import create_app
from src.config import ServerConfig
from src.encryption import EncryptionManager


def get_test_config():
    """Create test configuration."""
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
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )


def _make_attestation_doc():
    """Helper to create a mock AttestationDocument."""
    from datetime import datetime, timezone
    from src.models import AttestationDocument

    return AttestationDocument(
        repository_url="",
        commit_hash="",
        script_path="",
        timestamp=datetime.now(timezone.utc),
        signature=b"test_signature",
    )


# ---------------------------------------------------------------------------
# Property 135: Encryption Exemption for Non-Context Endpoints
# ---------------------------------------------------------------------------


class TestEncryptionExemptionForNonContextEndpoints:
    """**Validates: Requirements 43.1, 43.2, 43.3, 43.4**"""

    @settings(max_examples=100, deadline=None)
    @given(nonce=st.one_of(st.none(), st.text(min_size=1, max_size=64)))
    def test_attest_returns_plain_json_not_encrypted(self, nonce):
        """Property 135 (attest): /attest response is plain unencrypted JSON
        with no encrypted_response wrapper, even when EncryptionManager is configured."""
        encryption_manager = EncryptionManager()
        app = create_app(get_test_config(), encryption_manager=encryption_manager)
        client = TestClient(app)

        with patch.object(
            app.state.attestation_generator, "generate_attestation"
        ) as mock_attest:
            mock_attest.return_value = (_make_attestation_doc(), None)

            params = {}
            if nonce is not None:
                params["nonce"] = nonce

            response = client.get("/attest", params=params)

            assert response.status_code == 200
            data = response.json()
            # Must be valid JSON (response.json() would raise otherwise)
            # Must contain attestation_document key
            assert "attestation_document" in data
            # Must NOT contain encrypted_response wrapper
            assert "encrypted_response" not in data

    @settings(max_examples=100, deadline=None)
    @given(attestation_available=st.booleans())
    def test_health_returns_plain_json_not_encrypted(self, attestation_available):
        """Property 135 (health): /health response is plain unencrypted JSON
        with no encrypted_response wrapper, even when EncryptionManager is configured."""
        encryption_manager = EncryptionManager()
        app = create_app(get_test_config(), encryption_manager=encryption_manager)
        client = TestClient(app)

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        # Must contain status key
        assert "status" in data
        # Must NOT contain encrypted_response wrapper
        assert "encrypted_response" not in data


