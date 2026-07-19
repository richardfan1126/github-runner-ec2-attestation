"""
Integration tests for the complete AMI build flow (single shared instance).

Tests the end-to-end multi-flavor build driver with mocked external services
(AWS, SSH, ORAS, GitHub CLI). The workflow — not the script — owns the Terraform
apply/destroy lifecycle, so the script is invoked against a pre-provisioned
instance (host + SSH key) and drives all flavors in two passes.

Requirements: 11-20
"""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Import build_ami module using importlib
import importlib.util
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)


def make_mock_args(output_dir="."):
    """Create mock command-line arguments for the single-instance CLI."""
    args = Mock()
    args.host = "54.1.2.3"
    args.ssh_key_path = "/tmp/key.pem"
    args.run_id = "123-1"
    args.flavors_manifest = "flavors-manifest.json"
    args.region = "us-east-1"
    args.output_dir = output_dir
    args.artifacts_base_path = "~/artifacts"
    args.allow_debug = False
    args.expected_workflow = None
    args.producing_commit = None
    return args


def make_flavor(name="default"):
    """Create a single manifest flavor entry."""
    return {
        "flavor": name,
        "artifact_ref": "ghcr.io/owner/repo@sha256:" + "a" * 64,
        "container_image_digest": "sha256:" + "c" * 64,
        "relaxations": {},
    }


def make_pcr_measurements():
    """Create valid PCR measurements dict."""
    return {
        "Measurements": {
            "PCR4": "abcdef1234567890" * 3,
            "PCR7": "1234567890abcdef" * 3,
        }
    }


def _happy_path_patches(mock_ssh, flavors, pcr_measurements):
    """Context managers patching a fully-successful per-flavor build."""
    return [
        patch.object(build_ami, 'validate_aws_region'),
        patch.object(build_ami, 'validate_run_id'),
        patch.object(build_ami, 'load_flavors_manifest', return_value=flavors),
        patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh),
        patch.object(build_ami, 'install_all_tools'),
        patch.object(build_ami, 'reset_artifacts_dir'),
        patch.object(build_ami, 'verify_artifact_signature', return_value=True),
        patch.object(build_ami, 'pull_artifact_from_ghcr'),
        patch.object(build_ami, 'validate_artifact_files'),
        patch.object(build_ami, 'check_debug_annotation'),
        patch.object(build_ami, 'validate_pcr_measurements', return_value=pcr_measurements),
        patch.object(build_ami, 'wait_for_snapshot'),
        patch.object(build_ami, 'boto3'),
    ]


class TestCompleteBuildFlow:
    """Test complete build flow with mocked external services."""

    def test_successful_build_flow(self, tmp_path):
        """A fully successful single-flavor build writes its per-flavor result and cleans up."""
        mock_args = make_mock_args(output_dir=str(tmp_path))
        mock_ssh = Mock()
        pcr_measurements = make_pcr_measurements()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'upload_snapshot', return_value="snap-abc123"), \
             patch.object(build_ami, 'register_ami', return_value="ami-xyz789"), \
             patch.object(build_ami, 'cleanup_script_resources') as mock_cleanup:
            for p in _happy_path_patches(mock_ssh, [make_flavor("default")], pcr_measurements):
                p.start()
            try:
                exit_code = build_ami.main()
            finally:
                patch.stopall()

        assert exit_code == 0

        # Per-flavor result file (task 4.7 naming)
        output_file = tmp_path / "ami_build_result-default.json"
        assert output_file.exists(), "Per-flavor result file must be written"
        result = json.loads(output_file.read_text())

        assert result["ami_id"] == "ami-xyz789"
        assert result["snapshot_id"] == "snap-abc123"
        assert result["region"] == "us-east-1"
        assert "build_timestamp" in result
        assert result["pcr_measurements"]["pcr4"] == pcr_measurements["Measurements"]["PCR4"]
        assert result["pcr_measurements"]["pcr7"] == pcr_measurements["Measurements"]["PCR7"]

        # Script-owned cleanup should always run
        mock_cleanup.assert_called_once()

    def test_multi_flavor_partial_failure_isolated(self, tmp_path):
        """One flavor's application error does not stop the others (D8 isolation)."""
        mock_args = make_mock_args(output_dir=str(tmp_path))
        mock_ssh = Mock()
        pcr_measurements = make_pcr_measurements()
        flavors = [make_flavor("gpu-presence"), make_flavor("rust-build")]

        # gpu-presence fails signature (application error); rust-build succeeds. The
        # side_effect list maps to the two sequential Pass-1 verify calls.
        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_run_id'), \
             patch.object(build_ami, 'load_flavors_manifest', return_value=flavors), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools'), \
             patch.object(build_ami, 'reset_artifacts_dir'), \
             patch.object(build_ami, 'verify_artifact_signature', side_effect=[False, True]), \
             patch.object(build_ami, 'pull_artifact_from_ghcr'), \
             patch.object(build_ami, 'validate_artifact_files'), \
             patch.object(build_ami, 'check_debug_annotation'), \
             patch.object(build_ami, 'validate_pcr_measurements', return_value=pcr_measurements), \
             patch.object(build_ami, 'upload_snapshot', return_value="snap-ok"), \
             patch.object(build_ami, 'wait_for_snapshot'), \
             patch.object(build_ami, 'register_ami', return_value="ami-ok"), \
             patch.object(build_ami, 'cleanup_script_resources'), \
             patch.object(build_ami, 'boto3'):

            exit_code = build_ami.main()

        # At least one flavor succeeded → run succeeds.
        assert exit_code == 0
        # The failed flavor produced no result; the succeeded one did.
        assert not (tmp_path / "ami_build_result-gpu-presence.json").exists()
        assert (tmp_path / "ami_build_result-rust-build.json").exists()


