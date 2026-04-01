"""Property-based tests for Output Attestation Document generation

Feature: github-actions-remote-executor
Tests Properties 44, 45, 46 from the design document
"""
import base64
import hashlib
from unittest.mock import Mock, patch

from hypothesis import given, strategies as st, settings
from fastapi.testclient import TestClient

from src.attestation import AttestationGenerator
from src.server import create_app
from src.config import ServerConfig
from src.models import ExecutionStatus, ExecutionRecord, OutputData, OIDCValidationResult
from datetime import datetime, timezone


VALID_OIDC_RESULT = OIDCValidationResult(
    valid=True,
    status_code=200,
    error_message=None,
    claims={"repository": "owner/repo", "iss": "https://token.actions.githubusercontent.com", "aud": "https://example.com"},
)


def get_test_config():
    """Create test configuration"""
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
    )


# Strategies
script_stdout = st.text(min_size=0, max_size=500)
script_stderr = st.text(min_size=0, max_size=500)
script_exit_code = st.integers(min_value=-1, max_value=255)


# Property 44: Output Attestation Digest Integrity
@settings(max_examples=20)
@given(
    stdout=script_stdout,
    stderr=script_stderr,
    exit_code=script_exit_code,
    attestation_bytes=st.binary(min_size=100, max_size=2000),
)
def test_property_44_output_attestation_digest_integrity(
    stdout, stderr, exit_code, attestation_bytes
):
    """
    Property 44: For any Script_Output, the user_data passed to nitro-tpm-attest
    matches the SHA-256 hex digest of that Script_Output.

    **Validates: Requirements 6.7, 6.9**
    """
    generator = AttestationGenerator(tpm_attest_path="/usr/bin/nitro-tpm-attest")

    script_output = f"stdout:{stdout}\nstderr:{stderr}\nexit_code:{exit_code}"
    expected_digest = hashlib.sha256(script_output.encode("utf-8")).hexdigest()

    captured_user_data = {}

    def capture_and_run(cmd, **kwargs):
        # Read the user_data file that was passed to the command
        user_data_idx = cmd.index("--user-data")
        user_data_path = cmd[user_data_idx + 1]
        with open(user_data_path, "r") as f:
            captured_user_data["content"] = f.read()

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = attestation_bytes
        mock_result.stderr = b""
        return mock_result

    with patch("subprocess.run", side_effect=capture_and_run):
        result_bytes, error = generator.generate_output_attestation(script_output)

    assert result_bytes is not None, f"Expected success but got error: {error}"
    assert error is None
    assert captured_user_data["content"] == expected_digest, (
        f"user_data digest mismatch: got {captured_user_data['content']}, "
        f"expected {expected_digest}"
    )


# Property 45: Output Attestation Base64 Encoding
@settings(max_examples=20, deadline=None)
@given(
    stdout=script_stdout,
    stderr=script_stderr,
    exit_code=script_exit_code,
    attestation_bytes=st.binary(min_size=100, max_size=2000),
)
def test_property_45_output_attestation_base64_encoding(
    stdout, stderr, exit_code, attestation_bytes
):
    """
    Property 45: When output attestation generation succeeds, the
    output_attestation_document field is a valid base64-encoded string.

    **Validates: Requirements 6.8**
    """
    app = create_app(get_test_config())
    client = TestClient(app)

    execution_id = "test-exec-b64"

    record = ExecutionRecord(
        execution_id=execution_id,
        repository_url="https://github.com/test/repo",
        commit_hash="a" * 40,
        script_path="test.sh",
        status=ExecutionStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        exit_code=exit_code,
        timeout_seconds=300,
    )

    output_data = OutputData(
        stdout=stdout,
        stderr=stderr,
        stdout_offset=len(stdout),
        stderr_offset=len(stderr),
        complete=True,
        exit_code=exit_code,
    )

    with patch.object(
        app.state.request_validator, "validate_oidc_token", return_value=VALID_OIDC_RESULT
    ):
        with patch.object(
            app.state.execution_manager, "get_execution", return_value=record
        ):
            with patch.object(
                app.state.output_collector, "get_output", return_value=output_data
            ):
                with patch.object(
                    app.state.attestation_generator,
                    "generate_output_attestation",
                    return_value=(attestation_bytes, None),
                ):
                    response = client.get(f"/execution/{execution_id}/output")

    assert response.status_code == 200
    data = response.json()
    assert "output_attestation_document" in data
    doc_value = data["output_attestation_document"]
    assert doc_value is not None

    # Must be valid base64
    decoded = base64.b64decode(doc_value)
    assert decoded == attestation_bytes


# Property 46: Output Attestation Failure Graceful Degradation
@settings(max_examples=20, deadline=None)
@given(
    stdout=script_stdout,
    stderr=script_stderr,
    exit_code=script_exit_code,
    error_msg=st.text(min_size=1, max_size=200),
)
def test_property_46_output_attestation_failure_graceful_degradation(
    stdout, stderr, exit_code, error_msg
):
    """
    Property 46: When output attestation generation fails, the response still
    includes Script_Output, with output_attestation_document set to null and
    attestation_error present.

    **Validates: Requirements 6.11**
    """
    app = create_app(get_test_config())
    client = TestClient(app)

    execution_id = "test-exec-fail"

    record = ExecutionRecord(
        execution_id=execution_id,
        repository_url="https://github.com/test/repo",
        commit_hash="a" * 40,
        script_path="test.sh",
        status=ExecutionStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        exit_code=exit_code,
        timeout_seconds=300,
    )

    output_data = OutputData(
        stdout=stdout,
        stderr=stderr,
        stdout_offset=len(stdout),
        stderr_offset=len(stderr),
        complete=True,
        exit_code=exit_code,
    )

    with patch.object(
        app.state.request_validator, "validate_oidc_token", return_value=VALID_OIDC_RESULT
    ):
        with patch.object(
            app.state.execution_manager, "get_execution", return_value=record
        ):
            with patch.object(
                app.state.output_collector, "get_output", return_value=output_data
            ):
                with patch.object(
                    app.state.attestation_generator,
                    "generate_output_attestation",
                    return_value=(None, error_msg),
                ):
                    response = client.get(f"/execution/{execution_id}/output")

    assert response.status_code == 200
    data = response.json()

    # Script output must still be present
    assert data["stdout"] == stdout
    assert data["stderr"] == stderr
    assert data["exit_code"] == exit_code

    # output_attestation_document must be null
    assert data["output_attestation_document"] is None

    # attestation_error must be present
    assert "attestation_error" in data
    assert data["attestation_error"] == error_msg
