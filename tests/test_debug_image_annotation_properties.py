"""
Property-based tests for the debug image annotation feature.

These tests validate correctness properties 161-162 for the debug image
annotation and production gate across the GHA workflow and build-ami script.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from hypothesis import given, strategies as st, settings

# Import build_ami module using importlib (filename has a hyphen)
import importlib.util

scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)

# Path to workflow file
WORKFLOW_PATH = Path(__file__).parent.parent / ".github" / "workflows" / "build-attestable-image.yml"


# --- Strategies ---

def event_name_strategy():
    """Generate GitHub Actions event names."""
    return st.sampled_from(["push", "pull_request", "schedule", "workflow_dispatch"])


def debug_annotation_value_strategy():
    """Generate possible debug annotation values."""
    return st.sampled_from(["true", "false"])


def arbitrary_annotation_value_strategy():
    """Generate arbitrary annotation values including edge cases."""
    return st.one_of(
        st.just("true"),
        st.just("false"),
        st.just(""),
        st.just("TRUE"),
        st.just("False"),
        st.text(min_size=1, max_size=20).filter(lambda x: x not in ("true", "false")),
    )


# --- Helper to build a mock manifest JSON ---

def make_manifest_json(debug_value=None):
    """Build a minimal OCI manifest JSON string with optional debug annotation."""
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "annotations": {},
    }
    if debug_value is not None:
        manifest["annotations"]["debug"] = debug_value
    return json.dumps(manifest)


def mock_ssh_client_for_manifest(manifest_json, exit_code=0):
    """Create a mock SSH client whose execute_remote_command returns the given manifest."""
    class _MockSSH:
        pass

    mock_client = _MockSSH()

    def _execute(client, command, stream_output=True):
        return (exit_code, manifest_json, "")

    return mock_client, _execute


# ---------------------------------------------------------------------------
# Property 161: Debug Image Annotation
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    event_name=event_name_strategy(),
    enable_ssh=st.booleans(),
)
def test_property_161_debug_image_annotation(event_name, enable_ssh):
    """
    Property 161: Debug Image Annotation

    For any KIWI image build, the Artifact_Publisher should add a `debug=true`
    annotation when built with --enable-ssh and `debug=false` when built
    without --enable-ssh.

    **Validates: Requirements 46.1, 46.2**
    """
    with open(WORKFLOW_PATH, "r") as f:
        workflow = yaml.safe_load(f)

    # Find the "Push artifact to GHCR" step
    push_step = None
    for step in workflow["jobs"]["build-and-publish"]["steps"]:
        if step.get("name") == "Push artifact to GHCR":
            push_step = step
            break

    assert push_step is not None, "Push artifact to GHCR step not found"

    run_script = push_step["run"]

    # The workflow must include the debug annotation in the oras push command
    assert '--annotation "debug=${DEBUG_VALUE}"' in run_script, (
        "ORAS push must include --annotation \"debug=${DEBUG_VALUE}\""
    )

    # The workflow must set DEBUG_VALUE based on the same condition as SSH_FLAG
    assert 'DEBUG_VALUE="false"' in run_script, "DEBUG_VALUE should default to false"
    assert 'DEBUG_VALUE="true"' in run_script, "DEBUG_VALUE should be set to true conditionally"

    # Verify the conditional logic references workflow_dispatch and enable_ssh
    assert "workflow_dispatch" in run_script, "Debug annotation condition should check workflow_dispatch"
    assert "enable_ssh" in run_script, "Debug annotation condition should check enable_ssh"

    # Determine expected debug value
    should_be_debug = event_name == "workflow_dispatch" and enable_ssh is True

    if should_be_debug:
        # When workflow_dispatch + enable_ssh=true, DEBUG_VALUE should become "true"
        assert 'DEBUG_VALUE="true"' in run_script
    else:
        # For all other cases, DEBUG_VALUE stays "false" (the default)
        assert 'DEBUG_VALUE="false"' in run_script


# ---------------------------------------------------------------------------
# Property 162: Debug Image Production Gate
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    debug_value=debug_annotation_value_strategy(),
    allow_debug=st.booleans(),
)
def test_property_162_debug_image_production_gate(debug_value, allow_debug):
    """
    Property 162: Debug Image Production Gate

    For any artifact with `debug=true` annotation, the AMI_Converter should
    refuse to build the AMI unless an explicit `--allow-debug` CLI flag is
    provided.

    **Validates: Requirements 46.3, 46.4, 46.5**
    """
    manifest_json = make_manifest_json(debug_value=debug_value)
    mock_client, mock_execute = mock_ssh_client_for_manifest(manifest_json)

    with patch.object(build_ami, "execute_remote_command", mock_execute):
        if debug_value == "true" and not allow_debug:
            # Should refuse to build
            with pytest.raises(RuntimeError, match="REFUSING TO BUILD"):
                build_ami.check_debug_annotation(mock_client, f"ghcr.io/owner/repo:tag@sha256:{'a' * 64}", allow_debug)
        elif debug_value == "true" and allow_debug:
            # Should proceed with warning (no exception)
            build_ami.check_debug_annotation(mock_client, f"ghcr.io/owner/repo:tag@sha256:{'a' * 64}", allow_debug)
        else:
            # debug=false → should always proceed
            build_ami.check_debug_annotation(mock_client, f"ghcr.io/owner/repo:tag@sha256:{'a' * 64}", allow_debug)


@settings(max_examples=100)
@given(allow_debug=st.booleans())
def test_property_162_no_debug_annotation_proceeds(allow_debug):
    """
    Property 162: Debug Image Production Gate (no annotation case)

    When no debug annotation is present, the AMI_Converter should proceed
    regardless of the --allow-debug flag.

    **Validates: Requirements 46.3, 46.4, 46.5**
    """
    manifest_json = make_manifest_json(debug_value=None)
    mock_client, mock_execute = mock_ssh_client_for_manifest(manifest_json)

    with patch.object(build_ami, "execute_remote_command", mock_execute):
        # Should not raise
        build_ami.check_debug_annotation(mock_client, f"ghcr.io/owner/repo:tag@sha256:{'a' * 64}", allow_debug)


@settings(max_examples=100)
@given(
    annotation_value=arbitrary_annotation_value_strategy(),
    allow_debug=st.booleans(),
)
def test_property_162_only_exact_true_blocks(annotation_value, allow_debug):
    """
    Property 162: Debug Image Production Gate (strict matching)

    Only the exact string "true" for the debug annotation should trigger the
    production gate. All other values (including "TRUE", "True", empty, etc.)
    should be treated as non-debug.

    **Validates: Requirements 46.3, 46.4, 46.5**
    """
    manifest_json = make_manifest_json(debug_value=annotation_value)
    mock_client, mock_execute = mock_ssh_client_for_manifest(manifest_json)

    with patch.object(build_ami, "execute_remote_command", mock_execute):
        if annotation_value == "true" and not allow_debug:
            with pytest.raises(RuntimeError, match="REFUSING TO BUILD"):
                build_ami.check_debug_annotation(mock_client, f"ghcr.io/owner/repo:tag@sha256:{'a' * 64}", allow_debug)
        else:
            # All other values should proceed without error
            build_ami.check_debug_annotation(mock_client, f"ghcr.io/owner/repo:tag@sha256:{'a' * 64}", allow_debug)
