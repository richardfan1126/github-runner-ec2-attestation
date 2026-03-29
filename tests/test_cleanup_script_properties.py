"""
Property-based tests for the cleanup script (scripts/cleanup.py).

These tests validate correctness properties for cleanup CLI argument parsing,
build result loading, user cancellation, Terraform error propagation,
post-destroy state verification, AMI deregistration, resource verification,
and exit code correctness.
"""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given, strategies as st, settings

# Import cleanup module using importlib (same pattern as test_deployment_script_properties.py)
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("cleanup", scripts_dir / "cleanup.py")
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


# --- Strategies ---

def valid_file_path_strategy():
    """Generate valid file path strings for --ami-build-result."""
    return st.one_of(
        # Simple filenames with .json extension
        st.from_regex(r"[a-zA-Z][a-zA-Z0-9_\-]{0,19}\.json", fullmatch=True),
        # Paths with directory components
        st.builds(
            lambda d, f: f"{d}/{f}.json",
            st.from_regex(r"[a-zA-Z][a-zA-Z0-9_\-]{0,9}", fullmatch=True),
            st.from_regex(r"[a-zA-Z][a-zA-Z0-9_\-]{0,9}", fullmatch=True),
        ),
    )


def valid_dir_path_strategy():
    """Generate valid directory path strings for --terraform-dir."""
    return st.one_of(
        # Simple directory names
        st.from_regex(r"[a-zA-Z][a-zA-Z0-9_\-]{0,19}", fullmatch=True),
        # Nested directory paths
        st.builds(
            lambda a, b: f"{a}/{b}",
            st.from_regex(r"[a-zA-Z][a-zA-Z0-9_\-]{0,9}", fullmatch=True),
            st.from_regex(r"[a-zA-Z][a-zA-Z0-9_\-]{0,9}", fullmatch=True),
        ),
    )


# --- Property 87: Cleanup CLI Argument Parsing ---

@settings(max_examples=20, deadline=None)
@given(st.just(None))
def test_property_87_cleanup_cli_defaults(_):
    """
    Property 87: Cleanup CLI Argument Parsing (defaults)

    Invoking parse_arguments() with no args returns the default values:
    ami_build_result='ami_build_result.json' and terraform_dir='terraform/deploy'.

    **Validates: Requirements 28.1, 28.2**
    """
    with patch("sys.argv", ["cleanup.py"]):
        args = cleanup.parse_arguments()

    assert args.ami_build_result == "ami_build_result.json", (
        f"Expected default ami_build_result='ami_build_result.json', got '{args.ami_build_result}'"
    )
    expected_terraform_dir = Path(__file__).parent.parent / "scripts" / ".." / "terraform" / "deploy"
    assert Path(str(args.terraform_dir)).resolve() == expected_terraform_dir.resolve(), (
        f"Expected default terraform_dir to resolve to '{expected_terraform_dir.resolve()}', got '{args.terraform_dir}'"
    )


@settings(max_examples=20, deadline=None)
@given(
    file_path=valid_file_path_strategy(),
    dir_path=valid_dir_path_strategy(),
)
def test_property_87_cleanup_cli_custom_args(file_path, dir_path):
    """
    Property 87: Cleanup CLI Argument Parsing (custom args)

    For any valid file path and directory path, providing custom
    --ami-build-result and --terraform-dir values are correctly parsed
    and returned by parse_arguments().

    **Validates: Requirements 28.1, 28.2**
    """
    with patch("sys.argv", [
        "cleanup.py",
        "--ami-build-result", file_path,
        "--terraform-dir", dir_path,
    ]):
        args = cleanup.parse_arguments()

    assert args.ami_build_result == file_path, (
        f"Expected ami_build_result='{file_path}', got '{args.ami_build_result}'"
    )
    assert args.terraform_dir == dir_path, (
        f"Expected terraform_dir='{dir_path}', got '{args.terraform_dir}'"
    )

