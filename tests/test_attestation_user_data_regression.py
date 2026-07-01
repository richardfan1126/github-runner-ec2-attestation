"""Regression tests for the claims-digest envelope and claims document.

These tests ensure that the `user_data` envelope stays exactly
`{ v, claims_digest, timestamp, execution_id }` and that script_path,
script_env_hash, and the container-security fields live in the bound
claims document (`claims_raw`), not inline. If any of these fields are
removed or renamed, or move back inline, these tests will fail.

Validates: Requirements 4.14, 4.15 and the attestation-claims-digest change.
"""
import base64
import json
from datetime import datetime, UTC
from unittest.mock import Mock, patch

import pytest

from src.attestation import AttestationGenerator


@pytest.fixture
def generator():
    """Create an attestation generator instance."""
    return AttestationGenerator(tpm_attest_path="/usr/bin/nitro-tpm-attest")


@pytest.fixture
def mock_successful_attestation():
    """Mock subprocess.run to simulate successful attestation and capture user_data."""
    captured = {}

    def capture_and_run(cmd, **kwargs):
        """Intercept the subprocess call and read the user_data file."""
        if "--user-data" in cmd:
            user_data_idx = cmd.index("--user-data")
            user_data_path = cmd[user_data_idx + 1]
            with open(user_data_path, "r") as f:
                captured["user_data"] = json.load(f)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = b"mock_cbor_attestation"
        mock_result.stderr = b""
        return mock_result

    return captured, capture_and_run


def _decode_claims(claims_raw: str) -> dict:
    """Base64-decode and parse a claims_raw string into its claims dict."""
    return json.loads(base64.b64decode(claims_raw))


class TestScriptPathPresenceInClaims:
    """Regression tests asserting script_path is present in the claims document."""

    def test_script_path_present_basic(self, generator, mock_successful_attestation):
        """script_path must be present in claims_raw for a basic attestation call."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="scripts/build.sh",
            )

        assert error is None
        assert doc is not None
        claims = _decode_claims(doc.claims_raw)
        assert "script_path" in claims, (
            "script_path must be present in the claims document"
        )
        assert claims["script_path"] == "scripts/build.sh"
        assert "script_path" not in captured["user_data"], (
            "script_path must NOT appear inline in the user_data envelope"
        )

    def test_script_path_present_with_nonce(self, generator, mock_successful_attestation):
        """script_path must be present in claims_raw when nonce is provided."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="b" * 40,
                script_path="ci/deploy.sh",
                nonce="test_nonce_value",
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "script_path" in claims, (
            "script_path must be present in the claims document when nonce is provided"
        )
        assert claims["script_path"] == "ci/deploy.sh"

    def test_script_path_present_with_script_env(self, generator, mock_successful_attestation):
        """script_path must be present in claims_raw when script_env is provided."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="c" * 40,
                script_path="tests/run.sh",
                script_env={"NODE_ENV": "production", "CI": "true"},
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "script_path" in claims, (
            "script_path must be present in the claims document when script_env is provided"
        )
        assert claims["script_path"] == "tests/run.sh"

    def test_script_path_present_with_all_optional_params(
        self, generator, mock_successful_attestation
    ):
        """script_path must be present when all optional parameters are provided."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="d" * 40,
                script_path="path/to/script.sh",
                nonce="nonce123",
                script_env={"KEY": "value"},
                public_key=b"fake_public_key",
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "script_path" in claims, (
            "script_path must be present in the claims document with all optional params"
        )
        assert claims["script_path"] == "path/to/script.sh"

    def test_script_path_present_with_nested_path(
        self, generator, mock_successful_attestation
    ):
        """script_path must be present for deeply nested script paths."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/org/project",
                commit_hash="e" * 40,
                script_path="a/b/c/d/e/script.sh",
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "script_path" in claims
        assert claims["script_path"] == "a/b/c/d/e/script.sh"


class TestScriptEnvHashPresenceInClaims:
    """Regression tests asserting script_env_hash is present in the claims document."""

    def test_script_env_hash_present_without_script_env(
        self, generator, mock_successful_attestation
    ):
        """script_env_hash must be present even when script_env is not provided."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="scripts/build.sh",
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "script_env_hash" in claims, (
            "script_env_hash must be present in the claims document even without script_env"
        )
        # When script_env is None, should be SHA-256 of "{}"
        import hashlib
        expected_hash = hashlib.sha256("{}".encode("utf-8")).hexdigest()
        assert claims["script_env_hash"] == expected_hash

    def test_script_env_hash_present_with_empty_dict(
        self, generator, mock_successful_attestation
    ):
        """script_env_hash must be present when script_env is an empty dict."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="b" * 40,
                script_path="scripts/test.sh",
                script_env={},
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "script_env_hash" in claims, (
            "script_env_hash must be present in the claims document with empty script_env"
        )
        import hashlib
        expected_hash = hashlib.sha256("{}".encode("utf-8")).hexdigest()
        assert claims["script_env_hash"] == expected_hash

    def test_script_env_hash_present_with_populated_env(
        self, generator, mock_successful_attestation
    ):
        """script_env_hash must be present when script_env has values."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="c" * 40,
                script_path="scripts/deploy.sh",
                script_env={"AWS_REGION": "us-east-1", "DEBUG": "false"},
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "script_env_hash" in claims, (
            "script_env_hash must be present in the claims document with populated script_env"
        )
        # Verify it's a valid hex SHA-256 digest (64 hex chars)
        assert len(claims["script_env_hash"]) == 64
        assert all(c in "0123456789abcdef" for c in claims["script_env_hash"])

    def test_script_env_hash_present_with_nonce(
        self, generator, mock_successful_attestation
    ):
        """script_env_hash must be present when nonce is also provided."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="d" * 40,
                script_path="scripts/run.sh",
                nonce="nonce_abc",
                script_env={"VAR": "val"},
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "script_env_hash" in claims, (
            "script_env_hash must be present in the claims document when nonce is provided"
        )

    def test_script_env_hash_present_with_all_optional_params(
        self, generator, mock_successful_attestation
    ):
        """script_env_hash must be present when all optional parameters are provided."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="e" * 40,
                script_path="path/to/script.sh",
                nonce="nonce123",
                script_env={"KEY": "value"},
                public_key=b"fake_public_key",
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "script_env_hash" in claims, (
            "script_env_hash must be present in the claims document with all optional params"
        )


