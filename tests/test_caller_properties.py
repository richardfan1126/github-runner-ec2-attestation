"""Property-based tests for the GitHub Actions Remote Executor Caller."""

import base64
import datetime
import sys
import os
from unittest.mock import patch, MagicMock

import cbor2
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID
from cryptography import x509
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from pycose.messages import Sign1Message
from pycose.keys import EC2Key
from pycose.keys.keyparam import EC2KpCurve, EC2KpX, EC2KpY, EC2KpD
from pycose.keys.curves import P384
from pycose.headers import Algorithm
from pycose.algorithms import Es384
from Crypto.Util.number import long_to_bytes

# Add the caller script directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

from call_remote_executor import (
    EXPECTED_ATTESTATION_FIELDS,
    CallerError,
    RemoteExecutorCaller,
)


# ---------------------------------------------------------------------------
# Test CA and signing certificate generation (module-level, generated once)
# ---------------------------------------------------------------------------

def _generate_test_ca_and_cert():
    """Generate a test root CA and signing certificate for property tests."""
    ca_key = ec.generate_private_key(ec.SECP384R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA384())
    )

    sign_key = ec.generate_private_key(ec.SECP384R1())
    sign_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Signer")])
    sign_cert = (
        x509.CertificateBuilder()
        .subject_name(sign_name)
        .issuer_name(ca_name)
        .public_key(sign_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc))
        .sign(ca_key, hashes.SHA384())
    )

    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM).decode()
    ca_der = ca_cert.public_bytes(serialization.Encoding.DER)
    sign_cert_der = sign_cert.public_bytes(serialization.Encoding.DER)

    return ca_pem, ca_der, sign_key, sign_cert_der


_TEST_CA_PEM, _TEST_CA_DER, _TEST_SIGN_KEY, _TEST_SIGN_CERT_DER = _generate_test_ca_and_cert()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_caller() -> RemoteExecutorCaller:
    """Create a caller instance for testing (no root_cert_pem/expected_pcrs => crypto skipped)."""
    return RemoteExecutorCaller(server_url="http://localhost:8080")


def _wrap_cose_sign1(payload_dict: dict) -> str:
    """Wrap a payload dict in a COSE Sign1 structure and return base64 string."""
    payload_bytes = cbor2.dumps(payload_dict)
    protected_header = cbor2.dumps({1: -35})  # ES384
    cose_array = [protected_header, {}, payload_bytes, b'\x00' * 96]
    return base64.b64encode(cbor2.dumps(cose_array)).decode("ascii")


def _make_signed_cose(payload_dict: dict) -> tuple[str, list]:
    """Create a properly signed COSE Sign1 structure. Returns (base64_str, cose_array).

    pycose encodes Sign1 with CBOR tag 18. The production code expects a plain
    4-element array (no tag), so we unwrap the tag and re-encode as a plain list.
    """
    payload_bytes = cbor2.dumps(payload_dict)

    priv_numbers = _TEST_SIGN_KEY.private_numbers()
    pub_numbers = priv_numbers.public_numbers

    d_bytes = long_to_bytes(priv_numbers.private_value).rjust(48, b'\x00')
    x_bytes = long_to_bytes(pub_numbers.x).rjust(48, b'\x00')
    y_bytes = long_to_bytes(pub_numbers.y).rjust(48, b'\x00')

    cose_key = EC2Key.from_dict({
        EC2KpCurve: P384,
        EC2KpX: x_bytes,
        EC2KpY: y_bytes,
        EC2KpD: d_bytes,
    })

    msg = Sign1Message(
        phdr={Algorithm: Es384},
        uhdr={},
        payload=payload_bytes,
    )
    msg.key = cose_key
    encoded = msg.encode()

    # pycose produces CBORTag(18, [...]); unwrap to plain list
    decoded = cbor2.loads(encoded)
    if hasattr(decoded, 'value'):
        cose_array = list(decoded.value)
    else:
        cose_array = list(decoded)

    # Re-encode as a plain 4-element array (no CBOR tag)
    plain_encoded = cbor2.dumps(cose_array)
    b64_str = base64.b64encode(plain_encoded).decode("ascii")
    return b64_str, cose_array


