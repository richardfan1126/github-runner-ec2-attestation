"""Tests for the claims-digest envelope binding contract (attestation-claims-digest change).

Covers tasks.md section 9 (Tests & validation):
- 9.1: envelope has exactly { v, claims_digest, timestamp, execution_id }
- 9.2: decode-then-hash of claims_raw reproduces claims_digest; tampering breaks it
- 9.3: unknown MAJOR schema_version rejected; higher MINOR tolerated with unknown
       fields ignored; unknown digest algorithm rejected
- 9.5: output_digest binds canonical JSON { stdout, stderr, exit_code } so a
       delimiter-injection triple cannot collide with a genuine different triple

GPU claims coverage (9.4) lives in tests/test_gpu_passthrough.py::TestGPUAttestation.
Duplicate-nonce-rejected-on-both-endpoints coverage (9.5) lives in
tests/test_nonce_cache_unit.py.

There is no first-party verifier module in this repo (the verifier is the
`github-runner-ec2-attestation-rust-build-demo` caller, tracked cross-repo per
tasks.md 8.4) — `_verify_binding` below implements the documented contract
(README "Attestation Claims" section) as a reference verifier so the contract
itself has executable test coverage.
"""
import base64
import hashlib
import json
from unittest.mock import Mock, patch

import pytest

from src.attestation import AttestationGenerator, CLAIMS_SCHEMA_VERSION


@pytest.fixture
def generator():
    return AttestationGenerator(tpm_attest_path="/usr/bin/nitro-tpm-attest")


@pytest.fixture
def mock_successful_attestation():
    captured = {}

    def capture_and_run(cmd, **kwargs):
        if "--user-data" in cmd:
            with open(cmd[cmd.index("--user-data") + 1], "r") as f:
                captured["user_data"] = json.load(f)
        result = Mock()
        result.returncode = 0
        result.stdout = b"mock_cbor_attestation"
        result.stderr = b""
        return result

    return captured, capture_and_run


def _verify_binding(envelope: dict, claims_raw: str, known_major: str = "1") -> dict:
    """
    Reference implementation of the mandatory, fail-closed binding-check
    contract (README "Verifying the binding"):

    1. base64-decode claims_raw
    2. hash the decoded bytes, compare to claims_digest (reject unknown algorithm)
    3. parse JSON, reject unknown MAJOR schema_version; tolerate higher MINOR
    4. only then return the claim fields

    Raises ValueError on any failure (missing preimage, digest mismatch,
    unknown algorithm, unknown MAJOR). Returns the parsed claims dict on success.
    """
    if claims_raw is None:
        raise ValueError("missing claims_raw")

    digest_field = envelope["claims_digest"]
    if not digest_field.startswith("sha256:"):
        raise ValueError(f"unknown digest algorithm: {digest_field}")

    claims_bytes = base64.b64decode(claims_raw)
    recomputed = "sha256:" + hashlib.sha256(claims_bytes).hexdigest()
    if recomputed != digest_field:
        raise ValueError("claims_digest mismatch")

    claims = json.loads(claims_bytes)
    major, _, _minor = claims["schema_version"].partition(".")
    if major != known_major:
        raise ValueError(f"unknown MAJOR schema_version: {claims['schema_version']}")

    return claims


class TestEnvelopeExactFields:
    """9.1: user_data envelope is exactly { v, claims_digest, timestamp, execution_id }."""

    def test_envelope_has_exactly_v_claims_digest_timestamp_execution_id(
        self, generator, mock_successful_attestation
    ):
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="build.sh",
                execution_id="exec-envelope-001",
                script_env={"X": "1"},
            )

        assert error is None
        envelope = captured["user_data"]
        assert set(envelope.keys()) == {"v", "claims_digest", "timestamp", "execution_id"}
        assert envelope["execution_id"] == "exec-envelope-001"
        assert isinstance(envelope["v"], int)
        assert envelope["claims_digest"].startswith("sha256:")

        # None of the moved claim fields appear inline in the envelope
        claims = json.loads(base64.b64decode(doc.claims_raw))
        for moved_field in ("repository_url", "commit_hash", "script_path",
                            "script_env_hash", "security", "gpu"):
            assert moved_field not in envelope

        # And they DO appear in the claims document (repository_url et al.)
        assert claims["repository_url"] == "https://github.com/owner/repo"
        assert claims["commit_hash"] == "a" * 40
        assert claims["script_path"] == "build.sh"
        assert "script_env_hash" in claims
        # execution_id is envelope-only (D3) and must not be duplicated
        assert "execution_id" not in claims


