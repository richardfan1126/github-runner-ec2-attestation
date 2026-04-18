"""Property-based tests for encryption exemption on non-context endpoints.

Feature: github-actions-remote-executor
Tests Property 135 from the design document.
"""
from unittest.mock import Mock, patch

from hypothesis import given, settings, strategies as st
from fastapi.testclient import TestClient

from src.server import create_app
from src.config import ServerConfig
from src.encryption import EncryptionManager
from src.models import ExecutionStatus


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

        mock_usage = Mock(free=10240 * 1024 * 1024, total=20480 * 1024 * 1024, used=10240 * 1024 * 1024)

        with patch("shutil.disk_usage") as mock_disk_usage:
            mock_disk_usage.return_value = mock_usage
            with patch.object(
                app.state.attestation_generator, "verify_tpm_available"
            ) as mock_verify:
                mock_verify.return_value = attestation_available

                response = client.get("/health")

                assert response.status_code == 200
                data = response.json()
                # Must contain status key
                assert "status" in data
                # Must NOT contain encrypted_response wrapper
                assert "encrypted_response" not in data

    @settings(max_examples=100, deadline=None)
    @given(
        successful=st.integers(min_value=0, max_value=10),
        failed=st.integers(min_value=0, max_value=10),
    )
    def test_metrics_returns_plain_json_not_encrypted(self, successful, failed):
        """Property 135 (metrics): /metrics response is plain unencrypted JSON
        with no encrypted_response wrapper, even when EncryptionManager is configured."""
        encryption_manager = EncryptionManager()
        app = create_app(get_test_config(), encryption_manager=encryption_manager)
        client = TestClient(app)

        exec_manager = app.state.execution_manager

        for _ in range(successful):
            record = exec_manager.create_execution(
                "https://github.com/owner/repo", "a" * 40, "test.sh", 300
            )
            exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
            exec_manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)

        for _ in range(failed):
            record = exec_manager.create_execution(
                "https://github.com/owner/repo", "b" * 40, "test.sh", 300
            )
            exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
            exec_manager.update_status(record.execution_id, ExecutionStatus.FAILED, exit_code=1)

        response = client.get("/metrics")

        assert response.status_code == 200
        data = response.json()
        # Must contain expected metric keys
        assert "total_executions" in data
        assert "successful_executions" in data
        assert "failed_executions" in data
        assert "average_duration_ms" in data
        assert "active_executions" in data
        # Must NOT contain encrypted_response wrapper
        assert "encrypted_response" not in data