def _make_test_payload(extra_fields: dict | None = None) -> dict:
    """Create a valid attestation payload dict using test certificates."""
    doc = {
        "module_id": "test-module",
        "digest": "SHA384",
        "timestamp": 1700000000000,
        "nitrotpm_pcrs": {0: b'\x00' * 48, 4: b'\xaa' * 48, 7: b'\xbb' * 48},
        "certificate": _TEST_SIGN_CERT_DER,
        "cabundle": [_TEST_CA_DER],
    }
    if extra_fields:
        doc.update(extra_fields)
    return doc


# Strategy for generating valid attestation document dicts
def attestation_doc_strategy():
    """Generate a valid attestation document dict with all expected fields."""
    return st.fixed_dictionaries(
        {
            "module_id": st.text(min_size=1, max_size=50),
            "digest": st.text(min_size=1, max_size=20),
            "timestamp": st.integers(min_value=0, max_value=2**53),
            "nitrotpm_pcrs": st.dictionaries(
                st.integers(min_value=0, max_value=15),
                st.binary(min_size=1, max_size=48),
                min_size=1,
                max_size=5,
            ),
            "certificate": st.binary(min_size=1, max_size=200),
            "cabundle": st.lists(st.binary(min_size=1, max_size=200), min_size=1, max_size=3),
        }
    )


# ---------------------------------------------------------------------------
# Property 1: Attestation decode round-trip
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 1: Attestation decode round-trip
# **Validates: Requirements 4A.1, 4A.2, 4A.3, 6A.1, 6A.2, 6A.3**
class TestAttestationDecodeRoundTrip:
    """Property 1: Attestation decode round-trip."""

    @given(doc=attestation_doc_strategy())
    @settings(max_examples=20)
    def test_round_trip(self, doc: dict):
        """For any valid attestation document, wrapping in COSE Sign1, CBOR-encoding,
        base64-encoding, then passing through validate_attestation should produce a
        dict equivalent to the original for the fields the validator inspects."""
        caller = _make_caller()

        b64_str = _wrap_cose_sign1(doc)

        result = caller.validate_attestation(b64_str)

        for field in EXPECTED_ATTESTATION_FIELDS:
            assert result[field] == doc[field], (
                f"Field {field} mismatch: {result[field]!r} != {doc[field]!r}"
            )


# ---------------------------------------------------------------------------
# Property 2: Attestation structural field validation
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 2: Attestation structural field validation
# **Validates: Requirements 4A.7**
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
    @settings(max_examples=20)
    def test_structural_field_validation(self, base_doc: dict, fields_to_remove: list):
        """For any Python dict, validate_attestation should accept it if and only if
        all expected structural fields are present as keys."""
        caller = _make_caller()

        doc = dict(base_doc)
        for field in fields_to_remove:
            doc.pop(field, None)

        b64_str = _wrap_cose_sign1(doc)

        all_present = len(fields_to_remove) == 0

        if all_present:
            result = caller.validate_attestation(b64_str)
            assert isinstance(result, dict)
        else:
            with pytest.raises(CallerError) as exc_info:
                caller.validate_attestation(b64_str)
            assert exc_info.value.phase == "attestation"


# ---------------------------------------------------------------------------
# Property 4: Health check acceptance
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 4: Health check acceptance
# **Validates: Requirements 8.2, 8.3**
class TestHealthCheckAcceptance:
    """Property 4: Health check acceptance."""

    @given(
        status_code=st.integers(min_value=100, max_value=599),
        status_value=st.text(min_size=0, max_size=50),
    )
    @settings(max_examples=20)
    def test_health_check_acceptance(self, status_code: int, status_value: str):
        caller = _make_caller()

        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = {"status": status_value}
        mock_response.text = f'{{"status": "{status_value}"}}'

        with patch("call_remote_executor.requests.get", return_value=mock_response):
            if status_code == 200 and status_value == "healthy":
                result = caller.health_check()
                assert isinstance(result, dict)
                assert result["status"] == "healthy"
            else:
                with pytest.raises(CallerError) as exc_info:
                    caller.health_check()
                assert exc_info.value.phase == "health_check"


