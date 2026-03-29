"""Property-based tests for the GitHub Actions Remote Executor Caller."""

import base64
import sys
import os
from unittest.mock import patch, MagicMock

import cbor2
import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# Add the caller script directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

from call_remote_executor import (
    EXPECTED_ATTESTATION_FIELDS,
    CallerError,
    RemoteExecutorCaller,
)


def _make_caller() -> RemoteExecutorCaller:
    """Create a caller instance for testing."""
    return RemoteExecutorCaller(server_url="http://localhost:8080")


# Strategy for generating valid attestation document dicts
def attestation_doc_strategy():
    """Generate a valid attestation document dict with all expected fields."""
    return st.fixed_dictionaries(
        {
            "module_id": st.text(min_size=1, max_size=50),
            "digest": st.text(min_size=1, max_size=20),
            "timestamp": st.integers(min_value=0, max_value=2**53),
            "pcrs": st.dictionaries(
                st.integers(min_value=0, max_value=15),
                st.binary(min_size=1, max_size=48),
                min_size=1,
                max_size=5,
            ),
            "certificate": st.binary(min_size=1, max_size=200),
            "cabundle": st.lists(st.binary(min_size=1, max_size=200), min_size=1, max_size=3),
        }
    )


# Feature: gha-remote-executor-caller, Property 1: Attestation decode round-trip
# **Validates: Requirements 4.1, 4.2, 6.1, 6.2**
class TestAttestationDecodeRoundTrip:
    """Property 1: Attestation decode round-trip."""

    @given(doc=attestation_doc_strategy())
    @settings(max_examples=100)
    def test_round_trip(self, doc: dict):
        """For any valid attestation document, CBOR-encoding then base64-encoding,
        then passing through validate_attestation should produce a dict equivalent
        to the original for the fields the validator inspects."""
        caller = _make_caller()

        # Encode: dict -> CBOR -> base64
        cbor_bytes = cbor2.dumps(doc)
        b64_str = base64.b64encode(cbor_bytes).decode("ascii")

        # Decode through validate_attestation
        result = caller.validate_attestation(b64_str)

        # Verify all expected fields match
        for field in EXPECTED_ATTESTATION_FIELDS:
            assert result[field] == doc[field], (
                f"Field {field} mismatch: {result[field]!r} != {doc[field]!r}"
            )


# Feature: gha-remote-executor-caller, Property 2: Attestation structural field validation
# **Validates: Requirements 4.6**
class TestAttestationStructuralFieldValidation:
    """Property 2: Attestation structural field validation."""

    @given(
        base_doc=attestation_doc_strategy(),
        fields_to_remove=st.lists(
            st.sampled_from(EXPECTED_ATTESTATION_FIELDS),
            min_size=0,
            max_size=len(EXPECTED_ATTESTATION_FIELDS),
            unique=True,
        ),
    )
    @settings(max_examples=100)
    def test_structural_field_validation(self, base_doc: dict, fields_to_remove: list):
        """For any Python dict, validate_attestation should accept it if and only if
        all expected structural fields are present as keys."""
        caller = _make_caller()

        # Remove selected fields
        doc = dict(base_doc)
        for field in fields_to_remove:
            doc.pop(field, None)

        # Encode: dict -> CBOR -> base64
        cbor_bytes = cbor2.dumps(doc)
        b64_str = base64.b64encode(cbor_bytes).decode("ascii")

        all_present = len(fields_to_remove) == 0

        if all_present:
            # Should succeed without raising
            result = caller.validate_attestation(b64_str)
            assert isinstance(result, dict)
        else:
            # Should raise CallerError with phase "attestation"
            with pytest.raises(CallerError) as exc_info:
                caller.validate_attestation(b64_str)
            assert exc_info.value.phase == "attestation"


# Feature: gha-remote-executor-caller, Property 4: Health check acceptance
# **Validates: Requirements 8.2, 8.3**
class TestHealthCheckAcceptance:
    """Property 4: Health check acceptance.

    For any health response JSON, health_check should succeed (not raise) if and
    only if the HTTP status is 200 and the status field equals 'healthy'. For all
    other combinations of HTTP status or status field value, it should raise a
    CallerError.
    """

    @given(
        status_code=st.integers(min_value=100, max_value=599),
        status_value=st.text(min_size=0, max_size=50),
    )
    @settings(max_examples=100)
    def test_health_check_acceptance(self, status_code: int, status_value: str):
        caller = _make_caller()

        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = {"status": status_value}
        mock_response.text = f'{{"status": "{status_value}"}}'

        with patch("call_remote_executor.requests.get", return_value=mock_response):
            if status_code == 200 and status_value == "healthy":
                # Should succeed without raising
                result = caller.health_check()
                assert isinstance(result, dict)
                assert result["status"] == "healthy"
            else:
                # Should raise CallerError
                with pytest.raises(CallerError) as exc_info:
                    caller.health_check()
                assert exc_info.value.phase == "health_check"


# Feature: gha-remote-executor-caller, Property 5: Execute HTTP error propagation
# **Validates: Requirements 3.5**
class TestExecuteHTTPErrorPropagation:
    """Property 5: Execute HTTP error propagation.

    For any HTTP error status code (4xx or 5xx), when the /execute endpoint
    returns that status, the execute method should raise a CallerError
    containing the status code and error details.
    """

    @given(
        status_code=st.integers(min_value=400, max_value=599),
        response_body=st.text(min_size=0, max_size=200),
    )
    @settings(max_examples=100)
    def test_execute_http_error_propagation(self, status_code: int, response_body: str):
        caller = _make_caller()

        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.text = response_body

        with patch("call_remote_executor.requests.post", return_value=mock_response):
            with pytest.raises(CallerError) as exc_info:
                caller.execute(
                    repository_url="https://github.com/owner/repo",
                    commit_hash="abc123",
                    script_path=".github/scripts/sample-build.sh",
                    github_token="ghp_test_token",
                )
            assert exc_info.value.phase == "execute"
            assert exc_info.value.details["status_code"] == status_code