class TestSignatureVerificationFailure:
    """Test signature verification failure handling."""

    def test_signature_failure_returns_exit_code_1(self, tmp_path):
        """A sole flavor failing signature verification yields exit code 1."""
        mock_args = make_mock_args(output_dir=str(tmp_path))
        mock_ssh = Mock()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_run_id'), \
             patch.object(build_ami, 'load_flavors_manifest', return_value=[make_flavor()]), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools'), \
             patch.object(build_ami, 'reset_artifacts_dir'), \
             patch.object(build_ami, 'verify_artifact_signature', return_value=False), \
             patch.object(build_ami, 'register_ami') as mock_register, \
             patch.object(build_ami, 'cleanup_script_resources') as mock_cleanup, \
             patch.object(build_ami, 'boto3'):

            exit_code = build_ami.main()

            assert exit_code == 1
            # Signature failure must never register an AMI.
            mock_register.assert_not_called()
            mock_cleanup.assert_called_once()


class TestToolInstallationGate:
    """Install is a gate: any failure hard-aborts the whole run (D10)."""

    def test_tool_install_failure_hard_aborts_and_cleans_up(self, tmp_path):
        """If the toolchain install fails, the run aborts (exit 1) and cleanup runs."""
        mock_args = make_mock_args(output_dir=str(tmp_path))
        mock_ssh = Mock()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_run_id'), \
             patch.object(build_ami, 'load_flavors_manifest', return_value=[make_flavor()]), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools', side_effect=RuntimeError("coldsnap install failed")), \
             patch.object(build_ami, 'register_ami') as mock_register, \
             patch.object(build_ami, 'cleanup_script_resources') as mock_cleanup, \
             patch.object(build_ami, 'boto3'):

            exit_code = build_ami.main()

            assert exit_code == 1
            mock_register.assert_not_called()
            mock_cleanup.assert_called_once()
            # cleanup must receive the live ssh client for closing
            assert mock_cleanup.call_args[1]['ssh_client'] is mock_ssh


class TestCleanupOnVariousFailures:
    """Cleanup (SSH close + key delete) always runs; the workflow owns destroy."""

    def test_cleanup_on_connect_failure(self, tmp_path):
        """If connecting to the pre-provisioned instance fails, cleanup still runs."""
        mock_args = make_mock_args(output_dir=str(tmp_path))

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_run_id'), \
             patch.object(build_ami, 'load_flavors_manifest', return_value=[make_flavor()]), \
             patch.object(build_ami, 'verify_ssh_connectivity',
                          side_effect=RuntimeError("SSH connect failed")), \
             patch.object(build_ami, 'cleanup_script_resources') as mock_cleanup, \
             patch.object(build_ami, 'boto3'):

            exit_code = build_ami.main()

            assert exit_code == 1
            mock_cleanup.assert_called_once()

    def test_cleanup_on_snapshot_upload_failure(self, tmp_path):
        """An upload failure fails that flavor; with no successes the run exits 1 and cleans up."""
        mock_args = make_mock_args(output_dir=str(tmp_path))
        mock_ssh = Mock()
        pcr_measurements = make_pcr_measurements()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_run_id'), \
             patch.object(build_ami, 'load_flavors_manifest', return_value=[make_flavor()]), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools'), \
             patch.object(build_ami, 'reset_artifacts_dir'), \
             patch.object(build_ami, 'verify_artifact_signature', return_value=True), \
             patch.object(build_ami, 'pull_artifact_from_ghcr'), \
             patch.object(build_ami, 'validate_artifact_files'), \
             patch.object(build_ami, 'check_debug_annotation'), \
             patch.object(build_ami, 'validate_pcr_measurements', return_value=pcr_measurements), \
             patch.object(build_ami, 'upload_snapshot', side_effect=RuntimeError("coldsnap upload failed")), \
             patch.object(build_ami, 'register_ami') as mock_register, \
             patch.object(build_ami, 'cleanup_script_resources') as mock_cleanup, \
             patch.object(build_ami, 'boto3'):

            exit_code = build_ami.main()

            assert exit_code == 1
            mock_register.assert_not_called()
            mock_cleanup.assert_called_once()

    def test_cleanup_on_ami_registration_failure(self, tmp_path):
        """A Pass-2 register failure is isolated; with no successes the run exits 1 and cleans up."""
        mock_args = make_mock_args(output_dir=str(tmp_path))
        mock_ssh = Mock()
        pcr_measurements = make_pcr_measurements()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_run_id'), \
             patch.object(build_ami, 'load_flavors_manifest', return_value=[make_flavor()]), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools'), \
             patch.object(build_ami, 'reset_artifacts_dir'), \
             patch.object(build_ami, 'verify_artifact_signature', return_value=True), \
             patch.object(build_ami, 'pull_artifact_from_ghcr'), \
             patch.object(build_ami, 'validate_artifact_files'), \
             patch.object(build_ami, 'check_debug_annotation'), \
             patch.object(build_ami, 'validate_pcr_measurements', return_value=pcr_measurements), \
             patch.object(build_ami, 'upload_snapshot', return_value="snap-abc"), \
             patch.object(build_ami, 'wait_for_snapshot'), \
             patch.object(build_ami, 'register_ami', side_effect=RuntimeError("AMI registration failed")), \
             patch.object(build_ami, 'cleanup_script_resources') as mock_cleanup, \
             patch.object(build_ami, 'boto3'):

            exit_code = build_ami.main()

            assert exit_code == 1
            mock_cleanup.assert_called_once()
