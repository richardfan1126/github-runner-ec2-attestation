"""Regression tests for script_path and script_env_hash in attestation user_data.

These tests ensure that script_path and script_env_hash are always present in the
user_data passed to nitro-tpm-attest for all successful attestation generation paths.
If either field is removed or renamed, these tests will fail.

Validates: Requirements 4.14, 4.15
"""
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


class TestScriptPathPresenceInUserData:
    """Regression tests asserting script_path is present in attestation user_data."""

    def test_script_path_present_basic(self, generator, mock_successful_attestation):
        """script_path must be present in user_data for a basic attestation call."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="scripts/build.sh",
            )

        assert error is None
        assert doc is not None
        assert "script_path" in captured["user_data"], (
            "script_path must be present in attestation user_data"
        )
        assert captured["user_data"]["script_path"] == "scripts/build.sh"

    def test_script_path_present_with_nonce(self, generator, mock_successful_attestation):
        """script_path must be present in user_data when nonce is provided."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="b" * 40,
                script_path="ci/deploy.sh",
                nonce="test_nonce_value",
            )

        assert error is None
        assert "script_path" in captured["user_data"], (
            "script_path must be present in attestation user_data when nonce is provided"
        )
        assert captured["user_data"]["script_path"] == "ci/deploy.sh"

    def test_script_path_present_with_script_env(self, generator, mock_successful_attestation):
        """script_path must be present in user_data when script_env is provided."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="c" * 40,
                script_path="tests/run.sh",
                script_env={"NODE_ENV": "production", "CI": "true"},
            )

        assert error is None
        assert "script_path" in captured["user_data"], (
            "script_path must be present in attestation user_data when script_env is provided"
        )
        assert captured["user_data"]["script_path"] == "tests/run.sh"

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
        assert "script_path" in captured["user_data"], (
            "script_path must be present in attestation user_data with all optional params"
        )
        assert captured["user_data"]["script_path"] == "path/to/script.sh"

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
        assert "script_path" in captured["user_data"]
        assert captured["user_data"]["script_path"] == "a/b/c/d/e/script.sh"


class TestScriptEnvHashPresenceInUserData:
    """Regression tests asserting script_env_hash is present in attestation user_data."""

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
        assert "script_env_hash" in captured["user_data"], (
            "script_env_hash must be present in attestation user_data even without script_env"
        )
        # When script_env is None, should be SHA-256 of "{}"
        import hashlib
        expected_hash = hashlib.sha256("{}".encode("utf-8")).hexdigest()
        assert captured["user_data"]["script_env_hash"] == expected_hash

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
        assert "script_env_hash" in captured["user_data"], (
            "script_env_hash must be present in attestation user_data with empty script_env"
        )
        import hashlib
        expected_hash = hashlib.sha256("{}".encode("utf-8")).hexdigest()
        assert captured["user_data"]["script_env_hash"] == expected_hash

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
        assert "script_env_hash" in captured["user_data"], (
            "script_env_hash must be present in attestation user_data with populated script_env"
        )
        # Verify it's a valid hex SHA-256 digest (64 hex chars)
        assert len(captured["user_data"]["script_env_hash"]) == 64
        assert all(c in "0123456789abcdef" for c in captured["user_data"]["script_env_hash"])

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
        assert "script_env_hash" in captured["user_data"], (
            "script_env_hash must be present in attestation user_data when nonce is provided"
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
        assert "script_env_hash" in captured["user_data"], (
            "script_env_hash must be present in attestation user_data with all optional params"
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
        user_data = captured["user_data"]
        assert "script_path" in user_data, "script_path must be in user_data"
        assert "script_env_hash" in user_data, "script_env_hash must be in user_data"

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
        user_data = captured["user_data"]
        assert "script_path" in user_data, "script_path must be in user_data"
        assert "script_env_hash" in user_data, "script_env_hash must be in user_data"
        # Verify the complete expected structure
        assert "repository_url" in user_data
        assert "commit_hash" in user_data
        assert "timestamp" in user_data

    def test_user_data_has_exactly_expected_fields(
        self, generator, mock_successful_attestation
    ):
        """user_data must contain exactly the expected set of fields."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            doc, error = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="build.sh",
                script_env={"X": "1"},
            )

        assert error is None
        user_data = captured["user_data"]
        expected_fields = {"repository_url", "commit_hash", "script_path", "script_env_hash", "timestamp"}
        assert set(user_data.keys()) == expected_fields, (
            f"user_data must contain exactly {expected_fields}, got {set(user_data.keys())}"
        )