# ---------------------------------------------------------------------------
# Property 5: Execute HTTP error propagation
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 5: Execute HTTP error propagation
# **Validates: Requirements 3.5**
class TestExecuteHTTPErrorPropagation:
    """Property 5: Execute HTTP error propagation."""

    @given(
        status_code=st.integers(min_value=400, max_value=599),
        response_body=st.text(min_size=0, max_size=200),
    )
    @settings(max_examples=20)
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


# ---------------------------------------------------------------------------
# Property 10: COSE signature rejects tampered payloads
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 10: COSE signature verification rejects tampered payloads
# **Validates: Requirements 4C.15, 4C.16**
class TestCOSESignatureRejectsTamperedPayloads:
    """Property 10: COSE signature verification rejects tampered payloads."""

    @given(tamper_byte=st.integers(min_value=0, max_value=255))
    @settings(max_examples=20)
    def test_tampered_payload_rejected(self, tamper_byte: int):
        """Modifying the payload after signing should cause signature verification to fail."""
        payload_dict = _make_test_payload()
        b64_str, cose_array = _make_signed_cose(payload_dict)

        # Tamper at the semantic level: modify a field in the payload dict,
        # then re-CBOR-encode. This keeps CBOR structure valid so structural
        # checks pass, but the COSE signature will no longer match.
        tampered_dict = dict(payload_dict)
        tampered_dict["timestamp"] = payload_dict["timestamp"] + 1 + tamper_byte
        cose_array[2] = cbor2.dumps(tampered_dict)

        # Re-encode the tampered COSE array
        tampered_b64 = base64.b64encode(cbor2.dumps(cose_array)).decode("ascii")

        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            root_cert_pem=_TEST_CA_PEM,
        )

        with pytest.raises(CallerError) as exc_info:
            caller.validate_attestation(tampered_b64)
        assert exc_info.value.phase == "attestation"


# ---------------------------------------------------------------------------
# Property 11: PCR validation accepts matching, rejects mismatching
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 11: PCR validation accepts matching and rejects mismatching values
# **Validates: Requirements 4D.17, 4D.18, 4D.19**
class TestPCRValidation:
    """Property 11: PCR validation accepts matching and rejects mismatching values."""

    @given(
        pcr_values=st.dictionaries(
            st.integers(min_value=0, max_value=15),
            st.binary(min_size=48, max_size=48),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=20)
    def test_matching_pcrs_accepted(self, pcr_values: dict):
        """When expected PCRs match document PCRs, validation should pass."""
        expected = {idx: val.hex() for idx, val in pcr_values.items()}
        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            expected_pcrs=expected,
        )
        # Should not raise
        caller._validate_pcrs(pcr_values)

    @given(
        pcr_values=st.dictionaries(
            st.integers(min_value=0, max_value=15),
            st.binary(min_size=48, max_size=48),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=20)
    def test_mismatching_pcrs_rejected(self, pcr_values: dict):
        """When expected PCRs don't match document PCRs, validation should fail."""
        expected = {}
        for idx, val in pcr_values.items():
            flipped = bytes((b + 1) % 256 for b in val)
            expected[idx] = flipped.hex()
            break  # Only need one mismatch

        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            expected_pcrs=expected,
        )
        with pytest.raises(CallerError) as exc_info:
            caller._validate_pcrs(pcr_values)
        assert exc_info.value.phase == "attestation"

    @given(
        missing_idx=st.integers(min_value=0, max_value=15),
    )
    @settings(max_examples=20)
    def test_missing_pcr_index_rejected(self, missing_idx: int):
        """When an expected PCR index is missing from the document, validation should fail."""
        document_pcrs = {}  # Empty — no PCRs present
        expected = {missing_idx: "aa" * 48}

        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            expected_pcrs=expected,
        )
        with pytest.raises(CallerError) as exc_info:
            caller._validate_pcrs(document_pcrs)
        assert exc_info.value.phase == "attestation"


