"""Unit tests for the GitHub Actions Remote Executor Caller."""

import base64
import sys
import os

import pytest

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