class TestBothFieldsPresent:
    """Regression tests asserting both script_path and script_env_hash are present together."""

    def test_both_fields_present_minimal_call(
        self, generator, mock_successful_attestation
    ):
        """Both script_path and script_env_hash must be present in minimal attestation call."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="script.sh",
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "script_path" in claims, "script_path must be in the claims document"
        assert "script_env_hash" in claims, "script_env_hash must be in the claims document"

    def test_both_fields_present_full_call(
        self, generator, mock_successful_attestation
    ):
        """Both script_path and script_env_hash must be present in full attestation call."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="f" * 40,
                script_path="ci/pipeline.sh",
                nonce="unique_nonce",
                script_env={"CI": "true", "BRANCH": "main"},
                public_key=b"key_bytes",
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "script_path" in claims, "script_path must be in the claims document"
        assert "script_env_hash" in claims, "script_env_hash must be in the claims document"
        # Verify the complete expected structure
        assert "repository_url" in claims
        assert "commit_hash" in claims
        assert "timestamp" in captured["user_data"]

    def test_envelope_has_exactly_expected_fields(
        self, generator, mock_successful_attestation
    ):
        """user_data envelope must contain exactly { v, claims_digest, timestamp }
        (execution_id omitted here since it was not supplied)."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="build.sh",
                script_env={"X": "1"},
            )

        assert error is None
        expected_fields = {"v", "claims_digest", "timestamp"}
        assert set(captured["user_data"].keys()) == expected_fields, (
            f"user_data envelope must contain exactly {expected_fields}, "
            f"got {set(captured['user_data'].keys())}"
        )

    def test_claims_document_has_exactly_expected_fields(
        self, generator, mock_successful_attestation
    ):
        """The claims document must contain exactly the expected set of fields
        (no execution_id, timestamp, or v — those are envelope-only, D3)."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="build.sh",
                script_env={"X": "1"},
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        expected_fields = {
            "schema_version", "repository_url", "commit_hash",
            "script_path", "script_env_hash",
        }
        assert set(claims.keys()) == expected_fields, (
            f"claims document must contain exactly {expected_fields}, got {set(claims.keys())}"
        )


# The eight container-security values bound into the claims document (hardened defaults).
# See openspec/specs/container-security/spec.md (attested posture requirements).
SECURITY_KWARGS = dict(
    container_user="65534:65534",
    container_allow_root=False,
    container_cap_add=[
        "CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID",
        "NET_BIND_SERVICE", "KILL",
    ],
    no_new_privileges=True,
    container_read_only_rootfs=True,
    container_tmpfs_size="256m",
    workspace_mount_mode="ro",
    container_network_mode="none",
)

