"""
Property-based tests for snapshot upload and AMI registration.

These tests validate the correctness properties for coldsnap snapshot upload,
AMI registration configuration, and coldsnap output streaming.
"""

import sys
from pathlib import Path
from unittest.mock import Mock, patch, call

import pytest
from hypothesis import given, strategies as st, settings, assume

# Import build_ami module using importlib
import importlib.util
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)


# --- Strategies ---

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


def raw_filename_strategy():
    """Generate valid .raw filenames."""
    return st.from_regex(r"[a-z][a-z0-9\-]{0,19}\.raw", fullmatch=True)


def architecture_strategy():
    """Generate valid architecture strings."""
    return st.sampled_from(["x86_64", "arm64"])


def ami_name_strategy():
    """Generate valid AMI name strings."""
    return st.builds(
        lambda arch, ts: f"attestable-ami-imported-{arch}-{ts}",
        arch=architecture_strategy(),
        ts=st.from_regex(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}", fullmatch=True),
    )


# --- Property 73: Snapshot Upload Success ---
# For any successful coldsnap upload, the output should contain a valid
# snapshot ID starting with "snap-".
# **Validates: Requirements 19.4**


@settings(max_examples=100)
@given(
    raw_filename=raw_filename_strategy(),
    snapshot_id=snapshot_id_strategy(),
)
def test_snapshot_upload_returns_valid_snapshot_id(raw_filename, snapshot_id):
    """
    Property 73: Snapshot Upload Success

    For any successful coldsnap upload producing a snapshot ID, upload_snapshot
    should return that snapshot ID starting with 'snap-'.

    **Validates: Requirements 19.4**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        def side_effect(ssh_client, command, stream_output=True):
            if "ls *.raw" in command:
                return (0, raw_filename, "")
            if "coldsnap upload" in command:
                return (0, f"Uploading snapshot...\n{snapshot_id}", "")
            return (0, "", "")

        mock_execute.side_effect = side_effect

        result = build_ami.upload_snapshot(mock_ssh_client, "us-east-1")

        assert result.startswith("snap-"), \
            f"Snapshot ID should start with 'snap-', got: {result}"
        assert result == snapshot_id, \
            f"Should return the parsed snapshot ID: expected {snapshot_id}, got {result}"


@settings(max_examples=100)
@given(
    raw_filename=raw_filename_strategy(),
    snapshot_id=snapshot_id_strategy(),
)
def test_snapshot_upload_parses_id_from_multiline_output(raw_filename, snapshot_id):
    """
    Property 73: Snapshot Upload Success

    For any coldsnap output with the snapshot ID on any line, upload_snapshot
    should correctly parse it.

    **Validates: Requirements 19.4**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        def side_effect(ssh_client, command, stream_output=True):
            if "ls *.raw" in command:
                return (0, raw_filename, "")
            if "coldsnap upload" in command:
                # Snapshot ID embedded in multi-line output
                output = f"Starting upload...\nProgress: 50%\nProgress: 100%\n{snapshot_id}\nDone."
                return (0, output, "")
            return (0, "", "")

        mock_execute.side_effect = side_effect

        result = build_ami.upload_snapshot(mock_ssh_client, "us-east-1")
        assert result == snapshot_id


@settings(max_examples=100)
@given(raw_filename=raw_filename_strategy())
def test_snapshot_upload_fails_when_no_snapshot_id(raw_filename):
    """
    Property 73: Snapshot Upload Success

    When coldsnap output contains no snapshot ID, upload_snapshot should
    raise RuntimeError.

    **Validates: Requirements 19.4**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        def side_effect(ssh_client, command, stream_output=True):
            if "ls *.raw" in command:
                return (0, raw_filename, "")
            if "coldsnap upload" in command:
                return (0, "Upload complete, no ID here", "")
            return (0, "", "")

        mock_execute.side_effect = side_effect

        with pytest.raises(RuntimeError):
            build_ami.upload_snapshot(mock_ssh_client, "us-east-1")


@settings(max_examples=100)
@given(raw_filename=raw_filename_strategy())
def test_snapshot_upload_fails_on_coldsnap_error(raw_filename):
    """
    Property 73: Snapshot Upload Success

    When coldsnap returns a non-zero exit code, upload_snapshot should
    raise RuntimeError.

    **Validates: Requirements 19.4**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        def side_effect(ssh_client, command, stream_output=True):
            if "ls *.raw" in command:
                return (0, raw_filename, "")
            if "coldsnap upload" in command:
                return (1, "", "Upload failed: permission denied")
            return (0, "", "")

        mock_execute.side_effect = side_effect

        with pytest.raises(RuntimeError, match="coldsnap upload failed"):
            build_ami.upload_snapshot(mock_ssh_client, "us-east-1")


# --- Property 74: AMI Registration Configuration ---
# For any registered AMI, it should have TPM 2.0 support, UEFI boot mode,
# and ENA support enabled.
# **Validates: Requirements 19.7, 19.8, 19.9, 19.10, 19.11**


