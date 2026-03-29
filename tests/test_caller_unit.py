"""Unit tests for the GitHub Actions Remote Executor Caller."""

import base64
import sys
import os
from unittest.mock import patch

import pytest
import requests

# Add the caller script directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

from call_remote_executor import (
    CallerError,
    RemoteExecutorCaller,
)


def _make_caller() -> RemoteExecutorCaller:
    """Create a caller instance for testing."""
    return RemoteExecutorCaller(server_url="http://localhost:8080")


class TestAttestationValidationEdgeCases:
    """Unit tests for attestation validation edge cases."""

    def test_invalid_base64_raises_caller_error(self):
        """Invalid base64 input raises CallerError with phase 'attestation'.
        Validates: Requirement 4.3"""
        caller = _make_caller()
        with pytest.raises(CallerError) as exc_info:
            caller.validate_attestation("!!!not-valid-base64!!!")
        assert exc_info.value.phase == "attestation"

    def test_invalid_cbor_raises_caller_error(self):
        """Valid base64 but invalid CBOR raises CallerError with phase 'attestation'.
        Validates: Requirement 4.4"""
        caller = _make_caller()
        # Encode random bytes that are not valid CBOR
        invalid_cbor_b64 = base64.b64encode(b"\xff\xfe\xfd\xfc\xfb").decode("ascii")
        with pytest.raises(CallerError) as exc_info:
            caller.validate_attestation(invalid_cbor_b64)
        assert exc_info.value.phase == "attestation"


class TestHealthCheckAndExecuteEdgeCases:
    """Unit tests for health check and execute connection error edge cases."""

    def test_health_check_connection_refused_raises_caller_error(self):
        """Connection refused on health_check raises CallerError with phase 'health_check'.
        Validates: Requirement 8.4"""
        caller = _make_caller()
        with patch("call_remote_executor.requests.get", side_effect=requests.ConnectionError("Connection refused")):
            with pytest.raises(CallerError) as exc_info:
                caller.health_check()
            assert exc_info.value.phase == "health_check"

    def test_execute_connection_refused_raises_caller_error(self):
        """Connection refused on execute raises CallerError with phase 'execute'.
        Validates: Requirement 3.6"""
        caller = _make_caller()
        with patch("call_remote_executor.requests.post", side_effect=requests.ConnectionError("Connection refused")):
            with pytest.raises(CallerError) as exc_info:
                caller.execute(
                    repository_url="https://github.com/owner/repo",
                    commit_hash="abc123",
                    script_path=".github/scripts/sample-build.sh",
                    github_token="ghp_fake_token",
                )
            assert exc_info.value.phase == "execute"