# ---------------------------------------------------------------------------
# Property 12: Certificate chain validation rejects untrusted certs
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 12: Certificate chain validation rejects untrusted certificates
# **Validates: Requirements 4B.8, 4B.11, 4B.12**
class TestCertificateChainValidation:
    """Property 12: Certificate chain validation rejects untrusted certificates."""

    def test_valid_chain_accepted(self):
        """A certificate properly chained to the root CA should pass validation."""
        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            root_cert_pem=_TEST_CA_PEM,
        )
        # Should not raise
        caller._verify_certificate_chain(_TEST_SIGN_CERT_DER, [_TEST_CA_DER])

    @given(data=st.data())
    @settings(max_examples=20)
    def test_untrusted_cert_rejected(self, data):
        """A certificate not chained to the configured root CA should fail validation."""
        # Generate a completely different CA
        other_ca_key = ec.generate_private_key(ec.SECP384R1())
        other_ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Other CA")])
        other_ca_cert = (
            x509.CertificateBuilder()
            .subject_name(other_ca_name)
            .issuer_name(other_ca_name)
            .public_key(other_ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
            .not_valid_after(datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(other_ca_key, hashes.SHA384())
        )
        other_ca_pem = other_ca_cert.public_bytes(serialization.Encoding.PEM).decode()

        # Use the test signing cert (signed by _TEST_CA) but verify against the other CA.
        # Pass an empty cabundle so the real issuer (_TEST_CA) is NOT in the store.
        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            root_cert_pem=other_ca_pem,
        )
        with pytest.raises(CallerError) as exc_info:
            caller._verify_certificate_chain(_TEST_SIGN_CERT_DER, [])
        assert exc_info.value.phase == "attestation"


# ---------------------------------------------------------------------------
# Property 3: Output integrity verification
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 3: Output integrity verification
# **Validates: Requirements 6B.8, 6B.9, 6B.10, 6B.12**
class TestOutputIntegrityVerification:
    """Property 3: Output integrity verification."""

    @given(
        stdout_val=st.text(min_size=0, max_size=200),
        stderr_val=st.text(min_size=0, max_size=200),
        exit_code_val=st.integers(min_value=0, max_value=255),
    )
    @settings(max_examples=20)
    def test_matching_digest_accepted(self, stdout_val: str, stderr_val: str, exit_code_val: int):
        """If user_data contains the correct SHA-256 digest of the canonical output,
        validate_output_attestation should return True."""
        import hashlib as _hashlib

        canonical = f"stdout:{stdout_val}\nstderr:{stderr_val}\nexit_code:{exit_code_val}"
        digest = _hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        payload_dict = _make_test_payload(extra_fields={"user_data": digest.encode("utf-8")})
        b64_str, _ = _make_signed_cose(payload_dict)

        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            root_cert_pem=_TEST_CA_PEM,
            expected_pcrs={4: b'\xaa' * 48, 7: b'\xbb' * 48},
        )
        # Fix expected_pcrs to hex strings
        caller.expected_pcrs = {4: "aa" * 48, 7: "bb" * 48}

        result = caller.validate_output_attestation(b64_str, stdout_val, stderr_val, exit_code_val)
        assert result is True

    @given(
        stdout_val=st.text(min_size=0, max_size=200),
        stderr_val=st.text(min_size=0, max_size=200),
        exit_code_val=st.integers(min_value=0, max_value=255),
    )
    @settings(max_examples=20)
    def test_tampered_output_rejected(self, stdout_val: str, stderr_val: str, exit_code_val: int):
        """If stdout/stderr/exit_code is altered after the digest was computed,
        validate_output_attestation should raise CallerError."""
        import hashlib as _hashlib

        canonical = f"stdout:{stdout_val}\nstderr:{stderr_val}\nexit_code:{exit_code_val}"
        digest = _hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        payload_dict = _make_test_payload(extra_fields={"user_data": digest.encode("utf-8")})
        b64_str, _ = _make_signed_cose(payload_dict)

        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            root_cert_pem=_TEST_CA_PEM,
            expected_pcrs={4: "aa" * 48, 7: "bb" * 48},
        )

        # Tamper the stdout
        tampered_stdout = stdout_val + "_tampered"
        with pytest.raises(CallerError) as exc_info:
            caller.validate_output_attestation(b64_str, tampered_stdout, stderr_val, exit_code_val)
        assert exc_info.value.phase == "output_attestation"
        assert "mismatch" in exc_info.value.message.lower() or "integrity" in exc_info.value.message.lower()