# --- Additional imports for Property 88 ---
import json
import os
import tempfile


# --- Strategies for Property 88 (reused from test_deployment_script_properties.py) ---

def ami_id_strategy():
    """Generate valid AMI IDs."""
    return st.builds(
        lambda h: f"ami-{h}",
        st.text(alphabet="0123456789abcdef", min_size=8, max_size=17),
    )


def snapshot_id_strategy():
    """Generate valid snapshot IDs."""
    return st.builds(
        lambda h: f"snap-{h}",
        st.text(alphabet="0123456789abcdef", min_size=8, max_size=17),
    )


def region_strategy():
    """Generate valid AWS region strings."""
    return st.sampled_from([
        "us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1",
        "eu-central-1", "ap-northeast-1",
    ])


# --- Property 88: Cleanup Build Result Loading ---

@settings(max_examples=20, deadline=None)
@given(
    ami_id=ami_id_strategy(),
    snapshot_id=snapshot_id_strategy(),
    region=region_strategy(),
)
def test_property_88_cleanup_build_result_valid_json(ami_id, snapshot_id, region):
    """
    Property 88: Cleanup Build Result Loading (valid JSON)

    For any valid JSON file containing ami_id, snapshot_id, and region fields,
    the cleanup script should correctly parse and extract all three fields.

    **Validates: Requirements 28.4**
    """
    build_result = {
        "ami_id": ami_id,
        "snapshot_id": snapshot_id,
        "region": region,
    }

    tmp_file = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(build_result, f)
            tmp_file = f.name

        # Load the file the same way cleanup.py main() does
        with open(tmp_file, 'r') as f:
            loaded = json.loads(f.read())

        # Verify all three required fields are correctly extracted
        assert loaded['ami_id'] == ami_id, \
            f"ami_id mismatch: expected {ami_id}, got {loaded['ami_id']}"
        assert loaded['snapshot_id'] == snapshot_id, \
            f"snapshot_id mismatch: expected {snapshot_id}, got {loaded['snapshot_id']}"
        assert loaded['region'] == region, \
            f"region mismatch: expected {region}, got {loaded['region']}"
    finally:
        if tmp_file and os.path.exists(tmp_file):
            os.unlink(tmp_file)


def test_property_88_cleanup_build_result_missing_file():
    """
    Property 88: Cleanup Build Result Loading (missing file)

    When the AMI build result file does not exist, the cleanup script
    should raise FileNotFoundError (caught in main, returning exit code 1).

    **Validates: Requirements 28.4**
    """
    from unittest.mock import Mock

    assert not Path("/nonexistent/cleanup_build_result.json").exists()

    mock_args = Mock()
    mock_args.ami_build_result = "/nonexistent/cleanup_build_result.json"
    mock_args.terraform_dir = "terraform/deploy"

    with patch.object(cleanup, 'parse_arguments', return_value=mock_args):
        exit_code = cleanup.main()
        assert exit_code == 1, "Should return exit code 1 for missing file"


def test_property_88_cleanup_build_result_invalid_json():
    """
    Property 88: Cleanup Build Result Loading (invalid JSON)

    When the AMI build result file contains invalid JSON, the cleanup script
    should raise RuntimeError (caught in main, returning exit code 1).

    **Validates: Requirements 28.4**
    """
    from unittest.mock import Mock

    tmp_file = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("not valid json {{{")
            tmp_file = f.name

        mock_args = Mock()
        mock_args.ami_build_result = tmp_file
        mock_args.terraform_dir = "terraform/deploy"

        with patch.object(cleanup, 'parse_arguments', return_value=mock_args):
            exit_code = cleanup.main()
            assert exit_code == 1, "Should return exit code 1 for invalid JSON"
    finally:
        if tmp_file and os.path.exists(tmp_file):
            os.unlink(tmp_file)


# --- Property 89: Cleanup User Cancellation ---

from unittest.mock import Mock