@settings(max_examples=100)
@given(
    snapshot_id=snapshot_id_strategy(),
    architecture=architecture_strategy(),
    ami_name=ami_name_strategy(),
)
def test_ami_registration_has_correct_configuration(snapshot_id, architecture, ami_name):
    """
    Property 74: AMI Registration Configuration

    For any registered AMI, the register_image call should include TPM 2.0,
    UEFI boot mode, hvm virtualization, and ENA support.

    **Validates: Requirements 19.7, 19.8, 19.9, 19.10, 19.11**
    """
    mock_ec2_client = Mock()
    mock_ec2_client.register_image.return_value = {"ImageId": "ami-0123456789abcdef0"}

    build_ami.register_ami(mock_ec2_client, snapshot_id, architecture, ami_name)

    mock_ec2_client.register_image.assert_called_once()
    call_kwargs = mock_ec2_client.register_image.call_args[1]

    # Verify TPM 2.0 support
    assert call_kwargs["TpmSupport"] == "v2.0", \
        "AMI should have TPM 2.0 support"

    # Verify UEFI boot mode
    assert call_kwargs["BootMode"] == "uefi", \
        "AMI should have UEFI boot mode"

    # Verify ENA support
    assert call_kwargs["EnaSupport"] is True, \
        "AMI should have ENA support enabled"

    # Verify HVM virtualization
    assert call_kwargs["VirtualizationType"] == "hvm", \
        "AMI should use HVM virtualization"

    # Verify architecture
    assert call_kwargs["Architecture"] == architecture, \
        f"AMI architecture should be {architecture}"

    # Verify root device name
    assert call_kwargs["RootDeviceName"] == "/dev/xvda", \
        "Root device should be /dev/xvda"

    # Verify block device mappings include the snapshot
    block_mappings = call_kwargs["BlockDeviceMappings"]
    assert len(block_mappings) == 1
    assert block_mappings[0]["DeviceName"] == "/dev/xvda"
    assert block_mappings[0]["Ebs"]["SnapshotId"] == snapshot_id


@settings(max_examples=100)
@given(
    snapshot_id=snapshot_id_strategy(),
    architecture=architecture_strategy(),
    ami_name=ami_name_strategy(),
)
def test_ami_registration_returns_ami_id(snapshot_id, architecture, ami_name):
    """
    Property 74: AMI Registration Configuration

    For any successful AMI registration, register_ami should return the AMI ID
    from the response.

    **Validates: Requirements 19.7, 19.8**
    """
    expected_ami_id = "ami-0123456789abcdef0"
    mock_ec2_client = Mock()
    mock_ec2_client.register_image.return_value = {"ImageId": expected_ami_id}

    result = build_ami.register_ami(mock_ec2_client, snapshot_id, architecture, ami_name)

    assert result == expected_ami_id, \
        f"Should return AMI ID from response, got: {result}"


@settings(max_examples=100)
@given(
    snapshot_id=snapshot_id_strategy(),
    architecture=architecture_strategy(),
    ami_name=ami_name_strategy(),
)
def test_ami_registration_raises_on_client_error(snapshot_id, architecture, ami_name):
    """
    Property 74: AMI Registration Configuration

    When AWS register_image fails with ClientError, register_ami should
    propagate the error.

    **Validates: Requirements 19.7**
    """
    from botocore.exceptions import ClientError

    mock_ec2_client = Mock()
    mock_ec2_client.register_image.side_effect = ClientError(
        {"Error": {"Code": "InvalidParameterValue", "Message": "Invalid snapshot"}},
        "RegisterImage"
    )

    with pytest.raises(ClientError):
        build_ami.register_ami(mock_ec2_client, snapshot_id, architecture, ami_name)


# --- Property 80: Coldsnap Output Streaming ---
# For any snapshot upload operation, coldsnap output should be streamed
# to logs in real-time.
# **Validates: Requirements 19.3**


@settings(max_examples=100)
@given(
    raw_filename=raw_filename_strategy(),
    snapshot_id=snapshot_id_strategy(),
)
def test_coldsnap_output_streamed_to_logs(raw_filename, snapshot_id):
    """
    Property 80: Coldsnap Output Streaming

    For any snapshot upload, the coldsnap command should be executed with
    stream_output=True so output is streamed to logs in real-time.

    **Validates: Requirements 19.3**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        def side_effect(ssh_client, command, stream_output=True):
            if "ls *.raw" in command:
                return (0, raw_filename, "")
            if "coldsnap upload" in command:
                # Verify stream_output is True for coldsnap command
                assert stream_output is True, \
                    "coldsnap upload should stream output (stream_output=True)"
                return (0, snapshot_id, "")
            return (0, "", "")

        mock_execute.side_effect = side_effect

        build_ami.upload_snapshot(mock_ssh_client, "us-east-1")

        # Verify coldsnap command was called
        coldsnap_calls = [
            c for c in mock_execute.call_args_list
            if "coldsnap upload" in str(c)
        ]
        assert len(coldsnap_calls) == 1, \
            "Should call coldsnap upload exactly once"


@settings(max_examples=100)
@given(
    raw_filename=raw_filename_strategy(),
    snapshot_id=snapshot_id_strategy(),
)
def test_coldsnap_uses_full_binary_path(raw_filename, snapshot_id):
    """
    Property 80: Coldsnap Output Streaming

    For any snapshot upload, the coldsnap command should use the full binary
    path /home/ec2-user/.cargo/bin/coldsnap.

    **Validates: Requirements 19.3**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        def side_effect(ssh_client, command, stream_output=True):
            if "ls *.raw" in command:
                return (0, raw_filename, "")
            if "coldsnap" in command:
                assert "/home/ec2-user/.cargo/bin/coldsnap" in command, \
                    "Should use full path to coldsnap binary"
                return (0, snapshot_id, "")
            return (0, "", "")

        mock_execute.side_effect = side_effect

        build_ami.upload_snapshot(mock_ssh_client, "us-east-1")