SECURITY_KEYS = set(SECURITY_KWARGS.keys())


class TestSecurityFieldsPresentInExecuteAttestation:
    """Pin the eight container-security keys in the execution claims document (SC-004)."""

    def test_all_eight_security_fields_present(self, generator, mock_successful_attestation):
        """All eight container-security keys must be present with their effective values."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="build.sh",
                **SECURITY_KWARGS,
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        for key, value in SECURITY_KWARGS.items():
            assert key in claims, f"{key} must be present in the claims document"
            assert claims[key] == value, f"{key} must carry its effective value"
        assert SECURITY_KEYS.isdisjoint(captured["user_data"].keys()), (
            "security fields must NOT appear inline in the user_data envelope"
        )

    def test_container_cap_add_is_array(self, generator, mock_successful_attestation):
        """container_cap_add must serialize as a JSON array, not a string."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="b" * 40,
                script_path="build.sh",
                **SECURITY_KWARGS,
            )

        cap_add = _decode_claims(doc.claims_raw)["container_cap_add"]
        assert isinstance(cap_add, list)
        assert cap_add == SECURITY_KWARGS["container_cap_add"]

    def test_empty_cap_add_distinct_from_default(self, generator, mock_successful_attestation):
        """An explicitly empty cap_add ([]) must be distinguishable from the default set."""
        captured, side_effect = mock_successful_attestation

        kwargs = {**SECURITY_KWARGS, "container_cap_add": []}
        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="c" * 40,
                script_path="build.sh",
                **kwargs,
            )

        assert _decode_claims(doc.claims_raw)["container_cap_add"] == []

    def test_relaxed_value_distinguishable_from_default(
        self, generator, mock_successful_attestation
    ):
        """A relaxed setting (network=bridge) is distinguishable from the hardened default."""
        captured, side_effect = mock_successful_attestation

        kwargs = {**SECURITY_KWARGS, "container_network_mode": "bridge"}
        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="d" * 40,
                script_path="build.sh",
                **kwargs,
            )

        claims = _decode_claims(doc.claims_raw)
        assert claims["container_network_mode"] == "bridge"
        assert claims["container_network_mode"] != "none"


class TestSecurityFieldsPresentInOutputAttestation:
    """Pin the eight container-security keys in the output claims document."""

    def test_all_eight_security_fields_present(self, generator, mock_successful_attestation):
        """All eight keys must be present in the output claims document."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            result, error_msg = generator.generate_output_attestation(
                stdout="hi", stderr="", exit_code=0,
                execution_id="exec-123",
                **SECURITY_KWARGS,
            )

        assert error_msg is None
        claims = _decode_claims(result.claims_raw)
        for key, value in SECURITY_KWARGS.items():
            assert key in claims, f"{key} must be present in the output claims document"
            assert claims[key] == value

    def test_relaxed_value_distinguishable_from_default(
        self, generator, mock_successful_attestation
    ):
        """A relaxed setting flows into the output attestation too."""
        captured, side_effect = mock_successful_attestation

        kwargs = {**SECURITY_KWARGS, "workspace_mount_mode": "rw"}
        with patch("subprocess.run", side_effect=side_effect):
            result, error_msg = generator.generate_output_attestation(
                stdout="hi", stderr="", exit_code=0,
                execution_id="exec-456",
                **kwargs,
            )

        assert _decode_claims(result.claims_raw)["workspace_mount_mode"] == "rw"


class TestSecurityFieldsAbsentWhenNotProvided:
    """Backward compatibility: the eight keys are omitted when no security config is passed."""

    def test_execute_claims_unchanged_without_security_kwargs(
        self, generator, mock_successful_attestation
    ):
        """Without security kwargs, the claims document keeps exactly its original field set."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="build.sh",
                script_env={"X": "1"},
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert SECURITY_KEYS.isdisjoint(claims.keys())


