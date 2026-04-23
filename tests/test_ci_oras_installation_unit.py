"""
Unit tests for CI ORAS installation in the GitHub Actions workflow.

Validates that:
- The workflow ORAS version matches the version in build-ami.py
- The workflow contains a sha256sum verification step for the ORAS download
- The checksum value in the workflow matches the one in build-ami.py

Requirements: 17.13, 17.14
"""

import importlib.util
import inspect
import re
import sys
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
WORKFLOW_FILE = REPO_ROOT / ".github" / "workflows" / "build-attestable-image.yml"
SCRIPTS_DIR = REPO_ROOT / "scripts"

# ---------------------------------------------------------------------------
# Import build_ami module (filename contains a hyphen)
# ---------------------------------------------------------------------------

_spec = importlib.util.spec_from_file_location("build_ami", SCRIPTS_DIR / "build-ami.py")
build_ami = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_ami)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def workflow_yaml() -> dict:
    """Load and parse the build-attestable-image.yml workflow."""
    assert WORKFLOW_FILE.exists(), f"Workflow file not found: {WORKFLOW_FILE}"
    with open(WORKFLOW_FILE) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def install_oras_step(workflow_yaml) -> dict:
    """Return the 'Install ORAS' step from the workflow."""
    for job in workflow_yaml.get("jobs", {}).values():
        for step in job.get("steps", []):
            if step.get("name", "").strip().lower() == "install oras":
                return step
    pytest.fail("'Install ORAS' step not found in workflow")


@pytest.fixture(scope="module")
def install_oras_run_script(install_oras_step) -> str:
    """Return the shell script body of the 'Install ORAS' step."""
    return install_oras_step.get("run", "")


@pytest.fixture(scope="module")
def build_ami_install_oras_source() -> str:
    """Return the source code of install_oras() from build-ami.py."""
    return inspect.getsource(build_ami.install_oras)


# ---------------------------------------------------------------------------
# Helper extractors
# ---------------------------------------------------------------------------

def _extract_version_from_script(script: str) -> str:
    """Extract VERSION=X.Y.Z from a shell script."""
    match = re.search(r'VERSION=["\']?(\d+\.\d+\.\d+)["\']?', script)
    assert match, f"Could not find VERSION=X.Y.Z in script:\n{script}"
    return match.group(1)


def _extract_version_from_python(source: str) -> str:
    """Extract oras_version = 'X.Y.Z' from Python source."""
    match = re.search(r'oras_version\s*=\s*["\'](\d+\.\d+\.\d+)["\']', source)
    assert match, f"Could not find oras_version = 'X.Y.Z' in source:\n{source}"
    return match.group(1)


def _extract_checksum_from_script(script: str) -> str:
    """Extract the 64-char hex SHA-256 checksum from a shell script."""
    match = re.search(r'([0-9a-f]{64})', script)
    assert match, f"Could not find a 64-char hex checksum in script:\n{script}"
    return match.group(1)


def _extract_checksum_from_python(source: str) -> str:
    """Extract ORAS_SHA256_CHECKSUM from Python source."""
    match = re.search(r'ORAS_SHA256_CHECKSUM\s*=\s*["\']([0-9a-f]{64})["\']', source)
    assert match, f"Could not find ORAS_SHA256_CHECKSUM in source:\n{source}"
    return match.group(1)


# ---------------------------------------------------------------------------
# Tests: version consistency
# ---------------------------------------------------------------------------

