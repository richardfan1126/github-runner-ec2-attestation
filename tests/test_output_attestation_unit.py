"""Unit tests for output attestation document generation

Feature: github-actions-remote-executor
Requirements: 6.7, 6.8, 6.9, 6.11
"""
import base64
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.attestation import AttestationGenerator
from src.config import ServerConfig
from src.models import ExecutionRecord, ExecutionStatus, OutputData, OIDCValidationResult, OutputAttestationResult
from src.server import create_app


from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_output_request,
    decrypt_output_response,
)


VALID_OIDC_RESULT = OIDCValidationResult(
    valid=True,
    status_code=200,
    error_message=None,
    claims={"repository": "owner/repo", "iss": "https://token.actions.githubusercontent.com", "aud": "https://example.com", "sha": "a" * 40},
)


def get_test_config():
    return ServerConfig(
        port=8000,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=100,
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


@pytest.fixture
def generator():
    return AttestationGenerator(tpm_attest_path="/usr/bin/nitro-tpm-attest")


def _decode_claims(claims_raw: str) -> dict:
    return json.loads(base64.b64decode(claims_raw))


class TestGenerateOutputAttestation:
    """Tests for AttestationGenerator.generate_output_attestation"""

    @patch("subprocess.run")
    def test_success_returns_attestation_bytes(self, mock_run, generator):
        """Test successful output attestation returns bytes"""
        expected_bytes = b"mock_output_attestation_cbor"
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = expected_bytes
        mock_result.stderr = b""
        mock_run.return_value = mock_result

        result, error = generator.generate_output_attestation("hello", "", 0)

        assert result.signature == expected_bytes
        assert error is None

    @patch("subprocess.run")
    def test_output_digest_is_canonical_json_sha256(self, mock_run, generator):
        """Test that the output claims document contains output_digest computed
        over the canonical JSON { stdout, stderr, exit_code }, not a delimiter-glued
        string (D11)."""
        captured = {}

        def capture_and_run(cmd, **kwargs):
            idx = cmd.index("--user-data")
            with open(cmd[idx + 1], "r") as f:
                captured["user_data"] = json.load(f)
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = b"attestation"
            mock_result.stderr = b""
            return mock_result

        mock_run.side_effect = capture_and_run

        result, error = generator.generate_output_attestation("test output", "warn", 1)

        assert error is None
        claims = _decode_claims(result.claims_raw)
        canonical = json.dumps(
            {"stdout": "test output", "stderr": "warn", "exit_code": 1},
            sort_keys=True, separators=(',', ':'),
        )
        expected_digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert claims["output_digest"] == expected_digest
        # The envelope itself carries no output-related fields
        assert "output_digest" not in captured["user_data"]

    @patch("subprocess.run")
    def test_failure_returns_none_and_error(self, mock_run, generator):
        """Test failed nitro-tpm-attest returns None and error message"""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = b""
        mock_result.stderr = b"device not found"
        mock_run.return_value = mock_result

        result, error = generator.generate_output_attestation("some", "output", 0)

        assert result is None
        assert error is not None
        assert "exit code 1" in error

    @patch("subprocess.run")
    def test_timeout_returns_none_and_error(self, mock_run, generator):
        """Test timeout returns None and error message"""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["nitro-tpm-attest"], timeout=30
        )

        result, error = generator.generate_output_attestation("output", "", 0)

        assert result is None
        assert "timed out" in error.lower()

    @patch("subprocess.run")
    def test_os_error_returns_none_and_error(self, mock_run, generator):
        """Test OS error returns None and error message"""
        mock_run.side_effect = OSError("Permission denied")

        result, error = generator.generate_output_attestation("output", "", 0)

        assert result is None
        assert "OS error" in error