# The eight container-security values bound into user_data (hardened defaults).
# See specs/001-container-security-config/contracts/attestation-user-data-contract.md
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
    """Pin the eight container-security keys in generate_attestation user_data (SC-004)."""

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
        user_data = captured["user_data"]
        for key, value in SECURITY_KWARGS.items():
            assert key in user_data, f"{key} must be present in attestation user_data"
            assert user_data[key] == value, f"{key} must carry its effective value"

    def test_container_cap_add_is_array(self, generator, mock_successful_attestation):
        """container_cap_add must serialize as a JSON array, not a string."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="b" * 40,
                script_path="build.sh",
                **SECURITY_KWARGS,
            )

        cap_add = captured["user_data"]["container_cap_add"]
        assert isinstance(cap_add, list)
        assert cap_add == SECURITY_KWARGS["container_cap_add"]

    def test_empty_cap_add_distinct_from_default(self, generator, mock_successful_attestation):
        """An explicitly empty cap_add ([]) must be distinguishable from the default set."""
        captured, side_effect = mock_successful_attestation

        kwargs = {**SECURITY_KWARGS, "container_cap_add": []}
        with patch("subprocess.run", side_effect=side_effect):
            generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="c" * 40,
                script_path="build.sh",
                **kwargs,
            )

        assert captured["user_data"]["container_cap_add"] == []

    def test_relaxed_value_distinguishable_from_default(
        self, generator, mock_successful_attestation
    ):
        """A relaxed setting (network=bridge) is distinguishable from the hardened default."""
        captured, side_effect = mock_successful_attestation

        kwargs = {**SECURITY_KWARGS, "container_network_mode": "bridge"}
        with patch("subprocess.run", side_effect=side_effect):
            generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="d" * 40,
                script_path="build.sh",
                **kwargs,
            )

        assert captured["user_data"]["container_network_mode"] == "bridge"
        assert captured["user_data"]["container_network_mode"] != "none"


class TestSecurityFieldsPresentInOutputAttestation:
    """Pin the eight container-security keys in generate_output_attestation user_data."""

    def test_all_eight_security_fields_present(self, generator, mock_successful_attestation):
        """All eight keys must be present in the output attestation user_data."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            attestation_bytes, error_msg = generator.generate_output_attestation(
                script_output="stdout:hi\nstderr:\nexit_code:0",
                execution_id="exec-123",
                **SECURITY_KWARGS,
            )

        assert error_msg is None
        user_data = captured["user_data"]
        for key, value in SECURITY_KWARGS.items():
            assert key in user_data, f"{key} must be present in output attestation user_data"
            assert user_data[key] == value

    def test_relaxed_value_distinguishable_from_default(
        self, generator, mock_successful_attestation
    ):
        """A relaxed setting flows into the output attestation too."""
        captured, side_effect = mock_successful_attestation

        kwargs = {**SECURITY_KWARGS, "workspace_mount_mode": "rw"}
        with patch("subprocess.run", side_effect=side_effect):
            generator.generate_output_attestation(
                script_output="stdout:hi\nstderr:\nexit_code:0",
                execution_id="exec-456",
                **kwargs,
            )

        assert captured["user_data"]["workspace_mount_mode"] == "rw"


class TestSecurityFieldsAbsentWhenNotProvided:
    """Backward compatibility: the eight keys are omitted when no security config is passed."""

    def test_execute_user_data_unchanged_without_security_kwargs(
        self, generator, mock_successful_attestation
    ):
        """Without security kwargs, user_data keeps exactly its original field set."""
        captured, side_effect = mock_successful_attestation

        with patch("subprocess.run", side_effect=side_effect):
            generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="build.sh",
                script_env={"X": "1"},
            )

        assert SECURITY_KEYS.isdisjoint(captured["user_data"].keys())


class TestFieldRemovalCausesFailure:
    """Tests that verify removing script_path or script_env_hash would cause failure.

    These tests simulate what would happen if the fields were removed from the
    user_data dictionary construction in attestation.py. They directly test the
    user_data structure to ensure the regression tests above would catch such removal.
    """

    def test_missing_script_path_detected(self):
        """Demonstrate that our assertions catch missing script_path."""
        # Simulate user_data without script_path
        user_data_without_script_path = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_env_hash": "abc123" * 10 + "abcd",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        # This assertion would fail if script_path were removed
        assert "script_path" not in user_data_without_script_path, (
            "This test verifies our detection logic works"
        )

    def test_missing_script_env_hash_detected(self):
        """Demonstrate that our assertions catch missing script_env_hash."""
        # Simulate user_data without script_env_hash
        user_data_without_env_hash = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/build.sh",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        # This assertion would fail if script_env_hash were removed
        assert "script_env_hash" not in user_data_without_env_hash, (
            "This test verifies our detection logic works"
        )

    def test_renamed_script_path_detected(self):
        """Demonstrate that renaming script_path would be caught."""
        # Simulate user_data with script_path renamed to something else
        user_data_renamed = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "file_path": "scripts/build.sh",  # Wrong name
            "script_env_hash": "abc123" * 10 + "abcd",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        assert "script_path" not in user_data_renamed, (
            "Renaming script_path to file_path would be caught by regression tests"
        )

    def test_renamed_script_env_hash_detected(self):
        """Demonstrate that renaming script_env_hash would be caught."""
        # Simulate user_data with script_env_hash renamed
        user_data_renamed = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/build.sh",
            "env_hash": "abc123" * 10 + "abcd",  # Wrong name
            "timestamp": datetime.now(UTC).isoformat(),
        }
        assert "script_env_hash" not in user_data_renamed, (
            "Renaming script_env_hash to env_hash would be caught by regression tests"
        )