class TestOrasVersionConsistency:
    """The ORAS version in the workflow must match the version in build-ami.py."""

    def test_workflow_oras_version_is_1_3_0(self, install_oras_run_script):
        """The workflow Install ORAS step must use version 1.3.0."""
        version = _extract_version_from_script(install_oras_run_script)
        assert version == "1.3.0", (
            f"Workflow ORAS version must be 1.3.0, got {version!r}"
        )

    def test_build_ami_oras_version_is_1_3_0(self, build_ami_install_oras_source):
        """build-ami.py install_oras() must use version 1.3.0."""
        version = _extract_version_from_python(build_ami_install_oras_source)
        assert version == "1.3.0", (
            f"build-ami.py ORAS version must be 1.3.0, got {version!r}"
        )

    def test_workflow_version_matches_build_ami_version(
        self, install_oras_run_script, build_ami_install_oras_source
    ):
        """The ORAS version in the workflow must match the version in build-ami.py."""
        workflow_version = _extract_version_from_script(install_oras_run_script)
        build_ami_version = _extract_version_from_python(build_ami_install_oras_source)

        assert workflow_version == build_ami_version, (
            f"ORAS version mismatch: workflow={workflow_version!r}, "
            f"build-ami.py={build_ami_version!r}"
        )


# ---------------------------------------------------------------------------
# Tests: sha256sum verification step presence
# ---------------------------------------------------------------------------

class TestOrasChecksumVerificationPresence:
    """The workflow Install ORAS step must contain a sha256sum verification."""

    def test_sha256sum_command_present(self, install_oras_run_script):
        """The Install ORAS step must call sha256sum."""
        assert "sha256sum" in install_oras_run_script, (
            "The 'Install ORAS' step must contain a sha256sum command"
        )

    def test_sha256sum_uses_check_flag(self, install_oras_run_script):
        """The sha256sum command must use -c or --check to verify the archive."""
        assert "-c" in install_oras_run_script or "--check" in install_oras_run_script, (
            "sha256sum must use -c / --check to verify the downloaded archive"
        )

    def test_checksum_value_is_present(self, install_oras_run_script):
        """A 64-character hex SHA-256 checksum must be embedded in the step."""
        match = re.search(r'[0-9a-f]{64}', install_oras_run_script)
        assert match, (
            "A 64-character hex SHA-256 checksum must be present in the Install ORAS step"
        )

    def test_checksum_is_lowercase_hex(self, install_oras_run_script):
        """The embedded checksum must be lowercase hexadecimal."""
        match = re.search(r'[0-9a-f]{64}', install_oras_run_script)
        assert match, "No 64-char hex checksum found"
        checksum = match.group(0)
        assert re.fullmatch(r'[0-9a-f]{64}', checksum), (
            f"Checksum {checksum!r} must be lowercase hex"
        )


# ---------------------------------------------------------------------------
# Tests: checksum value consistency
# ---------------------------------------------------------------------------

class TestOrasChecksumValueConsistency:
    """The checksum in the workflow must match the one in build-ami.py."""

    def test_workflow_checksum_matches_build_ami_checksum(
        self, install_oras_run_script, build_ami_install_oras_source
    ):
        """The SHA-256 checksum in the workflow must match ORAS_SHA256_CHECKSUM in build-ami.py."""
        workflow_checksum = _extract_checksum_from_script(install_oras_run_script)
        build_ami_checksum = _extract_checksum_from_python(build_ami_install_oras_source)

        assert workflow_checksum == build_ami_checksum, (
            f"ORAS checksum mismatch:\n"
            f"  workflow:    {workflow_checksum!r}\n"
            f"  build-ami.py: {build_ami_checksum!r}\n"
            "Both must reference the same checksum for oras_1.3.0_linux_amd64.tar.gz"
        )

    def test_expected_checksum_value(self, install_oras_run_script):
        """The embedded checksum must be the known-good value for ORAS 1.3.0 linux_amd64."""
        # Source: https://github.com/oras-project/oras/releases/download/v1.3.0/oras_1.3.0_checksums.txt
        expected = "6cdc692f929100feb08aa8de584d02f7bcc30ec7d88bc2adc2054d782db57c64"
        workflow_checksum = _extract_checksum_from_script(install_oras_run_script)
        assert workflow_checksum == expected, (
            f"Workflow checksum {workflow_checksum!r} does not match the "
            f"known-good checksum {expected!r} for oras_1.3.0_linux_amd64.tar.gz"
        )


# ---------------------------------------------------------------------------
# Tests: ordering — checksum before extraction
# ---------------------------------------------------------------------------