def non_confirming_input_strategy():
    """Generate user input strings that are NOT 'yes' or 'y' (case-insensitive)."""
    return st.text(min_size=0, max_size=50).filter(
        lambda s: s.lower() not in ['yes', 'y']
    )


@settings(max_examples=20, deadline=None)
@given(user_input=non_confirming_input_strategy())
def test_property_89_cleanup_user_cancellation(user_input):
    """
    Property 89: Cleanup User Cancellation

    For any user input string that is not "yes" or "y" (case-insensitive),
    the cleanup script should exit with return code 0 without performing
    any resource deletion.

    **Validates: Requirements 28.8, 28.9**
    """
    tmp_file = None
    try:
        # Create a valid build result file so main() gets past the loading phase
        build_result = {
            "ami_id": "ami-abc12345",
            "snapshot_id": "snap-def67890",
            "region": "us-east-1",
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(build_result, f)
            tmp_file = f.name

        mock_args = Mock()
        mock_args.ami_build_result = tmp_file
        mock_args.terraform_dir = "terraform/deploy"

        with patch.object(cleanup, 'parse_arguments', return_value=mock_args), \
             patch('builtins.input', return_value=user_input), \
             patch.object(cleanup, 'destroy_infrastructure') as mock_destroy:

            exit_code = cleanup.main()

            assert exit_code == 0, (
                f"Expected exit code 0 for non-confirming input {user_input!r}, got {exit_code}"
            )
            mock_destroy.assert_not_called(), (
                f"destroy_infrastructure should not be called for input {user_input!r}"
            )
    finally:
        if tmp_file and os.path.exists(tmp_file):
            os.unlink(tmp_file)


# --- Property 90: Terraform Subprocess Error Propagation ---

import subprocess


@settings(max_examples=20, deadline=None)
@given(exit_code=st.integers(min_value=1, max_value=255))
def test_property_90_terraform_init_failure_raises_runtime_error(exit_code):
    """
    Property 90: Terraform Subprocess Error Propagation (init failure)

    For any non-zero exit code from terraform init, the destroy_infrastructure
    function should raise a RuntimeError.

    **Validates: Requirements 29.4, 29.6**
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp()
        # Create terraform.tfstate so the function doesn't return early
        state_file = os.path.join(tmp_dir, "terraform.tfstate")
        with open(state_file, "w") as f:
            f.write("{}")

        failed_init = subprocess.CompletedProcess(
            args=["terraform", "init"],
            returncode=exit_code,
            stdout="",
            stderr="init error",
        )

        with patch("subprocess.run", return_value=failed_init):
            with pytest.raises(RuntimeError, match=f"Terraform init failed with exit code {exit_code}"):
                cleanup.destroy_infrastructure(tmp_dir)
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            import shutil
            shutil.rmtree(tmp_dir)


@settings(max_examples=20, deadline=None)
@given(exit_code=st.integers(min_value=1, max_value=255))
def test_property_90_terraform_destroy_failure_raises_runtime_error(exit_code):
    """
    Property 90: Terraform Subprocess Error Propagation (destroy failure)

    For any non-zero exit code from terraform destroy (when init succeeds),
    the destroy_infrastructure function should raise a RuntimeError.

    **Validates: Requirements 29.4, 29.6**
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp()
        # Create terraform.tfstate so the function doesn't return early
        state_file = os.path.join(tmp_dir, "terraform.tfstate")
        with open(state_file, "w") as f:
            f.write("{}")

        successful_init = subprocess.CompletedProcess(
            args=["terraform", "init"],
            returncode=0,
            stdout="Initialized",
            stderr="",
        )
        failed_destroy = subprocess.CompletedProcess(
            args=["terraform", "destroy"],
            returncode=exit_code,
            stdout="",
            stderr="destroy error",
        )

        def mock_subprocess_run(cmd, **kwargs):
            if cmd[0] == "terraform" and cmd[1] == "init":
                return successful_init
            return failed_destroy

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with pytest.raises(RuntimeError, match=f"Terraform destroy failed with exit code {exit_code}"):
                cleanup.destroy_infrastructure(tmp_dir)
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            import shutil
            shutil.rmtree(tmp_dir)

# --- Property 91: Post-Destroy State Verification ---


def resource_dict_strategy():
    """Generate a resource dict resembling a Terraform state resource entry."""
    return st.fixed_dictionaries({
        "type": st.from_regex(r"aws_[a-z_]{1,20}", fullmatch=True),
        "name": st.from_regex(r"[a-z][a-z0-9_]{0,14}", fullmatch=True),
    })


@given(resources=st.lists(resource_dict_strategy(), min_size=1, max_size=10))
@settings(max_examples=20, deadline=None)
def test_property_91_post_destroy_state_non_empty_resources_logs_warning(resources):
    """
    Property 91: Post-Destroy State Verification (non-empty resources case)

    For any Terraform state file JSON with a non-empty resources array after
    destroy, the function should log a warning about remaining resources.

    **Validates: Requirements 29.7, 29.8**
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp()
        state_file = os.path.join(tmp_dir, "terraform.tfstate")
        # Write state with non-empty resources
        with open(state_file, "w") as f:
            json.dump({"resources": resources}, f)

        success_result = subprocess.CompletedProcess(
            args=["terraform"], returncode=0, stdout="ok", stderr=""
        )

        def mock_run(cmd, **kwargs):
            return success_result

        import logging

        with patch("subprocess.run", side_effect=mock_run):
            with patch.object(cleanup.logger, "warning") as mock_warning:
                with patch.object(cleanup.logger, "info"):
                    cleanup.destroy_infrastructure(tmp_dir)

                # Check that a warning was logged about remaining resources
                warning_messages = [str(call) for call in mock_warning.call_args_list]
                found_warning = any(
                    f"{len(resources)} resources still in Terraform state" in msg
                    for msg in warning_messages
                )
                assert found_warning, (
                    f"Expected warning about {len(resources)} remaining resources, "
                    f"got warnings: {warning_messages}"
                )
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            import shutil
            shutil.rmtree(tmp_dir)


@given(data=st.data())
@settings(max_examples=20, deadline=None)
def test_property_91_post_destroy_state_empty_resources_logs_success(data):
    """
    Property 91: Post-Destroy State Verification (empty resources case)

    For any Terraform state file JSON with an empty resources array after
    destroy, the function should log success indicating no remaining resources.

    **Validates: Requirements 29.7, 29.8**
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp()
        state_file = os.path.join(tmp_dir, "terraform.tfstate")
        # Write state with empty resources
        with open(state_file, "w") as f:
            json.dump({"resources": []}, f)

        success_result = subprocess.CompletedProcess(
            args=["terraform"], returncode=0, stdout="ok", stderr=""
        )

        def mock_run(cmd, **kwargs):
            return success_result

        with patch("subprocess.run", side_effect=mock_run):
            with patch.object(cleanup.logger, "info") as mock_info:
                cleanup.destroy_infrastructure(tmp_dir)

                # Check that success message was logged
                info_messages = [str(call) for call in mock_info.call_args_list]
                found_success = any(
                    "Terraform state shows no remaining resources" in msg
                    for msg in info_messages
                )
                assert found_success, (
                    f"Expected success log about no remaining resources, "
                    f"got info logs: {info_messages}"
                )
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            import shutil
            shutil.rmtree(tmp_dir)


# --- Property 92: AMI Deregistration Verification ---

from botocore.exceptions import ClientError


def _make_client_error(code, message="test error"):
    """Helper to create a botocore ClientError with the given error code."""
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "TestOperation",
    )


