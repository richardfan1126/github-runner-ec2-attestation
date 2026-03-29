"""Property-based tests for the GitHub Actions Remote Executor Caller."""

import base64
import sys
import os

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