class TestOrasChecksumOrdering:
    """Checksum verification must precede tar extraction in the Install ORAS step."""

    def test_sha256sum_before_tar(self, install_oras_run_script):
        """sha256sum must appear before tar in the Install ORAS step."""
        sha256sum_pos = install_oras_run_script.find("sha256sum")
        tar_pos = install_oras_run_script.find("tar ")

        assert sha256sum_pos != -1, "sha256sum must be present"
        assert tar_pos != -1, "tar extraction must be present"
        assert sha256sum_pos < tar_pos, (
            "sha256sum verification must appear before tar extraction"
        )

    def test_sha256sum_before_sudo_mv(self, install_oras_run_script):
        """sha256sum must appear before the binary is moved to /usr/local/bin."""
        sha256sum_pos = install_oras_run_script.find("sha256sum")
        mv_pos = install_oras_run_script.find("sudo mv")

        assert sha256sum_pos != -1, "sha256sum must be present"
        assert mv_pos != -1, "sudo mv must be present"
        assert sha256sum_pos < mv_pos, (
            "sha256sum verification must appear before moving the binary"
        )


# ---------------------------------------------------------------------------
# Tests: failure path
# ---------------------------------------------------------------------------

class TestOrasChecksumFailurePath:
    """The Install ORAS step must exit non-zero when checksum verification fails."""

    def test_step_exits_on_checksum_failure(self, install_oras_run_script):
        """The step must contain an explicit exit 1 or set -e to fail on mismatch."""
        has_exit_on_failure = (
            "exit 1" in install_oras_run_script
            or "set -e" in install_oras_run_script
            or "set -euo" in install_oras_run_script
            or "set -eu" in install_oras_run_script
        )
        assert has_exit_on_failure, (
            "The 'Install ORAS' step must exit non-zero when checksum verification fails. "
            "Use 'exit 1', 'set -e', or similar."
        )

    def test_error_message_on_checksum_failure(self, install_oras_run_script):
        """The step should emit an error message when checksum verification fails."""
        # The step should have either ::error:: annotation or an echo error message
        has_error_output = (
            "::error::" in install_oras_run_script
            or "echo" in install_oras_run_script
        )
        assert has_error_output, (
            "The 'Install ORAS' step should emit an error message when checksum fails"
        )


# ---------------------------------------------------------------------------
# Tests: workflow structure
# ---------------------------------------------------------------------------

class TestWorkflowStructure:
    """General structural checks for the Install ORAS step."""

    def test_install_oras_step_exists(self, workflow_yaml):
        """The workflow must contain an 'Install ORAS' step."""
        found = False
        for job in workflow_yaml.get("jobs", {}).values():
            for step in job.get("steps", []):
                if step.get("name", "").strip().lower() == "install oras":
                    found = True
                    break
        assert found, "The workflow must contain an 'Install ORAS' step"

    def test_install_oras_step_has_run_key(self, install_oras_step):
        """The 'Install ORAS' step must use a 'run' key (inline shell script)."""
        assert "run" in install_oras_step, (
            "The 'Install ORAS' step must use a 'run' key"
        )

    def test_install_oras_step_verifies_binary(self, install_oras_run_script):
        """The 'Install ORAS' step must verify the installed binary with 'oras version'."""
        assert "oras version" in install_oras_run_script, (
            "The 'Install ORAS' step must verify the installation with 'oras version'"
        )

    def test_install_oras_step_downloads_from_github(self, install_oras_run_script):
        """The 'Install ORAS' step must download ORAS from the official GitHub releases."""
        assert "github.com/oras-project/oras/releases/download" in install_oras_run_script, (
            "ORAS must be downloaded from the official GitHub releases page"
        )

    def test_install_oras_step_cleans_up_archive(self, install_oras_run_script):
        """The 'Install ORAS' step must remove the downloaded archive after installation."""
        assert "rm" in install_oras_run_script, (
            "The 'Install ORAS' step must clean up the downloaded archive"
        )