class TestOutputEndpointWithAttestation:
    """Tests for POST /execution/{id}/output with output attestation"""

    def _make_record(self, execution_id, status, exit_code=None):
        return ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=status,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc) if exit_code is not None else None,
            exit_code=exit_code,
            timeout_seconds=300,
            repository="owner/repo",
        )

    def _setup(self):
        ctx = EncryptionTestContext()
        app = create_app(get_test_config(), encryption_manager=ctx.encryption_manager)
        client = TestClient(app)
        return ctx, app, client

    def test_complete_execution_includes_output_attestation(self):
        """Test completed execution returns output_attestation_document"""
        ctx, app, client = self._setup()
        eid = "test-complete-attest"
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)

        record = self._make_record(eid, ExecutionStatus.COMPLETED, exit_code=0)
        output = OutputData(
            stdout="ok", stderr="", stdout_offset=2,
            stderr_offset=0, complete=True, exit_code=0,
        )
        attestation_bytes = b"output_attestation_cbor_data"

        with patch.object(app.state.execution_manager, "get_execution", return_value=record):
            with patch.object(app.state.output_collector, "get_output", return_value=output):
                with patch.object(
                    app.state.attestation_generator,
                    "generate_output_attestation",
                    return_value=(
                        OutputAttestationResult(signature=attestation_bytes, claims_raw="e30="),
                        None,
                    ),
                ):
                    req_body = make_encrypted_output_request(
                        {"offset": 0}, ctx.shared_key
                    )
                    response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert "output_attestation_document" in data
        decoded = base64.b64decode(data["output_attestation_document"])
        assert decoded == attestation_bytes
        assert data["claims_raw"] == "e30="

    def test_complete_execution_attestation_failure_returns_error(self):
        """Test completed execution with attestation failure returns null + error"""
        ctx, app, client = self._setup()
        eid = "test-attest-fail"
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)

        record = self._make_record(eid, ExecutionStatus.COMPLETED, exit_code=0)
        output = OutputData(
            stdout="ok", stderr="", stdout_offset=2,
            stderr_offset=0, complete=True, exit_code=0,
        )

        with patch.object(app.state.execution_manager, "get_execution", return_value=record):
            with patch.object(app.state.output_collector, "get_output", return_value=output):
                with patch.object(
                    app.state.attestation_generator,
                    "generate_output_attestation",
                    return_value=(None, "TPM device unavailable"),
                ):
                    req_body = make_encrypted_output_request(
                        {"offset": 0}, ctx.shared_key
                    )
                    response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert data["output_attestation_document"] is None
        assert data["attestation_error"] == "TPM device unavailable"
        # Script output still present
        assert data["stdout"] == "ok"
        assert data["exit_code"] == 0

    def test_running_execution_includes_output_attestation(self):
        """Test running execution includes output_attestation_document on every poll"""
        ctx, app, client = self._setup()
        eid = "test-running"
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)

        record = self._make_record(eid, ExecutionStatus.RUNNING)
        output = OutputData(
            stdout="partial", stderr="", stdout_offset=7,
            stderr_offset=0, complete=False, exit_code=None,
        )
        attestation_bytes = b"running_output_attestation_cbor"

        with patch.object(app.state.execution_manager, "get_execution", return_value=record):
            with patch.object(app.state.output_collector, "get_output", return_value=output):
                with patch.object(
                    app.state.attestation_generator,
                    "generate_output_attestation",
                    return_value=(
                        OutputAttestationResult(signature=attestation_bytes, claims_raw="e30="),
                        None,
                    ),
                ) as mock_gen:
                    req_body = make_encrypted_output_request(
                        {"offset": 0}, ctx.shared_key
                    )
                    response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert "output_attestation_document" in data
        decoded = base64.b64decode(data["output_attestation_document"])
        assert decoded == attestation_bytes
        # Verify stdout/stderr/exit_code were passed as separate positional args
        mock_gen.assert_called_once()
        call_args = mock_gen.call_args
        assert call_args[0][0] == "partial"
        assert call_args[0][1] == ""
        assert call_args[0][2] is None
        assert call_args[1].get("execution_id") == "test-running"

    def test_queued_execution_includes_output_attestation(self):
        """Test queued execution (no output buffer) includes output_attestation_document with empty output"""
        ctx, app, client = self._setup()
        eid = "test-queued"
        ctx.encryption_manager.store_encryption_context(eid, ctx.shared_key)

        record = self._make_record(eid, ExecutionStatus.QUEUED)
        attestation_bytes = b"queued_output_attestation_cbor"

        with patch.object(app.state.execution_manager, "get_execution", return_value=record):
            with patch.object(
                app.state.output_collector, "get_output",
                side_effect=ValueError("No output buffer"),
            ):
                with patch.object(
                    app.state.attestation_generator,
                    "generate_output_attestation",
                    return_value=(
                        OutputAttestationResult(signature=attestation_bytes, claims_raw="e30="),
                        None,
                    ),
                ) as mock_gen:
                    req_body = make_encrypted_output_request(
                        {"offset": 0}, ctx.shared_key
                    )
                    response = client.post(f"/execution/{eid}/output", json=req_body)

        assert response.status_code == 200
        data = decrypt_output_response(response.json(), ctx.shared_key)
        assert "output_attestation_document" in data
        decoded = base64.b64decode(data["output_attestation_document"])
        assert decoded == attestation_bytes
        # Verify empty output was passed as separate positional args
        mock_gen.assert_called_once()
        call_args = mock_gen.call_args
        assert call_args[0][0] == ""
        assert call_args[0][1] == ""
        assert call_args[0][2] is None
        assert call_args[1].get("execution_id") == "test-queued"
