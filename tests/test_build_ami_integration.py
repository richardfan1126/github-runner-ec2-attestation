"""
Integration tests for the complete AMI build flow.

Tests the end-to-end build process with mocked external services
(AWS, SSH, Terraform, ORAS, GitHub CLI).

Requirements: 11-20
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, call

import pytest

# Import build_ami module using importlib
import importlib.util
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)


def make_mock_args(output_file="test_output.json"):
    """Create mock command-line arguments."""
    args = Mock()
    args.artifact_ref = "ghcr.io/owner/repo:v1.0"
    args.region = "us-east-1"
    args.instance_type = "c5.9xlarge"
    args.output_file = output_file
    return args


def make_pcr_measurements():
    """Create valid PCR measurements dict."""
    return {
        "Measurements": {
            "PCR4": "abcdef1234567890" * 3,
            "PCR7": "1234567890abcdef" * 3,
        }
    }


class TestCompleteBuildFlow:
    """Test complete build flow with mocked external services."""

    def test_successful_build_flow(self, tmp_path):
        """Test a fully successful build produces correct output and cleans up."""
        output_file = str(tmp_path / "result.json")
        mock_args = make_mock_args(output_file=output_file)
        mock_ssh = Mock()
        pcr_measurements = make_pcr_measurements()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_artifact_reference'), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_output_file_path'), \
             patch.object(build_ami, 'get_user_public_ip', return_value="1.2.3.4"), \
             patch.object(build_ami, 'provision_ami_build_instance', return_value=("i-abc123", "54.1.2.3", "KEY")), \
             patch.object(build_ami, 'save_ssh_private_key', return_value="/tmp/key.pem"), \
             patch.object(build_ami, 'wait_for_instance_ready'), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools'), \
             patch.object(build_ami, 'verify_artifact_signature', return_value=True), \
             patch.object(build_ami, 'pull_artifact_from_ghcr'), \
             patch.object(build_ami, 'validate_artifact_files'), \
             patch.object(build_ami, 'check_debug_annotation'), \
             patch.object(build_ami, 'validate_pcr_measurements', return_value=pcr_measurements), \
             patch.object(build_ami, 'upload_snapshot', return_value="snap-abc123"), \
             patch.object(build_ami, 'wait_for_snapshot'), \
             patch.object(build_ami, 'register_ami', return_value="ami-xyz789"), \
             patch.object(build_ami, 'cleanup_infrastructure') as mock_cleanup, \
             patch('boto3.client'):

            exit_code = build_ami.main()

            assert exit_code == 0

            # Verify output file was written
            assert os.path.exists(output_file)
            with open(output_file) as f:
                result = json.load(f)

            assert result["ami_id"] == "ami-xyz789"
            assert result["snapshot_id"] == "snap-abc123"
            assert result["region"] == "us-east-1"
            assert "build_timestamp" in result
            assert result["pcr_measurements"]["pcr4"] == pcr_measurements["Measurements"]["PCR4"]
            assert result["pcr_measurements"]["pcr7"] == pcr_measurements["Measurements"]["PCR7"]

            # Cleanup should always be called
            mock_cleanup.assert_called_once()


class TestSignatureVerificationFailure:
    """Test signature verification failure handling."""

    def test_signature_failure_returns_exit_code_1(self):
        """Build should fail with exit code 1 when signature verification fails."""
        mock_args = make_mock_args()
        mock_ssh = Mock()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_artifact_reference'), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_output_file_path'), \
             patch.object(build_ami, 'get_user_public_ip', return_value="1.2.3.4"), \
             patch.object(build_ami, 'provision_ami_build_instance', return_value=("i-abc", "1.2.3.4", "KEY")), \
             patch.object(build_ami, 'save_ssh_private_key', return_value="/tmp/key.pem"), \
             patch.object(build_ami, 'wait_for_instance_ready'), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools'), \
             patch.object(build_ami, 'verify_artifact_signature', return_value=False), \
             patch.object(build_ami, 'cleanup_infrastructure') as mock_cleanup, \
             patch('boto3.client'):

            exit_code = build_ami.main()

            assert exit_code == 1
            mock_cleanup.assert_called_once()

    def test_signature_failure_does_not_create_ami(self):
        """When signature fails, register_ami should never be called."""
        mock_args = make_mock_args()
        mock_ssh = Mock()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_artifact_reference'), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_output_file_path'), \
             patch.object(build_ami, 'get_user_public_ip', return_value="1.2.3.4"), \
             patch.object(build_ami, 'provision_ami_build_instance', return_value=("i-abc", "1.2.3.4", "KEY")), \
             patch.object(build_ami, 'save_ssh_private_key', return_value="/tmp/key.pem"), \
             patch.object(build_ami, 'wait_for_instance_ready'), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools'), \
             patch.object(build_ami, 'verify_artifact_signature', return_value=False), \
             patch.object(build_ami, 'register_ami') as mock_register, \
             patch.object(build_ami, 'cleanup_infrastructure'), \
             patch('boto3.client'):

            build_ami.main()
            mock_register.assert_not_called()


class TestToolInstallationFailures:
    """Test tool installation failure handling."""

    def test_tool_install_failure_triggers_cleanup(self):
        """If tool installation fails, cleanup should still run."""
        mock_args = make_mock_args()
        mock_ssh = Mock()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_artifact_reference'), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_output_file_path'), \
             patch.object(build_ami, 'get_user_public_ip', return_value="1.2.3.4"), \
             patch.object(build_ami, 'provision_ami_build_instance', return_value=("i-abc", "1.2.3.4", "KEY")), \
             patch.object(build_ami, 'save_ssh_private_key', return_value="/tmp/key.pem"), \
             patch.object(build_ami, 'wait_for_instance_ready'), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools', side_effect=RuntimeError("coldsnap install failed")), \
             patch.object(build_ami, 'cleanup_infrastructure') as mock_cleanup, \
             patch('boto3.client'):

            exit_code = build_ami.main()

            assert exit_code == 1
            mock_cleanup.assert_called_once()

    def test_tool_install_failure_returns_exit_code_1(self):
        """Tool installation failure should return exit code 1."""
        mock_args = make_mock_args()
        mock_ssh = Mock()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_artifact_reference'), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_output_file_path'), \
             patch.object(build_ami, 'get_user_public_ip', return_value="1.2.3.4"), \
             patch.object(build_ami, 'provision_ami_build_instance', return_value=("i-abc", "1.2.3.4", "KEY")), \
             patch.object(build_ami, 'save_ssh_private_key', return_value="/tmp/key.pem"), \
             patch.object(build_ami, 'wait_for_instance_ready'), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools', side_effect=RuntimeError("Rust install failed")), \
             patch.object(build_ami, 'cleanup_infrastructure'), \
             patch('boto3.client'):

            exit_code = build_ami.main()
            assert exit_code == 1


class TestCleanupOnVariousFailures:
    """Test cleanup on various failure scenarios."""

    def test_cleanup_on_provisioning_failure(self):
        """Cleanup runs even when provisioning fails."""
        mock_args = make_mock_args()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_artifact_reference'), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_output_file_path'), \
             patch.object(build_ami, 'get_user_public_ip', return_value="1.2.3.4"), \
             patch.object(build_ami, 'provision_ami_build_instance', side_effect=RuntimeError("Terraform apply failed")), \
             patch.object(build_ami, 'cleanup_infrastructure') as mock_cleanup, \
             patch('boto3.client'):

            exit_code = build_ami.main()

            assert exit_code == 1
            mock_cleanup.assert_called_once()

    def test_cleanup_on_snapshot_upload_failure(self):
        """Cleanup runs when snapshot upload fails."""
        mock_args = make_mock_args()
        mock_ssh = Mock()
        pcr_measurements = make_pcr_measurements()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_artifact_reference'), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_output_file_path'), \
             patch.object(build_ami, 'get_user_public_ip', return_value="1.2.3.4"), \
             patch.object(build_ami, 'provision_ami_build_instance', return_value=("i-abc", "1.2.3.4", "KEY")), \
             patch.object(build_ami, 'save_ssh_private_key', return_value="/tmp/key.pem"), \
             patch.object(build_ami, 'wait_for_instance_ready'), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools'), \
             patch.object(build_ami, 'verify_artifact_signature', return_value=True), \
             patch.object(build_ami, 'pull_artifact_from_ghcr'), \
             patch.object(build_ami, 'validate_artifact_files'), \
             patch.object(build_ami, 'check_debug_annotation'), \
             patch.object(build_ami, 'validate_pcr_measurements', return_value=pcr_measurements), \
             patch.object(build_ami, 'upload_snapshot', side_effect=RuntimeError("coldsnap upload failed")), \
             patch.object(build_ami, 'cleanup_infrastructure') as mock_cleanup, \
             patch('boto3.client'):

            exit_code = build_ami.main()

            assert exit_code == 1
            mock_cleanup.assert_called_once()

    def test_cleanup_on_ami_registration_failure(self):
        """Cleanup runs when AMI registration fails."""
        mock_args = make_mock_args()
        mock_ssh = Mock()
        pcr_measurements = make_pcr_measurements()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_artifact_reference'), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_output_file_path'), \
             patch.object(build_ami, 'get_user_public_ip', return_value="1.2.3.4"), \
             patch.object(build_ami, 'provision_ami_build_instance', return_value=("i-abc", "1.2.3.4", "KEY")), \
             patch.object(build_ami, 'save_ssh_private_key', return_value="/tmp/key.pem"), \
             patch.object(build_ami, 'wait_for_instance_ready'), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools'), \
             patch.object(build_ami, 'verify_artifact_signature', return_value=True), \
             patch.object(build_ami, 'pull_artifact_from_ghcr'), \
             patch.object(build_ami, 'validate_artifact_files'), \
             patch.object(build_ami, 'check_debug_annotation'), \
             patch.object(build_ami, 'validate_pcr_measurements', return_value=pcr_measurements), \
             patch.object(build_ami, 'upload_snapshot', return_value="snap-abc"), \
             patch.object(build_ami, 'wait_for_snapshot'), \
             patch.object(build_ami, 'register_ami', side_effect=RuntimeError("AMI registration failed")), \
             patch.object(build_ami, 'cleanup_infrastructure') as mock_cleanup, \
             patch('boto3.client'):

            exit_code = build_ami.main()

            assert exit_code == 1
            mock_cleanup.assert_called_once()

    def test_cleanup_passes_ssh_client(self):
        """Cleanup should receive the ssh_client for closing."""
        mock_args = make_mock_args()
        mock_ssh = Mock()

        with patch.object(build_ami, 'parse_arguments', return_value=mock_args), \
             patch.object(build_ami, 'validate_artifact_reference'), \
             patch.object(build_ami, 'validate_aws_region'), \
             patch.object(build_ami, 'validate_output_file_path'), \
             patch.object(build_ami, 'get_user_public_ip', return_value="1.2.3.4"), \
             patch.object(build_ami, 'provision_ami_build_instance', return_value=("i-abc", "1.2.3.4", "KEY")), \
             patch.object(build_ami, 'save_ssh_private_key', return_value="/tmp/key.pem"), \
             patch.object(build_ami, 'wait_for_instance_ready'), \
             patch.object(build_ami, 'verify_ssh_connectivity', return_value=mock_ssh), \
             patch.object(build_ami, 'install_all_tools', side_effect=RuntimeError("fail")), \
             patch.object(build_ami, 'cleanup_infrastructure') as mock_cleanup, \
             patch('boto3.client'):

            build_ami.main()

            call_kwargs = mock_cleanup.call_args[1]
            assert call_kwargs['ssh_client'] is mock_ssh