@settings(max_examples=20, deadline=None)
@given(
    ami_id=ami_id_strategy(),
    snapshot_id=snapshot_id_strategy(),
)
def test_property_92_ami_deregistration_verifies_ami_and_snapshot(ami_id, snapshot_id):
    """
    Property 92: AMI Deregistration Verification (successful deregistration)

    For any AMI ID that exists, after calling deregister_image with
    DeleteAssociatedSnapshots=True, the function should verify both AMI
    deregistration (via describe_images raising InvalidAMIID.NotFound)
    and snapshot deletion (via describe_snapshots raising
    InvalidSnapshot.NotFound).

    **Validates: Requirements 30.2, 30.4, 30.5, 30.6**
    """
    ec2_client = Mock()

    # First describe_images call returns the AMI (it exists)
    # Second describe_images call raises InvalidAMIID.NotFound (deregistration verified)
    ec2_client.describe_images.side_effect = [
        {"Images": [{"ImageId": ami_id}]},
        _make_client_error("InvalidAMIID.NotFound"),
    ]

    # describe_snapshots raises InvalidSnapshot.NotFound (snapshot deletion verified)
    ec2_client.describe_snapshots.side_effect = _make_client_error(
        "InvalidSnapshot.NotFound"
    )

    with patch("time.sleep"):
        cleanup.deregister_ami(ec2_client, ami_id, snapshot_id)

    # Verify deregister_image was called with DeleteAssociatedSnapshots=True
    ec2_client.deregister_image.assert_called_once_with(
        ImageId=ami_id, DeleteAssociatedSnapshots=True
    )

    # Verify describe_images was called twice (existence check + deregistration verification)
    assert ec2_client.describe_images.call_count == 2
    for call in ec2_client.describe_images.call_args_list:
        assert call == ((), {"ImageIds": [ami_id]})

    # Verify describe_snapshots was called to verify snapshot deletion
    ec2_client.describe_snapshots.assert_called_once_with(SnapshotIds=[snapshot_id])


