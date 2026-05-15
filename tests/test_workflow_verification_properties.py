"""
Property-based tests for artifact provenance workflow verification.

These tests validate correctness property 163 for the workflow identity
verification feature in the build-ami script.
"""

import json
import base64
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given, strategies as st, settings

# Import build_ami module using importlib (filename has a hyphen)
import importlib.util

scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)


# --- Strategies ---

def artifact_ref_strategy():
    """Generate valid GHCR artifact references with digest pins."""
    return st.builds(
        lambda owner, repo, tag, digest: f"ghcr.io/{owner}/{repo}:{tag}@sha256:{digest}",
        owner=st.from_regex(r"[a-z][a-z0-9\-]{0,9}", fullmatch=True),
        repo=st.from_regex(r"[a-z][a-z0-9\-]{0,9}", fullmatch=True),
        tag=st.from_regex(r"[a-z0-9][a-z0-9.\-]{0,9}", fullmatch=True),
        digest=st.from_regex(r"[0-9a-f]{64}", fullmatch=True),
    )


def workflow_path_strategy():
    """Generate plausible workflow file paths."""
    return st.builds(
        lambda name: f".github/workflows/{name}.yml",
        name=st.from_regex(r"[a-z][a-z0-9\-]{2,20}", fullmatch=True),
    )


# --- Helpers ---

def make_attestation_bundle_payload(workflow_entry_point: str) -> str:
    """Build a minimal in-toto statement JSON with the given workflow entryPoint."""
    statement = {
        "predicate": {
            "invocation": {
                "configSource": {
                    "entryPoint": workflow_entry_point,
                }
            }
        }
    }
    return base64.b64encode(json.dumps(statement).encode()).decode()


def build_mock_execute(sig_exit_code, wf_stdout, wf_exit_code=0):
    """
    Return a mock execute_remote_command function.

    First call (signature verification) returns sig_exit_code.
    Second call (workflow extraction) returns wf_exit_code and wf_stdout.
    """
    call_count = {"n": 0}

    def _execute(client, command, stream_output=True):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Signature verification command
            return (sig_exit_code, "verified", "")
        else:
            # Workflow extraction command
            return (wf_exit_code, wf_stdout, "")

    return _execute


# ---------------------------------------------------------------------------
# Property 163: Artifact Provenance Workflow Verification
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    artifact_ref=artifact_ref_strategy(),
    workflow_path=workflow_path_strategy(),
)
def test_property_163_matching_workflow_succeeds(artifact_ref, workflow_path):
    """
    Property 163: Artifact Provenance Workflow Verification

    When --expected-workflow is provided and the attestation's workflow identity
    matches the expected workflow path, verification should succeed.

    **Validates: Requirements 47.1, 47.2, 47.3, 47.4**
    """
    mock_client = object()
    mock_execute = build_mock_execute(
        sig_exit_code=0,
        wf_stdout=workflow_path,
    )

    with patch.object(build_ami, "execute_remote_command", mock_execute):
        result = build_ami.verify_artifact_signature(
            mock_client, artifact_ref, expected_workflow=workflow_path
        )

    assert result is True, (
        "verify_artifact_signature should return True when the attestation "
        "workflow identity matches the expected workflow path"
    )


@settings(max_examples=100)
@given(
    artifact_ref=artifact_ref_strategy(),
    expected_workflow=workflow_path_strategy(),
    actual_workflow=workflow_path_strategy(),
)
def test_property_163_mismatched_workflow_fails(artifact_ref, expected_workflow, actual_workflow):
    """
    Property 163: Artifact Provenance Workflow Verification

    When --expected-workflow is provided and the attestation's workflow identity
    does NOT match the expected workflow path, verification should fail.

    **Validates: Requirements 47.1, 47.2, 47.3, 47.4**
    """
    # Ensure the workflows are actually different
    from hypothesis import assume
    assume(expected_workflow not in actual_workflow)

    mock_client = object()
    mock_execute = build_mock_execute(
        sig_exit_code=0,
        wf_stdout=actual_workflow,
    )

    with patch.object(build_ami, "execute_remote_command", mock_execute):
        result = build_ami.verify_artifact_signature(
            mock_client, artifact_ref, expected_workflow=expected_workflow
        )

    assert result is False, (
        "verify_artifact_signature should return False when the attestation "
        "workflow identity does not match the expected workflow path"
    )


@settings(max_examples=100)
@given(
    artifact_ref=artifact_ref_strategy(),
)
def test_property_163_no_expected_workflow_skips_check(artifact_ref):
    """
    Property 163: Artifact Provenance Workflow Verification

    When --expected-workflow is NOT provided, workflow identity verification
    should be skipped and signature verification alone determines the result.

    **Validates: Requirements 47.1, 47.2, 47.3, 47.4**
    """
    mock_client = object()
    call_count = {"n": 0}

    def _execute(client, command, stream_output=True):
        call_count["n"] += 1
        # Only the signature verification command should be called
        return (0, "verified", "")

    with patch.object(build_ami, "execute_remote_command", _execute):
        result = build_ami.verify_artifact_signature(
            mock_client, artifact_ref, expected_workflow=None
        )

    assert result is True, (
        "verify_artifact_signature should return True when signature verification "
        "succeeds and no expected_workflow is provided"
    )
    assert call_count["n"] == 1, (
        "Only the signature verification command should be called when "
        "--expected-workflow is not provided (workflow extraction should be skipped)"
    )


@settings(max_examples=100)
@given(
    artifact_ref=artifact_ref_strategy(),
    workflow_path=workflow_path_strategy(),
)
def test_property_163_workflow_as_suffix_matches(artifact_ref, workflow_path):
    """
    Property 163: Artifact Provenance Workflow Verification

    When the attestation's workflow identity contains the expected workflow path
    as a substring (e.g., full URI containing the path), verification should succeed.

    **Validates: Requirements 47.1, 47.2, 47.3, 47.4**
    """
    # Simulate a full workflow URI that contains the expected path as a suffix
    full_workflow_uri = f"https://github.com/owner/repo/{workflow_path}"

    mock_client = object()
    mock_execute = build_mock_execute(
        sig_exit_code=0,
        wf_stdout=full_workflow_uri,
    )

    with patch.object(build_ami, "execute_remote_command", mock_execute):
        result = build_ami.verify_artifact_signature(
            mock_client, artifact_ref, expected_workflow=workflow_path
        )

    assert result is True, (
        "verify_artifact_signature should return True when the expected workflow "
        "path appears as a substring in the attestation's workflow identity"
    )