# ---------------------------------------------------------------------------
# Property 6: Polling termination on completion
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 6: Polling termination on completion
# **Validates: Requirements 5.3, 5.4**
class TestPollingTerminationOnCompletion:
    """Property 6: Polling termination on completion."""

    @given(
        n_incomplete=st.integers(min_value=0, max_value=10),
        stdout_val=st.text(min_size=0, max_size=50),
        stderr_val=st.text(min_size=0, max_size=50),
        exit_code_val=st.integers(min_value=0, max_value=255),
    )
    @settings(max_examples=20)
    def test_polls_until_complete(self, n_incomplete: int, stdout_val: str, stderr_val: str, exit_code_val: int):
        """Given N incomplete responses followed by 1 complete response,
        poll_output should make exactly N+1 requests and return the final response."""
        incomplete_response = MagicMock()
        incomplete_response.status_code = 200
        incomplete_response.json.return_value = {
            "stdout": "",
            "stderr": "",
            "complete": False,
            "exit_code": None,
            "output_attestation_document": None,
        }

        complete_response = MagicMock()
        complete_response.status_code = 200
        complete_response.json.return_value = {
            "stdout": stdout_val,
            "stderr": stderr_val,
            "complete": True,
            "exit_code": exit_code_val,
            "output_attestation_document": "some_b64_doc",
        }

        responses = [incomplete_response] * n_incomplete + [complete_response]

        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            poll_interval=0,  # No sleep in tests
            max_poll_duration=9999,
        )

        with patch("call_remote_executor.requests.get", side_effect=responses) as mock_get:
            with patch("call_remote_executor.time.sleep"):
                result = caller.poll_output("test-exec-id")

        assert mock_get.call_count == n_incomplete + 1
        assert result["stdout"] == stdout_val
        assert result["stderr"] == stderr_val
        assert result["exit_code"] == exit_code_val
        assert result["output_attestation_document"] == "some_b64_doc"


# ---------------------------------------------------------------------------
# Property 7: Polling retry on transient errors
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 7: Polling retry on transient errors
# **Validates: Requirements 5.7**
class TestPollingRetryOnTransientErrors:
    """Property 7: Polling retry on transient errors."""

    @given(
        k_errors=st.integers(min_value=1, max_value=2),
    )
    @settings(max_examples=20)
    def test_recovers_from_fewer_than_max_retries(self, k_errors: int):
        """When K < max_retries consecutive HTTP errors occur followed by success,
        poll_output should recover and return the successful response."""
        max_retries = 3

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.text = "Internal Server Error"

        complete_response = MagicMock()
        complete_response.status_code = 200
        complete_response.json.return_value = {
            "stdout": "ok",
            "stderr": "",
            "complete": True,
            "exit_code": 0,
            "output_attestation_document": None,
        }

        responses = [error_response] * k_errors + [complete_response]

        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            poll_interval=0,
            max_poll_duration=9999,
            max_retries=max_retries,
        )

        with patch("call_remote_executor.requests.get", side_effect=responses):
            with patch("call_remote_executor.time.sleep"):
                result = caller.poll_output("test-exec-id")

        assert result["stdout"] == "ok"
        assert result["exit_code"] == 0

    @given(
        max_retries=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=20)
    def test_fails_after_max_retries_consecutive_errors(self, max_retries: int):
        """When max_retries consecutive HTTP errors occur, poll_output should raise CallerError."""
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.text = "Internal Server Error"

        responses = [error_response] * (max_retries + 5)  # More than enough errors

        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            poll_interval=0,
            max_poll_duration=9999,
            max_retries=max_retries,
        )

        with patch("call_remote_executor.requests.get", side_effect=responses):
            with patch("call_remote_executor.time.sleep"):
                with pytest.raises(CallerError) as exc_info:
                    caller.poll_output("test-exec-id")
                assert exc_info.value.phase == "polling"

    @given(
        k_errors=st.integers(min_value=1, max_value=2),
    )
    @settings(max_examples=20)
    def test_recovers_from_connection_errors(self, k_errors: int):
        """When K < max_retries consecutive connection errors occur followed by success,
        poll_output should recover."""
        max_retries = 3

        complete_response = MagicMock()
        complete_response.status_code = 200
        complete_response.json.return_value = {
            "stdout": "recovered",
            "stderr": "",
            "complete": True,
            "exit_code": 0,
            "output_attestation_document": None,
        }

        side_effects = [requests.ConnectionError("timeout")] * k_errors + [complete_response]

        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            poll_interval=0,
            max_poll_duration=9999,
            max_retries=max_retries,
        )

        with patch("call_remote_executor.requests.get", side_effect=side_effects):
            with patch("call_remote_executor.time.sleep"):
                result = caller.poll_output("test-exec-id")

        assert result["stdout"] == "recovered"