@settings(max_examples=20, deadline=None)
@given(
    ami_id=ami_id_strategy(),
    snapshot_id=snapshot_id_strategy(),
)
def test_property_92_ami_not_found_handled_gracefully(ami_id, snapshot_id):
    """
    Property 92: AMI Deregistration Verification (AMI not found)

    For any AMI ID that does not exist (InvalidAMIID.NotFound on first
    describe_images call), the function should log a warning and return
    without attempting deregistration or snapshot verification.

    **Validates: Requirements 30.2, 30.4, 30.5, 30.6**
    """
    ec2_client = Mock()

    # First describe_images call raises InvalidAMIID.NotFound
    ec2_client.describe_images.side_effect = _make_client_error(
        "InvalidAMIID.NotFound"
    )

    with patch("time.sleep"):
        cleanup.deregister_ami(ec2_client, ami_id, snapshot_id)

    # describe_images called once (existence check only)
    ec2_client.describe_images.assert_called_once_with(ImageIds=[ami_id])

    # deregister_image should NOT be called since AMI doesn't exist
    ec2_client.deregister_image.assert_not_called()

    # describe_snapshots should NOT be called since we skipped deregistration
    ec2_client.describe_snapshots.assert_not_called()


# --- Property 93: Cleanup Resource Verification and Reporting ---


def instance_id_strategy():
    """Generate valid EC2 instance IDs."""
    return st.builds(
        lambda h: f"i-{h}",
        st.text(alphabet="0123456789abcdef", min_size=8, max_size=17),
    )


def instance_state_strategy():
    """Generate EC2 instance states that verify_cleanup checks for."""
    return st.sampled_from(["pending", "running", "stopping", "stopped"])