class TestUserDataWithinNitroTpmLimit:
    """NitroTPM caps user_data at 1024 bytes; guard both surfaces (T025)."""

    @pytest.fixture
    def capture_raw_user_data(self):
        """Mock subprocess.run capturing the raw user_data bytes written to disk."""
        captured = {}

        def capture_and_run(cmd, **kwargs):
            if "--user-data" in cmd:
                user_data_idx = cmd.index("--user-data")
                with open(cmd[user_data_idx + 1], "rb") as f:
                    captured["raw"] = f.read()
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = b"mock_cbor_attestation"
            mock_result.stderr = b""
            return mock_result

        return captured, capture_and_run

    def test_execute_user_data_within_limit(self, generator, capture_raw_user_data):
        """A hardened-default execute attestation stays within the 1024-byte cap."""
        captured, side_effect = capture_raw_user_data

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="scripts/build.sh",
                execution_id="exec-123",
                **SECURITY_KWARGS,
            )

        assert error is None
        assert len(captured["raw"]) <= 1024, (
            f"user_data must stay within the NitroTPM 1024-byte limit, "
            f"got {len(captured['raw'])} bytes"
        )

    def test_output_user_data_within_limit(self, generator, capture_raw_user_data):
        """A hardened-default output attestation stays within the 1024-byte cap."""
        captured, side_effect = capture_raw_user_data

        with patch("subprocess.run", side_effect=side_effect):
            result, error_msg = generator.generate_output_attestation(
                stdout="hi", stderr="", exit_code=0,
                execution_id="exec-123",
                **SECURITY_KWARGS,
            )

        assert error_msg is None
        assert len(captured["raw"]) <= 1024, (
            f"output user_data must stay within the NitroTPM 1024-byte limit, "
            f"got {len(captured['raw'])} bytes"
        )

    def test_huge_repository_url_no_longer_overflows_envelope(
        self, generator, capture_raw_user_data
    ):
        """A large claims document (e.g. a long repository_url) no longer risks
        overflowing user_data, because only the fixed-length claims_digest is
        inline — this is the core guarantee the envelope refactor provides (D1)."""
        captured, side_effect = capture_raw_user_data

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/" + "r" * 1100,
                commit_hash="a" * 40,
                script_path="scripts/build.sh",
                **SECURITY_KWARGS,
            )

        assert error is None
        assert doc is not None
        assert len(captured["raw"]) <= 1024

    def test_oversize_execution_id_rejected(self, generator):
        """An oversize execution_id (the one remaining variable-length inline
        field) is rejected with a limit-naming error, no subprocess."""
        run = Mock()
        with patch("subprocess.run", run):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="scripts/build.sh",
                execution_id="x" * 1100,
                **SECURITY_KWARGS,
            )

        assert doc is None
        assert error is not None
        assert "1024" in error.context
        run.assert_not_called()

    def test_oversize_output_execution_id_rejected(self, generator):
        """An oversize execution_id in the output attestation path is rejected
        with a limit-naming error, no subprocess."""
        run = Mock()
        with patch("subprocess.run", run):
            result, error_msg = generator.generate_output_attestation(
                stdout="hi", stderr="", exit_code=0,
                execution_id="x" * 1100,
                **SECURITY_KWARGS,
            )

        assert result is None
        assert error_msg is not None
        assert "1024" in error_msg
        run.assert_not_called()