class TestClaimsDigestBinding:
    """9.2: decode-then-hash of claims_raw reproduces claims_digest; tampering breaks it."""

    def test_decode_then_hash_reproduces_claims_digest(
        self, generator, mock_successful_attestation
    ):
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="b" * 40,
                script_path="build.sh",
                execution_id="exec-002",
            )

        assert error is None
        envelope = captured["user_data"]
        claims = _verify_binding(envelope, doc.claims_raw)
        assert claims["repository_url"] == "https://github.com/owner/repo"

    def test_tampering_with_claims_raw_breaks_the_check(
        self, generator, mock_successful_attestation
    ):
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="c" * 40,
                script_path="build.sh",
                execution_id="exec-003",
            )

        assert error is None
        envelope = captured["user_data"]

        # Tamper: flip a byte in the decoded claims, re-encode
        original_bytes = bytearray(base64.b64decode(doc.claims_raw))
        original_bytes[0] ^= 0xFF
        tampered_claims_raw = base64.b64encode(bytes(original_bytes)).decode("ascii")

        with pytest.raises(ValueError, match="claims_digest mismatch"):
            _verify_binding(envelope, tampered_claims_raw)

    def test_missing_claims_raw_rejected(self, generator, mock_successful_attestation):
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="d" * 40,
                script_path="build.sh",
            )

        assert error is None
        with pytest.raises(ValueError, match="missing claims_raw"):
            _verify_binding(captured["user_data"], None)


class TestSchemaVersionAndAlgorithmGating:
    """9.3: unknown MAJOR rejected; higher MINOR tolerated with unknown fields ignored;
    unknown digest algorithm rejected."""

    def test_current_schema_version_is_major_1(self):
        major, _, _minor = CLAIMS_SCHEMA_VERSION.partition(".")
        assert major == "1"

    def test_unknown_major_schema_version_rejected(
        self, generator, mock_successful_attestation
    ):
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="build.sh",
            )

        assert error is None
        envelope = captured["user_data"]

        # Simulate a future MAJOR-bumped claims document: rewrite schema_version,
        # re-serialize, and re-bind so the envelope/claims pair is self-consistent
        # except for the version a verifier built for MAJOR "1" doesn't recognize.
        claims = json.loads(base64.b64decode(doc.claims_raw))
        claims["schema_version"] = "2.0"
        new_bytes = json.dumps(claims).encode("utf-8")
        new_claims_raw = base64.b64encode(new_bytes).decode("ascii")
        envelope["claims_digest"] = "sha256:" + hashlib.sha256(new_bytes).hexdigest()

        with pytest.raises(ValueError, match="unknown MAJOR schema_version"):
            _verify_binding(envelope, new_claims_raw, known_major="1")

    def test_higher_minor_tolerated_with_unknown_fields_ignored(
        self, generator, mock_successful_attestation
    ):
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="build.sh",
            )

        assert error is None
        envelope = captured["user_data"]

        # Simulate an additive MINOR bump: same MAJOR, one new field a verifier
        # built for the old MINOR doesn't recognize.
        claims = json.loads(base64.b64decode(doc.claims_raw))
        claims["schema_version"] = "1.1"
        claims["a_future_optional_field"] = "some-value"
        new_bytes = json.dumps(claims).encode("utf-8")
        new_claims_raw = base64.b64encode(new_bytes).decode("ascii")
        envelope["claims_digest"] = "sha256:" + hashlib.sha256(new_bytes).hexdigest()

        # Binding + version check succeed; a verifier reads known fields and
        # ignores the new one rather than rejecting.
        verified_claims = _verify_binding(envelope, new_claims_raw, known_major="1")
        assert verified_claims["repository_url"] == "https://github.com/owner/repo"
        assert verified_claims["a_future_optional_field"] == "some-value"

    def test_unknown_digest_algorithm_rejected(self, generator, mock_successful_attestation):
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="build.sh",
            )

        assert error is None
        envelope = captured["user_data"]
        envelope["claims_digest"] = "md5:" + envelope["claims_digest"].split(":", 1)[1]

        with pytest.raises(ValueError, match="unknown digest algorithm"):
            _verify_binding(envelope, doc.claims_raw)


