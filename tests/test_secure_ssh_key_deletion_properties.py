"""
Property-based tests for secure SSH key deletion.

These tests validate that the AMI_Converter overwrites the temporary SSH key
file with random bytes before unlinking, preventing key recovery from disk.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, call

import pytest
from hypothesis import given, strategies as st, settings

# Import build_ami module using importlib
import importlib.util
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)


# --- Strategies ---

def ssh_key_content_strategy():
    """Generate random SSH key file content (realistic key sizes)."""
    return st.binary(min_size=200, max_size=4096)


def region_strategy():
    """Generate valid AWS region strings."""
    return st.sampled_from([
        "us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1",
    ])


def instance_type_strategy():
    """Generate valid EC2 instance type strings."""
    return st.sampled_from(["c5.9xlarge", "m5.xlarge", "t3.large"])


# --- Property 160: Secure SSH Key Deletion ---


@settings(max_examples=100)
@given(
    ssh_key_content=ssh_key_content_strategy(),
    region=region_strategy(),
    instance_type=instance_type_strategy(),
)
def test_secure_ssh_key_deletion(ssh_key_content, region, instance_type):
    """
    Property 160: Secure SSH Key Deletion

    For any AMI build cleanup, the AMI_Converter should overwrite the
    temporary SSH key file with random bytes before unlinking.

    **Validates: Requirements 21.15**
    """
    # Create a real temporary file with the generated SSH key content
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(ssh_key_content)
        ssh_key_path = tmp.name

    try:
        file_size = len(ssh_key_content)
        random_bytes = os.urandom(file_size)

        # Track call order to verify overwrite happens before unlink
        call_order = []

        original_urandom = os.urandom
        original_unlink = os.unlink
        original_open = open

        def tracking_urandom(size):
            call_order.append(('urandom', size))
            return random_bytes

        def tracking_unlink(path):
            call_order.append(('unlink', path))
            return original_unlink(path)

        with patch.object(build_ami.subprocess, 'run') as mock_run, \
             patch.object(build_ami.os, 'urandom', side_effect=tracking_urandom), \
             patch.object(build_ami.os, 'unlink', side_effect=tracking_unlink):

            # Terraform destroy succeeds
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            build_ami.cleanup_infrastructure(
                region=region,
                instance_type=instance_type,
                allowed_ssh_cidr="10.0.0.0/8",
                ssh_key_path=ssh_key_path,
            )

            # Verify the file no longer exists
            assert not os.path.exists(ssh_key_path), \
                "SSH key file should be deleted after cleanup"

            # Verify urandom was called with the correct file size
            urandom_calls = [c for c in call_order if c[0] == 'urandom']
            assert len(urandom_calls) == 1, \
                f"Expected exactly one urandom call, got {len(urandom_calls)}"
            assert urandom_calls[0][1] == file_size, \
                f"urandom called with {urandom_calls[0][1]}, expected {file_size}"

            # Verify unlink was called with the correct path
            unlink_calls = [c for c in call_order if c[0] == 'unlink']
            assert len(unlink_calls) == 1, \
                f"Expected exactly one unlink call, got {len(unlink_calls)}"
            assert unlink_calls[0][1] == ssh_key_path

            # Verify overwrite (urandom) happened BEFORE unlink
            urandom_idx = next(i for i, c in enumerate(call_order) if c[0] == 'urandom')
            unlink_idx = next(i for i, c in enumerate(call_order) if c[0] == 'unlink')
            assert urandom_idx < unlink_idx, \
                "SSH key must be overwritten with random bytes BEFORE unlinking"

    finally:
        # Safety cleanup in case test fails before unlink
        if os.path.exists(ssh_key_path):
            os.unlink(ssh_key_path)
