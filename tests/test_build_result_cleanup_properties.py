"""
Property-based tests for build result output and infrastructure cleanup.

These tests validate the correctness properties for build result generation,
infrastructure cleanup guarantees, and cleanup on build failure.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, call

import pytest
from hypothesis import given, strategies as st, settings, assume

# Import build_ami module using importlib
import importlib.util
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)


# --- Strategies ---

def ami_id_strategy():
    """Generate valid AMI IDs starting with ami-."""
    return st.builds(
        lambda hex_part: f"ami-{hex_part}",
        hex_part=st.text(
            alphabet="0123456789abcdef",
            min_size=8,
            max_size=17,
        ),
    )


def snapshot_id_strategy():
    """Generate valid snapshot IDs starting with snap-."""
    return st.builds(
        lambda hex_part: f"snap-{hex_part}",
        hex_part=st.text(
            alphabet="0123456789abcdef",
            min_size=8,
            max_size=17,
        ),
    )


def region_strategy():
    """Generate valid AWS region strings."""
    return st.sampled_from([
        "us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1",
        "eu-central-1", "ap-northeast-1",
    ])


def pcr_hex_strategy():
    """Generate valid PCR hex strings (48 bytes = 96 hex chars for SHA-384)."""
    return st.text(
        alphabet="0123456789abcdef",
        min_size=16,
        max_size=96,
    ).filter(lambda x: len(x) > 0)


def pcr_measurements_strategy():
    """Generate valid PCR measurements dictionaries."""
    return st.builds(
        lambda pcr4, pcr7: {"Measurements": {"PCR4": pcr4, "PCR7": pcr7}},
        pcr4=pcr_hex_strategy(),
        pcr7=pcr_hex_strategy(),
    )


# --- Property 75: Build Result Completeness ---


@settings(max_examples=20)
@given(
    ami_id=ami_id_strategy(),
    snapshot_id=snapshot_id_strategy(),
    region=region_strategy(),
    pcr_measurements=pcr_measurements_strategy(),
)
def test_build_result_completeness(ami_id, snapshot_id, region, pcr_measurements):
    """
    Property 75: Build Result Completeness

    For any valid AMI build, the result should contain ami_id, snapshot_id,
    region, build_timestamp, and pcr_measurements with pcr4 and pcr7.

    **Validates: Requirements 20.1, 20.2, 20.3, 20.4, 20.5**
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        output_file = f.name

    try:
        result = build_ami.generate_build_result(
            ami_id=ami_id,
            snapshot_id=snapshot_id,
            region=region,
            pcr_measurements=pcr_measurements,
            output_file=output_file,
        )

        # Verify all required keys are present
        assert "ami_id" in result, "Build result missing ami_id"
        assert "snapshot_id" in result, "Build result missing snapshot_id"
        assert "region" in result, "Build result missing region"
        assert "build_timestamp" in result, "Build result missing build_timestamp"
        assert "pcr_measurements" in result, "Build result missing pcr_measurements"

        # Verify values match inputs
        assert result["ami_id"] == ami_id
        assert result["snapshot_id"] == snapshot_id
        assert result["region"] == region

        # Verify PCR measurements extracted correctly
        assert "pcr4" in result["pcr_measurements"]
        assert "pcr7" in result["pcr_measurements"]
        assert result["pcr_measurements"]["pcr4"] == pcr_measurements["Measurements"]["PCR4"]
        assert result["pcr_measurements"]["pcr7"] == pcr_measurements["Measurements"]["PCR7"]

        # Verify build_timestamp is valid ISO 8601 format
        ts = result["build_timestamp"]
        datetime.fromisoformat(ts)

        # Verify the output file was written with correct JSON
        with open(output_file, 'r') as f:
            file_content = json.load(f)
        assert file_content == result

        # Verify 2-space indentation
        with open(output_file, 'r') as f:
            raw = f.read()
        assert '  "ami_id"' in raw, "JSON not formatted with 2-space indentation"

    finally:
        if os.path.exists(output_file):
            os.unlink(output_file)


# --- Property 76: Script Resource Cleanup Guarantee ---

@settings(max_examples=20)
@given(
    build_succeeds=st.booleans(),
)
def test_infrastructure_cleanup_guarantee(build_succeeds):
    """
    Property 76: Script Resource Cleanup Guarantee

    The script's cleanup closes the SSH connection and securely deletes the temp
    SSH key. Teardown of the shared build instance is now owned by the workflow's
    always() terraform destroy step (D1) — the script runs NO terraform destroy.

    **Validates: Requirements 20.7, 20.8, 20.9, 21.15**
    """
    ssh_key_path = "/tmp/fake-key.pem"

    mock_ssh_client = Mock()

    # The script no longer imports subprocess / owns terraform teardown.
    assert not hasattr(build_ami, 'cleanup_infrastructure'), \
        "Script must not own terraform teardown any more (workflow owns destroy)"

    with patch.object(build_ami.os.path, 'exists', return_value=True), \
         patch.object(build_ami.os.path, 'getsize', return_value=256), \
         patch.object(build_ami.os, 'urandom', return_value=b'\x00' * 256), \
         patch('builtins.open', create=True) as mock_open, \
         patch.object(build_ami.os, 'unlink') as mock_unlink:

        build_ami.cleanup_script_resources(
            ssh_key_path=ssh_key_path,
            ssh_client=mock_ssh_client,
        )

        # SSH client should be closed
        mock_ssh_client.close.assert_called_once()

        # SSH key should be securely deleted (overwrite then unlink)
        mock_unlink.assert_called_once_with(ssh_key_path)


# --- Property 77: Build Failure Cleanup ---

@settings(max_examples=20)
@given(
    error_message=st.text(min_size=1, max_size=50).filter(lambda x: x.strip()),
)
def test_build_failure_cleanup(error_message):
    """
    Property 77: Build Failure Cleanup

    For any build failure, the script's finally cleanup should still execute
    (close SSH + delete key) and main() should return exit code 1.

    **Validates: Requirements 20.12**
    """
    # Mock parse_arguments to return controlled args for the new CLI.
    mock_args = Mock()
    mock_args.host = "1.2.3.4"
    mock_args.ssh_key_path = "/tmp/key.pem"
    mock_args.run_id = "123-1"
    mock_args.flavors_manifest = "flavors-manifest.json"
    mock_args.region = "us-east-1"
    mock_args.output_dir = "."
    mock_args.artifacts_base_path = "~/artifacts"
    mock_args.allow_debug = False
    mock_args.expected_workflow = None
    mock_args.producing_commit = "deadbeef"

    with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
         patch.object(build_ami, 'validate_aws_region'), \
         patch.object(build_ami, 'validate_run_id'), \
         patch.object(build_ami, 'load_flavors_manifest',
                      return_value=[{"flavor": "default", "artifact_ref": "ghcr.io/o/r@sha256:" + "a" * 64}]), \
         patch.object(build_ami.os, 'makedirs'), \
         patch.object(build_ami, 'boto3'), \
         patch.object(build_ami, 'verify_ssh_connectivity', side_effect=RuntimeError(error_message)), \
         patch.object(build_ami, 'cleanup_script_resources') as mock_cleanup:

        exit_code = build_ami.main()

        # Build failure should return exit code 1
        assert exit_code == 1, f"Expected exit code 1 on failure, got {exit_code}"

        # Cleanup should always be called even on failure
        mock_cleanup.assert_called_once()

        # Verify cleanup was called with the temp SSH key path
        call_kwargs = mock_cleanup.call_args[1]
        assert call_kwargs['ssh_key_path'] == "/tmp/key.pem"