# ---------------------------------------------------------------------------
# Property 8: Exit code propagation
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 8: Exit code propagation
# **Validates: Requirements 7.6**
class TestExitCodePropagation:
    """Property 8: For any integer exit code returned by the remote script,
    the run method should return that same exit code."""

    @given(
        exit_code=st.integers(min_value=0, max_value=255),
    )
    @settings(max_examples=100)
    def test_exit_code_propagated(self, exit_code: int):
        """run() returns the same exit code as the remote script."""
        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            poll_interval=0,
            max_poll_duration=9999,
        )

        health_response = MagicMock()
        health_response.status_code = 200
        health_response.json.return_value = {"status": "healthy"}

        exec_response = MagicMock()
        exec_response.status_code = 200
        exec_response.json.return_value = {
            "execution_id": "test-id",
            "attestation_document": "dGVzdA==",
            "status": "queued",
        }

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            "stdout": "out",
            "stderr": "err",
            "complete": True,
            "exit_code": exit_code,
            "output_attestation_document": None,
        }

        with patch("call_remote_executor.requests.get", side_effect=[health_response, poll_response]):
            with patch("call_remote_executor.requests.post", return_value=exec_response):
                with patch.object(caller, "validate_attestation", return_value={}):
                    result = caller.run("https://github.com/o/r", "abc", "script.sh", "tok")

        assert result == exit_code


# ---------------------------------------------------------------------------
# Property 9: Summary contains execution results
# ---------------------------------------------------------------------------

# Feature: gha-remote-executor-caller, Property 9: Summary contains execution results
# **Validates: Requirements 7.7**
class TestSummaryContainsExecutionResults:
    """Property 9: The generated summary string should contain stdout, stderr,
    exit code, attestation status, and output integrity status."""

    @given(
        stdout_val=st.text(min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"))),
        stderr_val=st.text(min_size=0, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"))),
        exit_code_val=st.integers(min_value=0, max_value=255),
    )
    @settings(max_examples=100)
    def test_summary_contains_all_fields(self, stdout_val: str, stderr_val: str, exit_code_val: int):
        """Summary string contains stdout, stderr, exit code, attestation and integrity status."""
        caller = RemoteExecutorCaller(
            server_url="http://localhost:8080",
            poll_interval=0,
            max_poll_duration=9999,
        )

        health_response = MagicMock()
        health_response.status_code = 200
        health_response.json.return_value = {"status": "healthy"}

        exec_response = MagicMock()
        exec_response.status_code = 200
        exec_response.json.return_value = {
            "execution_id": "test-id",
            "attestation_document": "dGVzdA==",
            "status": "queued",
        }

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            "stdout": stdout_val,
            "stderr": stderr_val,
            "complete": True,
            "exit_code": exit_code_val,
            "output_attestation_document": None,
        }

        with patch("call_remote_executor.requests.get", side_effect=[health_response, poll_response]):
            with patch("call_remote_executor.requests.post", return_value=exec_response):
                with patch.object(caller, "validate_attestation", return_value={}):
                    caller.run("https://github.com/o/r", "abc", "script.sh", "tok")

        summary = caller.summary
        assert stdout_val in summary
        assert stderr_val in summary
        assert str(exit_code_val) in summary
        assert "pass" in summary  # attestation status
        assert "skipped" in summary  # output integrity (no attestation doc)
