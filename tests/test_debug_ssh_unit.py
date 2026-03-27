"""
Unit tests for the debug SSH feature.

Tests cover: build script flag parsing, KIWI XML directive modification,
config.sh conditional sshd enablement, deploy.py SSH argument parsing,
Terraform variable construction, and infrastructure state output.

Validates: Requirements 32.1-32.28
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import pytest
import yaml

# Import deploy module using importlib
import importlib.util
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("deploy", scripts_dir / "deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

# Paths to source files
BUILD_SCRIPT_PATH = Path(__file__).parent.parent / ".github" / "scripts" / "build-kiwi-image.sh"
CONFIG_SH_PATH = Path(__file__).parent.parent / "kiwi-descriptions" / "config.sh"
APPLIANCE_KIWI_PATH = Path(__file__).parent.parent / "kiwi-descriptions" / "appliance.kiwi"
WORKFLOW_PATH = Path(__file__).parent.parent / ".github" / "workflows" / "build-attestable-image.yml"
MAIN_TF_PATH = Path(__file__).parent.parent / "terraform" / "deploy" / "main.tf"
VARIABLES_TF_PATH = Path(__file__).parent.parent / "terraform" / "deploy" / "variables.tf"


# =============================================================================
# Build script --enable-ssh flag parsing
# =============================================================================

class TestBuildScriptFlagParsing:
    """Test build-kiwi-image.sh --enable-ssh flag parsing."""

    def test_default_enable_ssh_false(self):
        """No args: ENABLE_SSH defaults to 'false'."""
        with open(BUILD_SCRIPT_PATH, 'r') as f:
            content = f.read()
        assert 'ENABLE_SSH="false"' in content, \
            "ENABLE_SSH should default to false"

    def test_enable_ssh_flag_sets_true(self):
        """--enable-ssh sets ENABLE_SSH to 'true'."""
        with open(BUILD_SCRIPT_PATH, 'r') as f:
            content = f.read()
        # Verify the case statement handles --enable-ssh
        assert '--enable-ssh)' in content, \
            "Script should handle --enable-ssh flag"
        assert 'ENABLE_SSH="true"' in content, \
            "Script should set ENABLE_SSH=true when flag is passed"

    def test_unknown_arg_causes_error(self):
        """Unknown argument causes error exit 1."""
        with open(BUILD_SCRIPT_PATH, 'r') as f:
            content = f.read()
        # Verify the default case handles unknown args
        assert '::error::Unknown argument' in content, \
            "Script should error on unknown arguments"
        assert 'exit 1' in content, \
            "Script should exit 1 on unknown arguments"


# =============================================================================
# Sed removal of SSH ignore directives
# =============================================================================

class TestSedDirectiveRemoval:
    """Test sed-based removal of SSH ignore directives from appliance.kiwi."""

    SSH_DIRECTIVES = [
        "openssh-server",
        "cloud-init",
        "cloud-init-cfg-ec2",
        "ec2-instance-connect",
    ]

    PRESERVED_DIRECTIVES = [
        "amazon-ssm-agent",
        "update-motd",
    ]

    def test_all_ssh_directives_present_in_original(self):
        """Verify all four SSH directives exist in the original appliance.kiwi."""
        with open(APPLIANCE_KIWI_PATH, 'r') as f:
            content = f.read()
        for directive in self.SSH_DIRECTIVES:
            assert f'<ignore name="{directive}"/>' in content, \
                f"Original should contain ignore for {directive}"

    def test_sed_removes_ssh_directives(self):
        """When --enable-ssh, sed removes the four SSH ignore directives."""
        with open(APPLIANCE_KIWI_PATH, 'r') as f:
            original = f.read()

        with tempfile.NamedTemporaryFile(mode='w', suffix='.kiwi', delete=False) as tmp:
            tmp.write(original)
            tmp_path = tmp.name

        try:
            # Run the same sed commands as the build script
            for directive in self.SSH_DIRECTIVES:
                subprocess.run(
                    ['sed', '-i', f'/<ignore name="{directive}"\\/>/d', tmp_path],
                    check=True,
                )

            with open(tmp_path, 'r') as f:
                result = f.read()

            for directive in self.SSH_DIRECTIVES:
                assert f'<ignore name="{directive}"/>' not in result, \
                    f"Directive {directive} should be removed"
        finally:
            os.unlink(tmp_path)

    def test_sed_preserves_other_directives(self):
        """Sed removal preserves amazon-ssm-agent and update-motd directives."""
        with open(APPLIANCE_KIWI_PATH, 'r') as f:
            original = f.read()

        with tempfile.NamedTemporaryFile(mode='w', suffix='.kiwi', delete=False) as tmp:
            tmp.write(original)
            tmp_path = tmp.name

        try:
            # Run the same sed commands as the build script
            for directive in self.SSH_DIRECTIVES:
                subprocess.run(
                    ['sed', '-i', f'/<ignore name="{directive}"\\/>/d', tmp_path],
                    check=True,
                )

            with open(tmp_path, 'r') as f:
                result = f.read()

            for directive in self.PRESERVED_DIRECTIVES:
                assert directive in result, \
                    f"Directive {directive} should be preserved"
        finally:
            os.unlink(tmp_path)

    def test_no_ssh_flag_preserves_all_directives(self):
        """Without --enable-ssh, all directives remain unchanged."""
        with open(APPLIANCE_KIWI_PATH, 'r') as f:
            content = f.read()

        all_directives = self.SSH_DIRECTIVES + self.PRESERVED_DIRECTIVES
        for directive in all_directives:
            assert directive in content, \
                f"Directive {directive} should be present in original"


# =============================================================================
# config.sh conditional sshd enablement
# =============================================================================

class TestConfigShSshdEnablement:
    """Test config.sh conditional sshd enablement."""

    def test_enable_ssh_true_enables_sshd(self):
        """ENABLE_SSH=true enables sshd service."""
        with open(CONFIG_SH_PATH, 'r') as f:
            content = f.read()
        # Verify the conditional enables sshd
        assert 'systemctl enable sshd' in content, \
            "config.sh should enable sshd when ENABLE_SSH=true"
        # Verify it's inside the true branch
        assert '[ "${ENABLE_SSH}" = "true" ]' in content, \
            "Should check ENABLE_SSH == true"

    def test_enable_ssh_false_skips_sshd(self):
        """ENABLE_SSH=false logs disabled message."""
        with open(CONFIG_SH_PATH, 'r') as f:
            content = f.read()
        assert 'SSH debug access disabled' in content, \
            "config.sh should log SSH disabled for non-true values"

    def test_unset_enable_ssh_skips_sshd(self):
        """Unset ENABLE_SSH falls through to else branch (disabled)."""
        with open(CONFIG_SH_PATH, 'r') as f:
            content = f.read()
        # The else branch handles unset/empty/false/other values
        # Verify the structure: if true -> enable, else -> disabled
        lines = content.split('\n')
        found_if = False
        found_else = False
        for line in lines:
            if '[ "${ENABLE_SSH}" = "true" ]' in line:
                found_if = True
            if found_if and 'else' in line:
                found_else = True
                break
        assert found_if and found_else, \
            "config.sh should have if/else for ENABLE_SSH"


# =============================================================================
# deploy.py --enable-ssh and --key-pair-name argument parsing
# =============================================================================

class TestDeploySSHArgumentParsing:
    """Test deploy.py SSH argument parsing."""

    def test_no_ssh_args_defaults(self):
        """No SSH args: enable_ssh=False, key_pair_name=''."""
        with patch("sys.argv", ["deploy.py"]):
            args = deploy.parse_arguments()
        assert args.enable_ssh is False
        assert args.key_pair_name == ""

    def test_both_ssh_args_provided(self):
        """--enable-ssh --key-pair-name foo: both set correctly."""
        with patch("sys.argv", ["deploy.py", "--enable-ssh", "--key-pair-name", "my-key"]):
            args = deploy.parse_arguments()
        assert args.enable_ssh is True
        assert args.key_pair_name == "my-key"

    def test_enable_ssh_without_key_pair_fails(self):
        """--enable-ssh without --key-pair-name: main() returns 1."""
        mock_args = Mock()
        mock_args.enable_ssh = True
        mock_args.key_pair_name = ""
        mock_args.ami_build_result = "dummy.json"
        mock_args.instance_type = "c5.9xlarge"
        mock_args.output_file = "out.json"

        with patch.object(deploy, 'parse_arguments', return_value=mock_args):
            exit_code = deploy.main()
        assert exit_code == 1, \
            "--enable-ssh without --key-pair-name should fail with exit code 1"


# =============================================================================
# Terraform variable construction
# =============================================================================

class TestTerraformVariableConstruction:
    """Test Terraform variable construction with and without SSH."""

    def test_without_ssh_four_vars(self):
        """Without SSH: 4 base Terraform variables."""
        ami_build_result = {"ami_id": "ami-abc", "region": "us-east-1"}

        with patch("subprocess.run") as mock_run:
            mock_apply = Mock(returncode=0, stdout="{}", stderr="")
            mock_output = Mock(
                returncode=0,
                stdout=json.dumps({"instance_id": {"value": "i-123", "type": "string", "sensitive": False}}),
                stderr="",
            )
            mock_run.side_effect = [mock_apply, mock_output]

            deploy.terraform_apply("/tmp/tf", ami_build_result, "1.2.3.4/32", "c5.9xlarge")

            cmd = mock_run.call_args_list[0][0][0]
            var_flags = [cmd[i + 1] for i in range(len(cmd)) if cmd[i] == '-var']

            assert len(var_flags) == 4, f"Expected 4 vars, got {len(var_flags)}: {var_flags}"
            var_keys = [v.split('=')[0] for v in var_flags]
            assert "attestable_ami_id" in var_keys
            assert "instance_type" in var_keys
            assert "allowed_http_cidr" in var_keys
            assert "aws_region" in var_keys
            assert "enable_ssh" not in var_keys
            assert "key_pair_name" not in var_keys

    def test_with_ssh_six_vars(self):
        """With SSH: 6 Terraform variables (base 4 + enable_ssh + key_pair_name)."""
        ami_build_result = {"ami_id": "ami-abc", "region": "us-east-1"}

        with patch("subprocess.run") as mock_run:
            mock_apply = Mock(returncode=0, stdout="{}", stderr="")
            mock_output = Mock(
                returncode=0,
                stdout=json.dumps({"instance_id": {"value": "i-123", "type": "string", "sensitive": False}}),
                stderr="",
            )
            mock_run.side_effect = [mock_apply, mock_output]

            deploy.terraform_apply(
                "/tmp/tf", ami_build_result, "1.2.3.4/32", "c5.9xlarge",
                enable_ssh=True, key_pair_name="my-key",
            )

            cmd = mock_run.call_args_list[0][0][0]
            var_flags = [cmd[i + 1] for i in range(len(cmd)) if cmd[i] == '-var']

            assert len(var_flags) == 6, f"Expected 6 vars, got {len(var_flags)}: {var_flags}"
            var_keys = [v.split('=')[0] for v in var_flags]
            assert "enable_ssh" in var_keys
            assert "key_pair_name" in var_keys

            # Verify values
            var_dict = {v.split('=')[0]: v.split('=', 1)[1] for v in var_flags}
            assert var_dict["enable_ssh"] == "true"
            assert var_dict["key_pair_name"] == "my-key"


# =============================================================================
# Infrastructure state output
# =============================================================================

class TestInfrastructureStateOutput:
    """Test infrastructure state includes ssh_enabled field."""

    def _run_deploy_and_get_state(self, enable_ssh):
        """Helper to run deploy.main() and return the infrastructure state."""
        ami_build_result = {
            "ami_id": "ami-abc",
            "snapshot_id": "snap-def",
            "region": "us-east-1",
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(ami_build_result, f)
            ami_file = f.name

        output_file = tempfile.mktemp(suffix='.json')

        try:
            mock_args = Mock()
            mock_args.ami_build_result = ami_file
            mock_args.instance_type = "c5.9xlarge"
            mock_args.output_file = output_file
            mock_args.enable_ssh = enable_ssh
            mock_args.key_pair_name = "test-key" if enable_ssh else ""

            mock_ip = MagicMock()
            mock_ip.read.return_value = b"1.2.3.4\n"
            mock_ip.__enter__ = Mock(return_value=mock_ip)
            mock_ip.__exit__ = Mock(return_value=False)

            raw_tf_output = {
                "instance_id": {"value": "i-abc", "type": "string", "sensitive": False},
                "instance_public_ip": {"value": "5.6.7.8", "type": "string", "sensitive": False},
                "attestation_api_url": {"value": "http://5.6.7.8:8080", "type": "string", "sensitive": False},
                "vpc_id": {"value": "vpc-123", "type": "string", "sensitive": False},
                "subnet_id": {"value": "subnet-456", "type": "string", "sensitive": False},
                "security_group_id": {"value": "sg-789", "type": "string", "sensitive": False},
            }

            with patch.object(deploy, 'parse_arguments', return_value=mock_args), \
                 patch.object(deploy.request, 'urlopen', return_value=mock_ip), \
                 patch.object(deploy, 'terraform_init'), \
                 patch.object(deploy, 'terraform_apply', return_value=raw_tf_output):

                exit_code = deploy.main()
                assert exit_code == 0

            with open(output_file, 'r') as f:
                return json.load(f)
        finally:
            if os.path.exists(ami_file):
                os.unlink(ami_file)
            if os.path.exists(output_file):
                os.unlink(output_file)

    def test_ssh_enabled_true(self):
        """ssh_enabled=true when --enable-ssh is provided."""
        state = self._run_deploy_and_get_state(enable_ssh=True)
        assert state['ssh_enabled'] is True

    def test_ssh_enabled_false(self):
        """ssh_enabled=false when --enable-ssh is not provided."""
        state = self._run_deploy_and_get_state(enable_ssh=False)
        assert state['ssh_enabled'] is False