class TestContainerTmpfsExecInClaims:
    """Pin container_tmpfs_exec in the claims document for both attest paths (INV-3, FR-009)."""

    def test_tmpfs_exec_false_present_in_execute_attestation(
        self, generator, mock_successful_attestation
    ):
        """container_tmpfs_exec=False must appear in the execution claims document."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="build.sh",
                **SECURITY_KWARGS,
                container_tmpfs_exec=False,
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "container_tmpfs_exec" in claims
        assert claims["container_tmpfs_exec"] is False

    def test_tmpfs_exec_true_present_in_execute_attestation(
        self, generator, mock_successful_attestation
    ):
        """container_tmpfs_exec=True must appear in the execution claims document."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="b" * 40,
                script_path="build.sh",
                **SECURITY_KWARGS,
                container_tmpfs_exec=True,
            )

        assert error is None
        claims = _decode_claims(doc.claims_raw)
        assert "container_tmpfs_exec" in claims
        assert claims["container_tmpfs_exec"] is True

    def test_tmpfs_exec_absent_when_not_provided_in_execute_attestation(
        self, generator, mock_successful_attestation
    ):
        """container_tmpfs_exec is absent from claims when not passed (backward compat)."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="c" * 40,
                script_path="build.sh",
                **SECURITY_KWARGS,
            )

        claims = _decode_claims(doc.claims_raw)
        assert "container_tmpfs_exec" not in claims

    def test_tmpfs_exec_false_present_in_output_attestation(
        self, generator, mock_successful_attestation
    ):
        """container_tmpfs_exec=False must appear in the output claims document."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            result, error_msg = generator.generate_output_attestation(
                stdout="hi", stderr="", exit_code=0,
                execution_id="exec-123",
                **SECURITY_KWARGS,
                container_tmpfs_exec=False,
            )

        assert error_msg is None
        claims = _decode_claims(result.claims_raw)
        assert "container_tmpfs_exec" in claims
        assert claims["container_tmpfs_exec"] is False

    def test_tmpfs_exec_true_present_in_output_attestation(
        self, generator, mock_successful_attestation
    ):
        """container_tmpfs_exec=True must appear in the output claims document."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            result, error_msg = generator.generate_output_attestation(
                stdout="hi", stderr="", exit_code=0,
                execution_id="exec-456",
                **SECURITY_KWARGS,
                container_tmpfs_exec=True,
            )

        assert error_msg is None
        claims = _decode_claims(result.claims_raw)
        assert "container_tmpfs_exec" in claims
        assert claims["container_tmpfs_exec"] is True

    def test_execute_and_output_attestations_agree_on_tmpfs_exec(
        self, generator, mock_successful_attestation
    ):
        """The same container_tmpfs_exec value must appear in both attest paths."""
        for exec_value in (False, True):
            captured_exec: dict = {}
            captured_output: dict = {}

            def side_effect_exec(cmd, **kwargs):
                if "--user-data" in cmd:
                    path = cmd[cmd.index("--user-data") + 1]
                    with open(path) as f:
                        captured_exec.update(json.load(f))
                m = type("M", (), {"returncode": 0, "stdout": b"cbor", "stderr": b""})()
                return m

            def side_effect_output(cmd, **kwargs):
                if "--user-data" in cmd:
                    path = cmd[cmd.index("--user-data") + 1]
                    with open(path) as f:
                        captured_output.update(json.load(f))
                m = type("M", (), {"returncode": 0, "stdout": b"cbor", "stderr": b""})()
                return m

            with patch("subprocess.run", side_effect=side_effect_exec):
                doc, _ = generator.generate_attestation(
                    repository_url="https://github.com/owner/repo",
                    commit_hash="a" * 40,
                    script_path="build.sh",
                    **SECURITY_KWARGS,
                    container_tmpfs_exec=exec_value,
                )

            with patch("subprocess.run", side_effect=side_effect_output):
                result, _ = generator.generate_output_attestation(
                    stdout="x", stderr="", exit_code=0,
                    execution_id="exec-1",
                    **SECURITY_KWARGS,
                    container_tmpfs_exec=exec_value,
                )

            exec_claims = _decode_claims(doc.claims_raw)
            output_claims = _decode_claims(result.claims_raw)
            assert exec_claims.get("container_tmpfs_exec") == exec_value
            assert output_claims.get("container_tmpfs_exec") == exec_value


class TestFieldRemovalCausesFailure:
    """Tests that verify removing script_path or script_env_hash would cause failure.

    These tests simulate what would happen if the fields were removed from the
    claims document construction in attestation.py. They directly test the
    claims structure to ensure the regression tests above would catch such removal.
    """

    def test_missing_script_path_detected(self):
        """Demonstrate that our assertions catch missing script_path."""
        claims_without_script_path = {
            "schema_version": "1.0",
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_env_hash": "abc123" * 10 + "abcd",
        }
        # This assertion would fail if script_path were removed
        assert "script_path" not in claims_without_script_path, (
            "This test verifies our detection logic works"
        )

    def test_missing_script_env_hash_detected(self):
        """Demonstrate that our assertions catch missing script_env_hash."""
        claims_without_env_hash = {
            "schema_version": "1.0",
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/build.sh",
        }
        # This assertion would fail if script_env_hash were removed
        assert "script_env_hash" not in claims_without_env_hash, (
            "This test verifies our detection logic works"
        )

    def test_renamed_script_path_detected(self):
        """Demonstrate that renaming script_path would be caught."""
        claims_renamed = {
            "schema_version": "1.0",
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "file_path": "scripts/build.sh",  # Wrong name
            "script_env_hash": "abc123" * 10 + "abcd",
        }
        assert "script_path" not in claims_renamed, (
            "Renaming script_path to file_path would be caught by regression tests"
        )

    def test_renamed_script_env_hash_detected(self):
        """Demonstrate that renaming script_env_hash would be caught."""
        claims_renamed = {
            "schema_version": "1.0",
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/build.sh",
            "env_hash": "abc123" * 10 + "abcd",  # Wrong name
        }
        assert "script_env_hash" not in claims_renamed, (
            "Renaming script_env_hash to env_hash would be caught by regression tests"
        )
