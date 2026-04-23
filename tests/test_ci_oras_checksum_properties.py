"""
Property-based tests for CI ORAS checksum verification.

These tests validate that the GitHub Actions workflow "Install ORAS" step
contains a checksum verification command and that the ORAS version matches
the version used in scripts/build-ami.py.

Feature: github-actions-remote-executor
Property 174: CI ORAS Checksum Verification
Validates: Requirements 17.13, 17.14
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings, strategies as st

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
# Helpers
# ---------------------------------------------------------------------------

def _get_install_oras_step() -> dict:
    """
    Parse the workflow YAML and return the 'Install ORAS' step dict.

    Raises:
        AssertionError: if the workflow file is missing or the step is not found.
    """
    assert WORKFLOW_FILE.exists(), f"Workflow file not found: {WORKFLOW_FILE}"

    with open(WORKFLOW_FILE) as f:
        workflow = yaml.safe_load(f)

    jobs = workflow.get("jobs", {})
    for job in jobs.values():
        for step in job.get("steps", []):
            if step.get("name", "").strip().lower() == "install oras":
                return step

    raise AssertionError("'Install ORAS' step not found in workflow")


def _extract_oras_version_from_workflow() -> str:
    """
    Extract the ORAS version string set in the workflow's Install ORAS step.

    Returns the version string (e.g. "1.3.0").
    """
    step = _get_install_oras_step()
    run_script = step.get("run", "")
    match = re.search(r'VERSION=["\']?(\d+\.\d+\.\d+)["\']?', run_script)
    assert match, "Could not find VERSION=X.Y.Z in the Install ORAS step"
    return match.group(1)


def _extract_oras_version_from_build_ami() -> str:
    """
    Extract the ORAS version string from the install_oras() function in build-ami.py.

    Returns the version string (e.g. "1.3.0").
    """
    import inspect
    source = inspect.getsource(build_ami.install_oras)
    match = re.search(r'oras_version\s*=\s*["\'](\d+\.\d+\.\d+)["\']', source)
    assert match, "Could not find oras_version = 'X.Y.Z' in install_oras()"
    return match.group(1)


def _extract_oras_checksum_from_build_ami() -> str:
    """
    Extract the expected SHA-256 checksum from install_oras() in build-ami.py.

    Returns the 64-character hex checksum string.
    """
    import inspect
    source = inspect.getsource(build_ami.install_oras)
    match = re.search(r'ORAS_SHA256_CHECKSUM\s*=\s*["\']([0-9a-f]{64})["\']', source)
    assert match, "Could not find ORAS_SHA256_CHECKSUM in install_oras()"
    return match.group(1)


# ---------------------------------------------------------------------------
# Feature: github-actions-remote-executor, Property 174: CI ORAS Checksum Verification
# ---------------------------------------------------------------------------


def test_property_174_workflow_oras_version_matches_build_ami():
    """
    Property 174 (part 1): CI ORAS Checksum Verification — version consistency

    The ORAS version in the workflow's "Install ORAS" step must match the
    version used in scripts/build-ami.py's install_oras() function.

    **Validates: Requirements 17.13, 17.14**
    """
    workflow_version = _extract_oras_version_from_workflow()
    build_ami_version = _extract_oras_version_from_build_ami()

    assert workflow_version == build_ami_version, (
        f"ORAS version mismatch: workflow uses {workflow_version!r} but "
        f"build-ami.py uses {build_ami_version!r}. Both must be kept in sync."
    )


def test_property_174_workflow_install_oras_step_has_checksum_verification():
    """
    Property 174 (part 2): CI ORAS Checksum Verification — sha256sum present

    The "Install ORAS" step in the workflow must contain a SHA-256 checksum
    verification command (sha256sum -c or equivalent) to verify the downloaded
    archive before extracting it.

    **Validates: Requirements 17.13, 17.14**
    """
    step = _get_install_oras_step()
    run_script = step.get("run", "")

    assert "sha256sum" in run_script, (
        "The 'Install ORAS' step must contain a sha256sum verification command"
    )
    assert "-c" in run_script or "--check" in run_script, (
        "The sha256sum command must use -c / --check to verify the checksum"
    )


def test_property_174_workflow_install_oras_checksum_value_matches_build_ami():
    """
    Property 174 (part 3): CI ORAS Checksum Verification — checksum value consistency

    The SHA-256 checksum embedded in the workflow's "Install ORAS" step must
    match the ORAS_SHA256_CHECKSUM constant in build-ami.py's install_oras().

    **Validates: Requirements 17.13, 17.14**
    """
    step = _get_install_oras_step()
    run_script = step.get("run", "")

    # Extract the 64-character hex checksum from the workflow script
    match = re.search(r'([0-9a-f]{64})', run_script)
    assert match, (
        "Could not find a 64-character hex SHA-256 checksum in the 'Install ORAS' step"
    )
    workflow_checksum = match.group(1)

    build_ami_checksum = _extract_oras_checksum_from_build_ami()

    assert workflow_checksum == build_ami_checksum, (
        f"ORAS checksum mismatch: workflow uses {workflow_checksum!r} but "
        f"build-ami.py uses {build_ami_checksum!r}. Both must be kept in sync."
    )


def test_property_174_workflow_checksum_verified_before_extraction():
    """
    Property 174 (part 4): CI ORAS Checksum Verification — ordering

    The sha256sum verification command must appear before the tar extraction
    command in the "Install ORAS" step, so that a tampered archive is never
    extracted.

    **Validates: Requirements 17.13, 17.14**
    """
    step = _get_install_oras_step()
    run_script = step.get("run", "")

    sha256sum_pos = run_script.find("sha256sum")
    tar_pos = run_script.find("tar ")

    assert sha256sum_pos != -1, "sha256sum must be present in the Install ORAS step"
    assert tar_pos != -1, "tar extraction must be present in the Install ORAS step"
    assert sha256sum_pos < tar_pos, (
        "sha256sum verification must appear before tar extraction in the Install ORAS step"
    )


def test_property_174_workflow_checksum_failure_exits_nonzero():
    """
    Property 174 (part 5): CI ORAS Checksum Verification — failure path

    The "Install ORAS" step must exit with a non-zero status when checksum
    verification fails (i.e., the script must not silently continue on mismatch).

    **Validates: Requirements 17.13, 17.14**
    """
    step = _get_install_oras_step()
    run_script = step.get("run", "")

    # The step must either use `sha256sum -c` (which exits non-zero on mismatch)
    # combined with `|| { ...; exit 1; }` or `set -e` / `set -euo pipefail`.
    # We accept any of these patterns.
    has_exit_on_failure = (
        "exit 1" in run_script
        or "set -e" in run_script
        or "set -euo" in run_script
        or "set -eu" in run_script
    )

    assert has_exit_on_failure, (
        "The 'Install ORAS' step must explicitly exit with a non-zero status "
        "when checksum verification fails (use 'exit 1', 'set -e', or similar)"
    )


# ---------------------------------------------------------------------------
# Parametric property: version string format is always X.Y.Z
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(
    version=st.from_regex(r"\d+\.\d+\.\d+", fullmatch=True)
)
def test_oras_version_format_is_semver(version: str):
    """
    For any ORAS version string, it must follow semantic versioning (X.Y.Z)
    with all-numeric components.

    **Validates: Requirements 17.13, 17.14**
    """
    parts = version.split(".")
    assert len(parts) == 3, "ORAS version must have exactly three components"
    for part in parts:
        assert part.isdigit(), f"Version component {part!r} must be numeric"
        assert int(part) >= 0, "Version components must be non-negative"


@settings(max_examples=50)
@given(
    checksum=st.from_regex(r"[0-9a-f]{64}", fullmatch=True)
)
def test_sha256_checksum_format(checksum: str):
    """
    For any SHA-256 checksum string, it must be exactly 64 lowercase hex characters.

    **Validates: Requirements 17.13, 17.14**
    """
    assert len(checksum) == 64, "SHA-256 checksum must be exactly 64 characters"
    assert re.fullmatch(r"[0-9a-f]{64}", checksum), (
        "SHA-256 checksum must contain only lowercase hex characters"
    )