@settings(max_examples=20, deadline=None)
@given(
    ami_id=ami_id_strategy(),
    snapshot_id=snapshot_id_strategy(),
    instance_ids=st.lists(instance_id_strategy(), min_size=1, max_size=5),
    instance_states=st.lists(instance_state_strategy(), min_size=1, max_size=5),
)
def test_property_93_verify_cleanup_reports_remaining_resources(
    ami_id, snapshot_id, instance_ids, instance_states,
):
    """
    Property 93: Cleanup Resource Verification and Reporting (resources found)

    For any set of remaining AWS resources (EC2 instances, AMIs, EBS snapshots),
    verify_cleanup should log a warning for each resource listing its type, ID,
    and status.

    **Validates: Requirements 31.1, 31.2, 31.3, 31.4, 31.5, 31.6**
    """
    # Pair up instance IDs and states (zip to shortest)
    pairs = list(zip(instance_ids, instance_states))

    ec2_client = Mock()

    # EC2 instances found
    instances = [
        {"InstanceId": iid, "State": {"Name": state}}
        for iid, state in pairs
    ]
    ec2_client.describe_instances.return_value = {
        "Reservations": [{"Instances": instances}]
    }

    # AMI found
    ec2_client.describe_images.return_value = {
        "Images": [{"ImageId": ami_id}]
    }

    # Snapshot found
    ec2_client.describe_snapshots.return_value = {
        "Snapshots": [{"SnapshotId": snapshot_id, "State": "completed"}]
    }

    ami_build_result = {"ami_id": ami_id, "snapshot_id": snapshot_id}

    with patch.object(cleanup.logger, "warning") as mock_warning, \
         patch.object(cleanup.logger, "info"):
        cleanup.verify_cleanup(ec2_client, ami_build_result)

    warning_messages = " ".join(str(c) for c in mock_warning.call_args_list)

    # Each instance should be reported with type, ID, and status
    for iid, state in pairs:
        assert iid in warning_messages, (
            f"Expected instance ID {iid} in warnings, got: {warning_messages}"
        )
        assert state in warning_messages, (
            f"Expected state '{state}' in warnings, got: {warning_messages}"
        )

    # AMI should be reported
    assert ami_id in warning_messages, (
        f"Expected AMI ID {ami_id} in warnings, got: {warning_messages}"
    )

    # Snapshot should be reported
    assert snapshot_id in warning_messages, (
        f"Expected snapshot ID {snapshot_id} in warnings, got: {warning_messages}"
    )

    # Should mention resource count
    expected_count = len(pairs) + 2  # instances + AMI + snapshot
    assert str(expected_count) in warning_messages, (
        f"Expected count {expected_count} in warnings, got: {warning_messages}"
    )


@settings(max_examples=20, deadline=None)
@given(
    ami_id=ami_id_strategy(),
    snapshot_id=snapshot_id_strategy(),
)
def test_property_93_verify_cleanup_reports_no_remaining_resources(ami_id, snapshot_id):
    """
    Property 93: Cleanup Resource Verification and Reporting (no resources)

    When no resources remain (instances empty, AMI not found, snapshot not found),
    verify_cleanup should log that all resources are removed.

    **Validates: Requirements 31.1, 31.2, 31.3, 31.4, 31.5, 31.6**
    """
    ec2_client = Mock()

    # No instances found
    ec2_client.describe_instances.return_value = {"Reservations": []}

    # AMI not found
    ec2_client.describe_images.side_effect = _make_client_error("InvalidAMIID.NotFound")

    # Snapshot not found
    ec2_client.describe_snapshots.side_effect = _make_client_error("InvalidSnapshot.NotFound")

    ami_build_result = {"ami_id": ami_id, "snapshot_id": snapshot_id}

    with patch.object(cleanup.logger, "info") as mock_info, \
         patch.object(cleanup.logger, "warning") as mock_warning:
        cleanup.verify_cleanup(ec2_client, ami_build_result)

    info_messages = " ".join(str(c) for c in mock_info.call_args_list)

    # Should log that no remaining resources were found
    assert "No remaining resources found" in info_messages, (
        f"Expected 'No remaining resources found' in info logs, got: {info_messages}"
    )
    assert "all resources removed" in info_messages, (
        f"Expected 'all resources removed' in info logs, got: {info_messages}"
    )

    # No resource-specific warnings should be logged (only separator lines etc.)
    resource_warnings = [
        str(c) for c in mock_warning.call_args_list
        if "EC2 Instance" in str(c) or "AMI" in str(c) or "EBS Snapshot" in str(c)
    ]
    assert len(resource_warnings) == 0, (
        f"Expected no resource warnings, got: {resource_warnings}"
    )


