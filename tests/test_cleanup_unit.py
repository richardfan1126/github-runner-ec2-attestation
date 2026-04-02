"""
Unit tests for the cleanup script (scripts/cleanup.py).

Tests cover: parse_arguments, build result loading, user confirmation,
destroy_infrastructure, deregister_ami, verify_cleanup, and main exit codes.

Validates: Requirements 28.1-28.9, 29.1-29.8, 30.1-30.7, 31.1-31.8
"""

import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

# Import cleanup module using importlib (same pattern as test_cleanup_script_properties.py)
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("cleanup", scripts_dir / "cleanup.py")
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


# --- Helpers ---

def _make_client_error(code, message="test error"):
    """Create a botocore ClientError with the given error code."""
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "TestOperation",
    )


def _write_temp_json(data):
    """Write data as JSON to a temp file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


VALID_BUILD_RESULT = {
    "ami_id": "ami-0123456789abcdef0",
    "snapshot_id": "snap-0123456789abcdef0",
    "region": "us-east-1",
}


# =============================================================================
# parse_arguments tests
# =============================================================================

class TestParseArguments:
    def test_parse_arguments_defaults(self):
        with patch("sys.argv", ["cleanup.py"]):
            args = cleanup.parse_arguments()
        assert args.ami_build_result == "ami_build_result.json"
        expected = (Path(__file__).parent.parent / "scripts" / ".." / "terraform" / "deploy").resolve()
        assert Path(str(args.terraform_dir)).resolve() == expected

    def test_parse_arguments_custom_ami_build_result(self):
        with patch("sys.argv", ["cleanup.py", "--ami-build-result", "custom.json"]):
            args = cleanup.parse_arguments()
        assert args.ami_build_result == "custom.json"
        expected = (Path(__file__).parent.parent / "scripts" / ".." / "terraform" / "deploy").resolve()
        assert Path(str(args.terraform_dir)).resolve() == expected

    def test_parse_arguments_custom_terraform_dir(self):
        with patch("sys.argv", ["cleanup.py", "--terraform-dir", "custom/dir"]):
            args = cleanup.parse_arguments()
        assert args.ami_build_result == "ami_build_result.json"
        assert args.terraform_dir == "custom/dir"

    def test_parse_arguments_both_custom(self):
        with patch("sys.argv", [
            "cleanup.py",
            "--ami-build-result", "my_result.json",
            "--terraform-dir", "infra/tf",
        ]):
            args = cleanup.parse_arguments()
        assert args.ami_build_result == "my_result.json"
        assert args.terraform_dir == "infra/tf"

    def test_parse_arguments_keep_ami_absent(self):
        with patch("sys.argv", ["cleanup.py"]):
            args = cleanup.parse_arguments()
        assert args.keep_ami is False

    def test_parse_arguments_keep_ami_present(self):
        with patch("sys.argv", ["cleanup.py", "--keep-ami"]):
            args = cleanup.parse_arguments()
        assert args.keep_ami is True


# =============================================================================
# Build result loading tests (via main)
# =============================================================================

class TestBuildResultLoading:
    def test_build_result_missing_file(self):
        mock_args = Mock()
        mock_args.ami_build_result = "/nonexistent/file.json"
        mock_args.terraform_dir = "terraform/deploy"

        with patch.object(cleanup, "parse_arguments", return_value=mock_args):
            assert cleanup.main() == 1

    def test_build_result_empty_file(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write("")
        tmp.close()
        try:
            mock_args = Mock()
            mock_args.ami_build_result = tmp.name
            mock_args.terraform_dir = "terraform/deploy"

            with patch.object(cleanup, "parse_arguments", return_value=mock_args):
                assert cleanup.main() == 1
        finally:
            os.unlink(tmp.name)

    def test_build_result_invalid_json(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write("{not valid json!!!")
        tmp.close()
        try:
            mock_args = Mock()
            mock_args.ami_build_result = tmp.name
            mock_args.terraform_dir = "terraform/deploy"

            with patch.object(cleanup, "parse_arguments", return_value=mock_args):
                assert cleanup.main() == 1
        finally:
            os.unlink(tmp.name)

    def test_build_result_missing_fields(self):
        """Valid JSON but missing required fields (ami_id, snapshot_id, region) -> KeyError -> exit 1."""
        tmp = _write_temp_json({"some_key": "some_value"})
        try:
            mock_args = Mock()
            mock_args.ami_build_result = tmp
            mock_args.terraform_dir = "terraform/deploy"

            with patch.object(cleanup, "parse_arguments", return_value=mock_args):
                assert cleanup.main() == 1
        finally:
            os.unlink(tmp)


# =============================================================================
# User confirmation tests (via main)
# =============================================================================

class TestUserConfirmation:
    """Test that user input is handled correctly in main()."""

    def _run_main_with_input(self, user_input):
        tmp = _write_temp_json(VALID_BUILD_RESULT)
        try:
            mock_args = Mock()
            mock_args.ami_build_result = tmp
            mock_args.terraform_dir = "terraform/deploy"
            mock_args.keep_ami = False

            with patch.object(cleanup, "parse_arguments", return_value=mock_args), \
                 patch("builtins.input", return_value=user_input), \
                 patch.object(cleanup, "destroy_infrastructure") as mock_destroy, \
                 patch.object(cleanup, "boto3") as mock_boto3, \
                 patch.object(cleanup, "deregister_ami"), \
                 patch.object(cleanup, "verify_cleanup"):
                mock_boto3.client.return_value = Mock()
                exit_code = cleanup.main()
                return exit_code, mock_destroy.called
        finally:
            os.unlink(tmp)

    def test_user_confirms_yes(self):
        code, destroyed = self._run_main_with_input("yes")
        assert code == 0
        assert destroyed is True

    def test_user_confirms_y(self):
        code, destroyed = self._run_main_with_input("y")
        assert code == 0
        assert destroyed is True

    def test_user_confirms_Yes(self):
        code, destroyed = self._run_main_with_input("Yes")
        assert code == 0
        assert destroyed is True

    def test_user_confirms_Y(self):
        code, destroyed = self._run_main_with_input("Y")
        assert code == 0
        assert destroyed is True

    def test_user_cancels_no(self):
        code, destroyed = self._run_main_with_input("no")
        assert code == 0
        assert destroyed is False

    def test_user_cancels_n(self):
        code, destroyed = self._run_main_with_input("n")
        assert code == 0
        assert destroyed is False

    def test_user_cancels_empty(self):
        code, destroyed = self._run_main_with_input("")
        assert code == 0
        assert destroyed is False

    def test_user_cancels_maybe(self):
        code, destroyed = self._run_main_with_input("maybe")
        assert code == 0
        assert destroyed is False


# =============================================================================
# destroy_infrastructure tests
# =============================================================================

class TestDestroyInfrastructure:
    def test_destroy_missing_directory(self):
        """Nonexistent directory -> returns without error (logs warning)."""
        cleanup.destroy_infrastructure("/nonexistent/terraform/dir")

    def test_destroy_missing_state(self):
        """Directory exists but no terraform.tfstate -> returns without error."""
        tmp_dir = tempfile.mkdtemp()
        try:
            cleanup.destroy_infrastructure(tmp_dir)
        finally:
            os.rmdir(tmp_dir)

    def test_destroy_init_failure(self):
        """terraform init fails -> raises RuntimeError."""
        tmp_dir = tempfile.mkdtemp()
        state_file = os.path.join(tmp_dir, "terraform.tfstate")
        with open(state_file, "w") as f:
            f.write("{}")
        try:
            failed_init = subprocess.CompletedProcess(
                args=["terraform", "init"], returncode=1,
                stdout="", stderr="init error",
            )
            with patch("subprocess.run", return_value=failed_init):
                with pytest.raises(RuntimeError, match="Terraform init failed"):
                    cleanup.destroy_infrastructure(tmp_dir)
        finally:
            os.unlink(state_file)
            os.rmdir(tmp_dir)

    def test_destroy_destroy_failure(self):
        """terraform destroy fails -> raises RuntimeError."""
        tmp_dir = tempfile.mkdtemp()
        state_file = os.path.join(tmp_dir, "terraform.tfstate")
        with open(state_file, "w") as f:
            f.write("{}")
        try:
            ok_init = subprocess.CompletedProcess(
                args=["terraform", "init"], returncode=0,
                stdout="Initialized", stderr="",
            )
            failed_destroy = subprocess.CompletedProcess(
                args=["terraform", "destroy"], returncode=1,
                stdout="", stderr="destroy error",
            )

            def side_effect(cmd, **kwargs):
                if "init" in cmd:
                    return ok_init
                return failed_destroy

            with patch("subprocess.run", side_effect=side_effect):
                with pytest.raises(RuntimeError, match="Terraform destroy failed"):
                    cleanup.destroy_infrastructure(tmp_dir)
        finally:
            os.unlink(state_file)
            os.rmdir(tmp_dir)

    def test_destroy_successful(self):
        """Both init and destroy succeed -> no error raised."""
        tmp_dir = tempfile.mkdtemp()
        state_file = os.path.join(tmp_dir, "terraform.tfstate")
        with open(state_file, "w") as f:
            json.dump({"resources": []}, f)
        try:
            ok_result = subprocess.CompletedProcess(
                args=["terraform"], returncode=0,
                stdout="Success", stderr="",
            )
            with patch("subprocess.run", return_value=ok_result):
                cleanup.destroy_infrastructure(tmp_dir)  # should not raise
        finally:
            os.unlink(state_file)
            os.rmdir(tmp_dir)


# =============================================================================
# deregister_ami tests
# =============================================================================

class TestDeregisterAmi:
    def test_deregister_ami_exists(self):
        """AMI exists, deregisters successfully, verifies deletion."""
        ec2 = Mock()
        ami_id = "ami-abc12345"
        snap_id = "snap-def67890"

        # First call: AMI exists; second call: AMI gone
        ec2.describe_images.side_effect = [
            {"Images": [{"ImageId": ami_id}]},
            _make_client_error("InvalidAMIID.NotFound"),
        ]
        ec2.describe_snapshots.side_effect = _make_client_error("InvalidSnapshot.NotFound")

        with patch("time.sleep"):
            cleanup.deregister_ami(ec2, ami_id, snap_id)

        ec2.deregister_image.assert_called_once_with(
            ImageId=ami_id, DeleteAssociatedSnapshots=True,
        )
        assert ec2.describe_images.call_count == 2
        ec2.describe_snapshots.assert_called_once_with(SnapshotIds=[snap_id])

    def test_deregister_ami_not_found(self):
        """InvalidAMIID.NotFound on first check -> returns without error."""
        ec2 = Mock()
        ec2.describe_images.side_effect = _make_client_error("InvalidAMIID.NotFound")

        with patch("time.sleep"):
            cleanup.deregister_ami(ec2, "ami-missing", "snap-missing")

        ec2.deregister_image.assert_not_called()

    def test_deregister_ami_api_error(self):
        """Other ClientError on describe_images -> raises."""
        ec2 = Mock()
        ec2.describe_images.side_effect = _make_client_error("UnauthorizedAccess", "no perms")

        with pytest.raises(ClientError):
            cleanup.deregister_ami(ec2, "ami-err", "snap-err")

    def test_deregister_ami_keep_ami_true_skips_all(self):
        """keep_ami=True -> skips all API calls and logs skip message."""
        ec2 = Mock()

        with patch.object(cleanup.logger, "info") as mock_info:
            cleanup.deregister_ami(ec2, "ami-abc123", "snap-def456", keep_ami=True)

        ec2.describe_images.assert_not_called()
        ec2.deregister_image.assert_not_called()
        ec2.describe_snapshots.assert_not_called()

        info_msgs = " ".join(str(c) for c in mock_info.call_args_list)
        assert "Skipping AMI deregistration" in info_msgs

    def test_deregister_ami_keep_ami_false_proceeds(self):
        """keep_ami=False -> proceeds with normal deregistration."""
        ec2 = Mock()
        ec2.describe_images.side_effect = [
            {"Images": [{"ImageId": "ami-abc123"}]},
            _make_client_error("InvalidAMIID.NotFound"),
        ]
        ec2.describe_snapshots.side_effect = _make_client_error("InvalidSnapshot.NotFound")

        with patch("time.sleep"):
            cleanup.deregister_ami(ec2, "ami-abc123", "snap-def456", keep_ami=False)

        ec2.deregister_image.assert_called_once()


# =============================================================================
# verify_cleanup tests
# =============================================================================

class TestVerifyCleanup:
    def _build_result(self, ami_id="ami-test123", snapshot_id="snap-test456"):
        return {"ami_id": ami_id, "snapshot_id": snapshot_id}

    def test_verify_no_remaining_resources(self):
        ec2 = Mock()
        ec2.describe_instances.return_value = {"Reservations": []}
        ec2.describe_images.side_effect = _make_client_error("InvalidAMIID.NotFound")
        ec2.describe_snapshots.side_effect = _make_client_error("InvalidSnapshot.NotFound")

        with patch.object(cleanup.logger, "info") as mock_info, \
             patch.object(cleanup.logger, "warning"):
            cleanup.verify_cleanup(ec2, self._build_result())

        info_msgs = " ".join(str(c) for c in mock_info.call_args_list)
        assert "No remaining resources found" in info_msgs

    def test_verify_ec2_instances_found(self):
        ec2 = Mock()
        ec2.describe_instances.return_value = {
            "Reservations": [{
                "Instances": [{"InstanceId": "i-abc123", "State": {"Name": "running"}}]
            }]
        }
        ec2.describe_images.side_effect = _make_client_error("InvalidAMIID.NotFound")
        ec2.describe_snapshots.side_effect = _make_client_error("InvalidSnapshot.NotFound")

        with patch.object(cleanup.logger, "warning") as mock_warn, \
             patch.object(cleanup.logger, "info"):
            cleanup.verify_cleanup(ec2, self._build_result())

        warn_msgs = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "i-abc123" in warn_msgs
        assert "1 remaining" in warn_msgs

    def test_verify_ami_found(self):
        ec2 = Mock()
        ec2.describe_instances.return_value = {"Reservations": []}
        ec2.describe_images.return_value = {"Images": [{"ImageId": "ami-test123"}]}
        ec2.describe_snapshots.side_effect = _make_client_error("InvalidSnapshot.NotFound")

        with patch.object(cleanup.logger, "warning") as mock_warn, \
             patch.object(cleanup.logger, "info"):
            cleanup.verify_cleanup(ec2, self._build_result())

        warn_msgs = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "ami-test123" in warn_msgs

    def test_verify_snapshot_found(self):
        ec2 = Mock()
        ec2.describe_instances.return_value = {"Reservations": []}
        ec2.describe_images.side_effect = _make_client_error("InvalidAMIID.NotFound")
        ec2.describe_snapshots.return_value = {
            "Snapshots": [{"SnapshotId": "snap-test456", "State": "completed"}]
        }

        with patch.object(cleanup.logger, "warning") as mock_warn, \
             patch.object(cleanup.logger, "info"):
            cleanup.verify_cleanup(ec2, self._build_result())

        warn_msgs = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "snap-test456" in warn_msgs

    def test_verify_mixed_resources(self):
        """Instances + AMI + snapshot all found."""
        ec2 = Mock()
        ec2.describe_instances.return_value = {
            "Reservations": [{
                "Instances": [
                    {"InstanceId": "i-111", "State": {"Name": "running"}},
                    {"InstanceId": "i-222", "State": {"Name": "stopped"}},
                ]
            }]
        }
        ec2.describe_images.return_value = {"Images": [{"ImageId": "ami-test123"}]}
        ec2.describe_snapshots.return_value = {
            "Snapshots": [{"SnapshotId": "snap-test456", "State": "completed"}]
        }

        with patch.object(cleanup.logger, "warning") as mock_warn, \
             patch.object(cleanup.logger, "info"):
            cleanup.verify_cleanup(ec2, self._build_result())

        warn_msgs = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "i-111" in warn_msgs
        assert "i-222" in warn_msgs
        assert "ami-test123" in warn_msgs
        assert "snap-test456" in warn_msgs
        assert "4 remaining" in warn_msgs

    def test_verify_keep_ami_skips_ami_and_snapshot(self):
        """keep_ami=True -> skips AMI and snapshot checks."""
        ec2 = Mock()
        ec2.describe_instances.return_value = {"Reservations": []}

        with patch.object(cleanup.logger, "info") as mock_info, \
             patch.object(cleanup.logger, "warning"):
            cleanup.verify_cleanup(ec2, self._build_result(), keep_ami=True)

        ec2.describe_images.assert_not_called()
        ec2.describe_snapshots.assert_not_called()

        info_msgs = " ".join(str(c) for c in mock_info.call_args_list)
        assert "intentionally preserved" in info_msgs

    def test_verify_keep_ami_still_reports_instances(self):
        """keep_ami=True with remaining instances -> reports instances only."""
        ec2 = Mock()
        ec2.describe_instances.return_value = {
            "Reservations": [{
                "Instances": [{"InstanceId": "i-abc123", "State": {"Name": "running"}}]
            }]
        }

        with patch.object(cleanup.logger, "warning") as mock_warn, \
             patch.object(cleanup.logger, "info"):
            cleanup.verify_cleanup(ec2, self._build_result(), keep_ami=True)

        ec2.describe_images.assert_not_called()
        ec2.describe_snapshots.assert_not_called()

        warn_msgs = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "i-abc123" in warn_msgs
        assert "1 remaining" in warn_msgs


# =============================================================================
# main exit code tests
# =============================================================================

class TestMainExitCodes:
    def test_main_full_success(self):
        tmp = _write_temp_json(VALID_BUILD_RESULT)
        try:
            mock_args = Mock()
            mock_args.ami_build_result = tmp
            mock_args.terraform_dir = "terraform/deploy"
            mock_args.keep_ami = False

            with patch.object(cleanup, "parse_arguments", return_value=mock_args), \
                 patch("builtins.input", return_value="yes"), \
                 patch.object(cleanup, "destroy_infrastructure"), \
                 patch.object(cleanup, "boto3") as mock_boto3, \
                 patch.object(cleanup, "deregister_ami"), \
                 patch.object(cleanup, "verify_cleanup"):
                mock_boto3.client.return_value = Mock()
                assert cleanup.main() == 0
        finally:
            os.unlink(tmp)

    def test_main_terraform_failure(self):
        tmp = _write_temp_json(VALID_BUILD_RESULT)
        try:
            mock_args = Mock()
            mock_args.ami_build_result = tmp
            mock_args.terraform_dir = "terraform/deploy"
            mock_args.keep_ami = False

            with patch.object(cleanup, "parse_arguments", return_value=mock_args), \
                 patch("builtins.input", return_value="yes"), \
                 patch.object(cleanup, "destroy_infrastructure",
                              side_effect=RuntimeError("Terraform destroy failed")):
                assert cleanup.main() == 1
        finally:
            os.unlink(tmp)

    def test_main_user_cancellation(self):
        tmp = _write_temp_json(VALID_BUILD_RESULT)
        try:
            mock_args = Mock()
            mock_args.ami_build_result = tmp
            mock_args.terraform_dir = "terraform/deploy"
            mock_args.keep_ami = False

            with patch.object(cleanup, "parse_arguments", return_value=mock_args), \
                 patch("builtins.input", return_value="no"), \
                 patch.object(cleanup, "destroy_infrastructure") as mock_destroy:
                assert cleanup.main() == 0
                mock_destroy.assert_not_called()
        finally:
            os.unlink(tmp)

    def test_main_keep_ami_passes_flag(self):
        """main() passes keep_ami to deregister_ami and verify_cleanup."""
        tmp = _write_temp_json(VALID_BUILD_RESULT)
        try:
            mock_args = Mock()
            mock_args.ami_build_result = tmp
            mock_args.terraform_dir = "terraform/deploy"
            mock_args.keep_ami = True

            with patch.object(cleanup, "parse_arguments", return_value=mock_args), \
                 patch("builtins.input", return_value="yes"), \
                 patch.object(cleanup, "destroy_infrastructure"), \
                 patch.object(cleanup, "boto3") as mock_boto3, \
                 patch.object(cleanup, "deregister_ami") as mock_deregister, \
                 patch.object(cleanup, "verify_cleanup") as mock_verify:
                mock_boto3.client.return_value = Mock()
                assert cleanup.main() == 0

                # Verify keep_ami was passed through
                mock_deregister.assert_called_once()
                _, kwargs = mock_deregister.call_args
                assert kwargs.get("keep_ami") is True

                mock_verify.assert_called_once()
                _, kwargs = mock_verify.call_args
                assert kwargs.get("keep_ami") is True
        finally:
            os.unlink(tmp)