class TestOutputDigestInjectivity:
    """9.5: output_digest binds canonical JSON, so delimiter-injection cannot forge a collision (D11)."""

    def test_delimiter_injection_does_not_collide(self, generator):
        """The old delimiter-glued 'stdout:...\\nstderr:...\\nexit_code:...' scheme let
        a stdout/stderr value containing those literal delimiters collide with a
        different genuine triple. The canonical-JSON output_digest must not."""
        # Two distinct triples that collided under the old glued-string scheme:
        triple_a = ("hello\nstderr:oops\nexit_code:1", "", 0)
        triple_b = ("hello", "oops\nexit_code:1\nstderr:", 0)

        old_glued_a = f"stdout:{triple_a[0]}\nstderr:{triple_a[1]}\nexit_code:{triple_a[2]}"
        old_glued_b = f"stdout:{triple_b[0]}\nstderr:{triple_b[1]}\nexit_code:{triple_b[2]}"
        assert old_glued_a == old_glued_b, "sanity check: these must collide under the old scheme"

        def digest_for(stdout, stderr, exit_code):
            canonical = json.dumps(
                {"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
                sort_keys=True, separators=(',', ':'),
            )
            return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        digest_a = digest_for(*triple_a)
        digest_b = digest_for(*triple_b)
        assert digest_a != digest_b, (
            "canonical JSON output_digest must NOT collide for distinct "
            "(stdout, stderr, exit_code) triples, even when one embeds the other's delimiters"
        )

    def test_generate_output_attestation_produces_injective_digest(self, generator):
        """End-to-end: generate_output_attestation's output_digest differs for the
        two colliding-under-old-scheme triples."""
        def run_and_get_digest(stdout, stderr, exit_code):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0, stdout=b"cbor", stderr=b"")
                result, error = generator.generate_output_attestation(stdout, stderr, exit_code)
            assert error is None
            claims = json.loads(base64.b64decode(result.claims_raw))
            return claims["output_digest"]

        digest_a = run_and_get_digest("hello\nstderr:oops\nexit_code:1", "", 0)
        digest_b = run_and_get_digest("hello", "oops\nexit_code:1\nstderr:", 0)
        assert digest_a != digest_b

    def test_output_attestation_uses_same_envelope_shape(self, generator):
        """The output attestation's user_data uses the same { v, claims_digest, timestamp,
        execution_id } envelope as the execution attestation (D8)."""
        captured = {}

        def capture_and_run(cmd, **kwargs):
            if "--user-data" in cmd:
                with open(cmd[cmd.index("--user-data") + 1], "r") as f:
                    captured["user_data"] = json.load(f)
            return Mock(returncode=0, stdout=b"cbor", stderr=b"")

        with patch("subprocess.run", side_effect=capture_and_run):
            result, error = generator.generate_output_attestation(
                "out", "err", 0, execution_id="exec-output-envelope"
            )

        assert error is None
        assert set(captured["user_data"].keys()) == {"v", "claims_digest", "timestamp", "execution_id"}
        assert captured["user_data"]["execution_id"] == "exec-output-envelope"
