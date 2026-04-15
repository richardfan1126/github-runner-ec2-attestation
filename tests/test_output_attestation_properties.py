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


from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_output_request,
    decrypt_output_response,
)


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
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )


# Strategies
script_stdout = st.text(min_size=0, max_size=500)
script_stderr = st.text(min_size=0, max_size=500)
script_exit_code = st.integers(min_value=-1, max_value=255)

execution_status_strategy = st.sampled_from([
    ExecutionStatus.RUNNING,
    ExecutionStatus.COMPLETED,
    ExecutionStatus.FAILED,
    ExecutionStatus.TIMED_OUT,
])


def _build_record_and_output(exec_status, stdout, stderr, exit_code, execution_id="test-exec"):
    """Build ExecutionRecord and OutputData appropriate for the given status."""
    now = datetime.now(timezone.utc)

    if exec_status == ExecutionStatus.RUNNING:
        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.RUNNING,
            created_at=now,
            started_at=now,
            completed_at=None,
            exit_code=None,
            timeout_seconds=300,
        )
        output_data = OutputData(
            stdout=stdout,
            stderr=stderr,
            stdout_offset=len(stdout),
            stderr_offset=len(stderr),
            complete=False,
            exit_code=None,
        )
    elif exec_status == ExecutionStatus.COMPLETED:
        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.COMPLETED,
            created_at=now,
            started_at=now,
            completed_at=now,
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
    elif exec_status == ExecutionStatus.FAILED:
        # For failed, ensure non-zero exit code
        failed_exit = exit_code if exit_code != 0 else 1
        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.FAILED,
            created_at=now,
            started_at=now,
            completed_at=now,
            exit_code=failed_exit,
            timeout_seconds=300,
        )
        output_data = OutputData(
            stdout=stdout,
            stderr=stderr,
            stdout_offset=len(stdout),
            stderr_offset=len(stderr),
            complete=True,
            exit_code=failed_exit,
        )
    else:  # TIMED_OUT
        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.TIMED_OUT,
            created_at=now,
            started_at=now,
            completed_at=now,
            exit_code=None,
            timeout_seconds=300,
        )
        output_data = OutputData(
            stdout=stdout,
            stderr=stderr,
            stdout_offset=len(stdout),
            stderr_offset=len(stderr),
            complete=True,
            exit_code=None,
        )

    return record, output_data


# Property 44: Output Attestation Digest Integrity
@settings(max_examples=20)
@given(
    stdout=script_stdout,
    stderr=script_stderr,
    exit_code=script_exit_code,
    attestation_bytes=st.binary(min_size=100, max_size=2000),
    exec_status=execution_status_strategy,
)
def test_property_44_output_attestation_digest_integrity(
    stdout, stderr, exit_code, attestation_bytes, exec_status
):
    """
    Property 44: For any Script_Output, the user_data passed to nitro-tpm-attest
    matches the SHA-256 hex digest of that Script_Output, regardless of execution status.

    **Validates: Requirements 6.7, 6.9**
    """
    generator = AttestationGenerator(tpm_attest_path="/usr/bin/nitro-tpm-attest")

    # Determine the effective exit_code based on status (mirrors server behavior)
    if exec_status == ExecutionStatus.RUNNING:
        effective_exit_code = None
    elif exec_status == ExecutionStatus.FAILED:
        effective_exit_code = exit_code if exit_code != 0 else 1
    elif exec_status == ExecutionStatus.TIMED_OUT:
        effective_exit_code = None
    else:  # COMPLETED
        effective_exit_code = exit_code

    script_output = f"stdout:{stdout}\nstderr:{stderr}\nexit_code:{effective_exit_code}"
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
        f"user_data digest mismatch for status {exec_status.value}: "
        f"got {captured_user_data['content']}, expected {expected_digest}"
    )


# Property 45: Output Attestation Base64 Encoding
@settings(max_examples=20, deadline=None)
@given(
    stdout=script_stdout,
    stderr=script_stderr,
    exit_code=script_exit_code,
    attestation_bytes=st.binary(min_size=100, max_size=2000),
    exec_status=execution_status_strategy,
)
def test_property_45_output_attestation_base64_encoding(
    stdout, stderr, exit_code, attestation_bytes, exec_status
):
    """
    Property 45: When output attestation generation succeeds, the
    output_attestation_document field is a valid base64-encoded string,
    on every poll response regardless of execution status.

    **Validates: Requirements 6.8**
    """
    ctx = EncryptionTestContext()
    app = create_app(get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)

    execution_id = "test-exec-b64"
    ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

    record, output_data = _build_record_and_output(
        exec_status, stdout, stderr, exit_code, execution_id
    )

    with patch.object(
        app.state.request_validator, "validate_oidc_token_from_body", return_value=VALID_OIDC_RESULT
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
                    req_body = make_encrypted_output_request(
                        {"oidc_token": "valid.oidc.token", "offset": 0}, ctx.shared_key
                    )
                    response = client.post(f"/execution/{execution_id}/output", json=req_body)

    assert response.status_code == 200
    data = decrypt_output_response(response.json(), ctx.shared_key)
    assert "output_attestation_document" in data
    doc_value = data["output_attestation_document"]
    assert doc_value is not None, (
        f"output_attestation_document should not be null for status {exec_status.value}"
    )

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
    exec_status=execution_status_strategy,
)
def test_property_46_output_attestation_failure_graceful_degradation(
    stdout, stderr, exit_code, error_msg, exec_status
):
    """
    Property 46: When output attestation generation fails, the response still
    includes Script_Output, with output_attestation_document set to null and
    attestation_error present, on every poll response regardless of execution status.

    **Validates: Requirements 6.11**
    """
    ctx = EncryptionTestContext()
    app = create_app(get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)

    execution_id = "test-exec-fail"
    ctx.encryption_manager.store_encryption_context(execution_id, ctx.shared_key)

    record, output_data = _build_record_and_output(
        exec_status, stdout, stderr, exit_code, execution_id
    )

    with patch.object(
        app.state.request_validator, "validate_oidc_token_from_body", return_value=VALID_OIDC_RESULT
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
                    req_body = make_encrypted_output_request(
                        {"oidc_token": "valid.oidc.token", "offset": 0}, ctx.shared_key
                    )
                    response = client.post(f"/execution/{execution_id}/output", json=req_body)

    assert response.status_code == 200
    data = decrypt_output_response(response.json(), ctx.shared_key)

    # Script output must still be present
    assert data["stdout"] == output_data.stdout
    assert data["stderr"] == output_data.stderr
    assert data["exit_code"] == output_data.exit_code

    # output_attestation_document must be null
    assert data["output_attestation_document"] is None

    # attestation_error must be present
    assert "attestation_error" in data
    assert data["attestation_error"] == error_msg