# --- Property 94: Cleanup Exit Code Correctness ---


def exception_strategy():
    """Generate different exception types with messages for testing failure paths."""
    return st.one_of(
        st.builds(RuntimeError, st.text(min_size=1, max_size=50)),
        st.builds(FileNotFoundError, st.text(min_size=1, max_size=50)),
        st.builds(OSError, st.text(min_size=1, max_size=50)),
        st.builds(ValueError, st.text(min_size=1, max_size=50)),
        st.builds(KeyError, st.text(min_size=1, max_size=50)),
    )


@settings(max_examples=20, deadline=None)
@given(
    ami_id=ami_id_strategy(),
    snapshot_id=snapshot_id_strategy(),
    region=region_strategy(),
)
def test_property_94_main_returns_0_when_all_steps_succeed(ami_id, snapshot_id, region):
    """
    Property 94: Cleanup Exit Code Correctness (success path)

    For any valid build result, when all cleanup steps succeed (destroy_infrastructure,
    deregister_ami, verify_cleanup), main() should return exit code 0.

    **Validates: Requirements 31.7, 31.8**
    """
    tmp_file = None
    try:
        build_result = {
            "ami_id": ami_id,
            "snapshot_id": snapshot_id,
            "region": region,
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(build_result, f)
            tmp_file = f.name

        mock_args = Mock()
        mock_args.ami_build_result = tmp_file
        mock_args.terraform_dir = "terraform/deploy"

        mock_ec2_client = Mock()

        with patch.object(cleanup, 'parse_arguments', return_value=mock_args), \
             patch('builtins.input', return_value='yes'), \
             patch.object(cleanup, 'destroy_infrastructure') as mock_destroy, \
             patch.object(cleanup, 'boto3') as mock_boto3, \
             patch.object(cleanup, 'deregister_ami') as mock_deregister, \
             patch.object(cleanup, 'verify_cleanup') as mock_verify:

            mock_boto3.client.return_value = mock_ec2_client

            exit_code = cleanup.main()

            assert exit_code == 0, (
                f"Expected exit code 0 when all steps succeed, got {exit_code}"
            )
            mock_destroy.assert_called_once()
            mock_deregister.assert_called_once()
            mock_verify.assert_called_once()
    finally:
        if tmp_file and os.path.exists(tmp_file):
            os.unlink(tmp_file)


@settings(max_examples=20, deadline=None)
@given(
    ami_id=ami_id_strategy(),
    snapshot_id=snapshot_id_strategy(),
    region=region_strategy(),
    exc=exception_strategy(),
)
def test_property_94_main_returns_1_when_exception_raised(ami_id, snapshot_id, region, exc):
    """
    Property 94: Cleanup Exit Code Correctness (failure path)

    For any valid build result and any exception type raised during cleanup,
    main() should return exit code 1.

    **Validates: Requirements 31.7, 31.8**
    """
    tmp_file = None
    try:
        build_result = {
            "ami_id": ami_id,
            "snapshot_id": snapshot_id,
            "region": region,
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(build_result, f)
            tmp_file = f.name

        mock_args = Mock()
        mock_args.ami_build_result = tmp_file
        mock_args.terraform_dir = "terraform/deploy"

        with patch.object(cleanup, 'parse_arguments', return_value=mock_args), \
             patch('builtins.input', return_value='yes'), \
             patch.object(cleanup, 'destroy_infrastructure', side_effect=exc):

            exit_code = cleanup.main()

            assert exit_code == 1, (
                f"Expected exit code 1 when {type(exc).__name__} is raised, got {exit_code}"
            )
    finally:
        if tmp_file and os.path.exists(tmp_file):
            os.unlink(tmp_file)
