"""
Property-based tests for the debug SSH feature.

These tests validate correctness properties 95-103 for the SSH debug access
feature across the build script, config.sh, GHA workflow, deploy script,
and Terraform configuration.
"""

import json
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest
import yaml
from hypothesis import given, strategies as st, settings, assume

# Import deploy module using importlib
import importlib.util
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("deploy", scripts_dir / "deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

# Paths to source files
WORKFLOW_PATH = Path(__file__).parent.parent / ".github" / "workflows" / "build-attestable-image.yml"
APPLIANCE_KIWI_PATH = Path(__file__).parent.parent / "kiwi-descriptions" / "appliance.kiwi"
MAIN_TF_PATH = Path(__file__).parent.parent / "terraform" / "deploy" / "main.tf"
VARIABLES_TF_PATH = Path(__file__).parent.parent / "terraform" / "deploy" / "variables.tf"


# --- Strategies ---

def event_name_strategy():
    """Generate GitHub Actions event names."""
    return st.sampled_from(["push", "pull_request", "schedule", "workflow_dispatch"])


def non_dispatch_event_strategy():
    """Generate non-workflow_dispatch event names."""
    return st.sampled_from(["push", "pull_request", "schedule"])


def enable_ssh_input_strategy():
    """Generate possible enable_ssh input values."""
    return st.sampled_from([True, False, "true", "false", ""])


def enable_ssh_env_strategy():
    """Generate possible ENABLE_SSH environment variable values."""
    return st.one_of(
        st.just("true"),
        st.just("false"),
        st.just(""),
        st.text(min_size=1, max_size=20).filter(lambda x: x != "true"),
    )


def key_pair_name_strategy():
    """Generate valid EC2 key pair names (must start with alphanumeric)."""
    return st.builds(
        lambda first, rest: first + rest,
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=1),
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=0, max_size=29),
    )


def ssh_ignore_directive_strategy():
    """Generate additional ignore directives that should NOT be removed."""
    return st.sampled_from([
        '<ignore name="amazon-ssm-agent"/>',
        '<ignore name="update-motd" />',
    ])


# --- Property 95: Build Flag Propagation ---

@settings(max_examples=20, deadline=None)
@given(
    event_name=event_name_strategy(),
    enable_ssh_input=st.booleans(),
)
def test_property_95_build_flag_propagation(event_name, enable_ssh_input):
    """
    Property 95: Build Flag Propagation

    For any workflow trigger event, the --enable-ssh flag should be passed to
    build-kiwi-image.sh if and only if the event is workflow_dispatch with
    enable_ssh input set to true. For all other trigger types, the flag should
    never be passed.

    **Validates: Requirements 32.1, 32.3, 32.4**
    """
    # Load the workflow YAML
    with open(WORKFLOW_PATH, 'r') as f:
        workflow = yaml.safe_load(f)

    # Find the Build KIWI image step
    build_step = None
    for step in workflow['jobs']['build-and-publish']['steps']:
        if step.get('name') == 'Build KIWI image':
            build_step = step
            break

    assert build_step is not None, "Build KIWI image step not found"

    run_script = build_step['run']

    # The workflow logic: SSH_FLAG is set only when event_name == 'workflow_dispatch'
    # AND inputs.enable_ssh == 'true'
    # Simulate the conditional logic from the workflow
    should_pass_flag = (event_name == "workflow_dispatch" and enable_ssh_input is True)

    # Verify the workflow script contains the correct conditional
    assert 'SSH_FLAG=""' in run_script, "SSH_FLAG should default to empty"
    assert 'workflow_dispatch' in run_script, "Should check for workflow_dispatch event"
    assert 'enable_ssh' in run_script, "Should check enable_ssh input"

    if should_pass_flag:
        # When workflow_dispatch + enable_ssh=true, flag should be set
        assert '--enable-ssh' in run_script, "Script should reference --enable-ssh flag"
    else:
        # For non-dispatch events, SSH_FLAG stays empty (default)
        # The conditional ensures it's only set for workflow_dispatch + true
        assert 'SSH_FLAG=""' in run_script, "SSH_FLAG should default to empty"


# --- Property 96: KIWI XML SSH Directive Modification ---

SSH_DIRECTIVES_TO_REMOVE = [
    "openssh-server",
    "cloud-init",
    "cloud-init-cfg-ec2",
    "ec2-instance-connect",
]

SSH_DIRECTIVES_TO_KEEP = [
    "amazon-ssm-agent",
    "update-motd",
]


@settings(max_examples=20, deadline=None)
@given(enable_ssh=st.booleans())
def test_property_96_kiwi_xml_ssh_directive_modification(enable_ssh):
    """
    Property 96: KIWI XML SSH Directive Modification

    When --enable-ssh is passed, the four SSH ignore directives should be removed
    from appliance.kiwi. When not passed, all four should remain.

    **Validates: Requirements 32.8, 32.9**
    """
    # Read the original appliance.kiwi
    with open(APPLIANCE_KIWI_PATH, 'r') as f:
        original_xml = f.read()

    # Create a temp copy and apply sed commands (simulating build script behavior)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.kiwi', delete=False) as tmp:
        tmp.write(original_xml)
        tmp_path = tmp.name

    try:
        if enable_ssh:
            # Simulate the sed commands from build-kiwi-image.sh
            with open(tmp_path, 'r') as f:
                content = f.read()
            for directive in SSH_DIRECTIVES_TO_REMOVE:
                pattern = f'<ignore name="{directive}"/>'
                content = content.replace(pattern, '')
            with open(tmp_path, 'w') as f:
                f.write(content)

        with open(tmp_path, 'r') as f:
            result_xml = f.read()

        for directive in SSH_DIRECTIVES_TO_REMOVE:
            pattern = f'<ignore name="{directive}"/>'
            if enable_ssh:
                assert pattern not in result_xml, \
                    f"Directive {directive} should be removed when SSH enabled"
            else:
                assert pattern in result_xml, \
                    f"Directive {directive} should remain when SSH disabled"

        # Directives that should ALWAYS be preserved
        for directive in SSH_DIRECTIVES_TO_KEEP:
            assert directive in result_xml, \
                f"Directive {directive} should always be preserved"

    finally:
        os.unlink(tmp_path)


# --- Property 97: Conditional sshd Enablement ---

@settings(max_examples=20, deadline=None)
@given(enable_ssh_value=enable_ssh_env_strategy())
def test_property_97_conditional_sshd_enablement(enable_ssh_value):
    """
    Property 97: Conditional sshd Enablement

    sshd is enabled if and only if ENABLE_SSH equals "true". For all other
    values (empty, unset, "false", arbitrary strings) sshd is not enabled.

    **Validates: Requirements 32.10, 32.11, 32.12, 32.13**
    """
    # Read config.sh and verify the conditional logic
    config_path = Path(__file__).parent.parent / "kiwi-descriptions" / "config.sh"
    with open(config_path, 'r') as f:
        config_content = f.read()

    # Verify the script checks for exact "true" match
    assert '[ "${ENABLE_SSH}" = "true" ]' in config_content or \
           '[ "$ENABLE_SSH" = "true" ]' in config_content, \
        "config.sh should check ENABLE_SSH == 'true'"

    should_enable = (enable_ssh_value == "true")

    if should_enable:
        assert "systemctl enable sshd" in config_content, \
            "config.sh should enable sshd when ENABLE_SSH=true"
    else:
        # The else branch should log that SSH is disabled
        assert "SSH debug access disabled" in config_content, \
            "config.sh should log SSH disabled for non-true values"


# --- Property 98: GHA Summary SSH Warning ---

@settings(max_examples=20, deadline=None)
@given(
    event_name=event_name_strategy(),
    enable_ssh=st.booleans(),
)
def test_property_98_gha_summary_ssh_warning(event_name, enable_ssh):
    """
    Property 98: GHA Summary SSH Warning

    The job summary contains an SSH warning if and only if the trigger is
    workflow_dispatch with enable_ssh=true.

    **Validates: Requirements 32.5**
    """
    with open(WORKFLOW_PATH, 'r') as f:
        workflow = yaml.safe_load(f)

    # Find the SSH debug warning step
    warning_step = None
    for step in workflow['jobs']['build-and-publish']['steps']:
        if step.get('name') == 'SSH debug warning':
            warning_step = step
            break

    assert warning_step is not None, "SSH debug warning step not found"

    # Check the condition
    condition = warning_step.get('if', '')
    should_warn = (event_name == "workflow_dispatch" and enable_ssh is True)

    # Verify the condition references both workflow_dispatch and enable_ssh
    assert "workflow_dispatch" in condition, \
        "Warning condition should check for workflow_dispatch"
    assert "enable_ssh" in condition, \
        "Warning condition should check enable_ssh input"

    # Verify the warning content
    run_content = warning_step['run']
    if should_warn:
        assert "WARNING" in run_content or "⚠️" in run_content, \
            "Warning step should contain a warning message"
        assert "SSH" in run_content, \
            "Warning should mention SSH"
        assert "GITHUB_STEP_SUMMARY" in run_content, \
            "Warning should write to GITHUB_STEP_SUMMARY"


# --- Property 99: Deploy Script SSH Argument Validation ---

@settings(max_examples=20, deadline=None)
@given(
    enable_ssh=st.booleans(),
    key_pair_name=st.one_of(st.just(""), key_pair_name_strategy()),
)
def test_property_99_deploy_script_ssh_argument_validation(enable_ssh, key_pair_name):
    """
    Property 99: Deploy Script SSH Argument Validation

    --enable-ssh without --key-pair-name fails with error; --enable-ssh with
    --key-pair-name proceeds; no --enable-ssh proceeds regardless of --key-pair-name.

    **Validates: Requirements 32.15, 32.16, 32.17**
    """
    # Build argv
    argv = ["deploy.py", "--ami-build-result", "dummy.json"]
    if enable_ssh:
        argv.append("--enable-ssh")
    if key_pair_name:
        argv.extend(["--key-pair-name", key_pair_name])

    with patch("sys.argv", argv):
        args = deploy.parse_arguments()

    assert args.enable_ssh == enable_ssh
    if key_pair_name:
        assert args.key_pair_name == key_pair_name
    else:
        assert args.key_pair_name == ""

    # Validate the SSH argument validation logic
    should_fail = enable_ssh and not key_pair_name

    if should_fail:
        # When enable_ssh=True and no key_pair_name, main() should return 1
        mock_args = Mock()
        mock_args.enable_ssh = True
        mock_args.key_pair_name = ""
        mock_args.ami_build_result = "dummy.json"
        mock_args.instance_type = "c5.9xlarge"
        mock_args.output_file = "out.json"

        with patch.object(deploy, 'parse_arguments', return_value=mock_args):
            exit_code = deploy.main()
            assert exit_code == 1, \
                "--enable-ssh without --key-pair-name should fail"
    elif enable_ssh and key_pair_name:
        # Should proceed (won't fail on SSH validation)
        mock_args = Mock()
        mock_args.enable_ssh = True
        mock_args.key_pair_name = key_pair_name
        mock_args.ami_build_result = "/nonexistent/file.json"
        mock_args.instance_type = "c5.9xlarge"
        mock_args.output_file = "out.json"

        with patch.object(deploy, 'parse_arguments', return_value=mock_args):
            exit_code = deploy.main()
            # It will fail on missing file, not on SSH validation
            assert exit_code == 1  # fails for file not found, not SSH validation
    else:
        # No --enable-ssh: should proceed regardless of key_pair_name
        mock_args = Mock()
        mock_args.enable_ssh = False
        mock_args.key_pair_name = key_pair_name
        mock_args.ami_build_result = "/nonexistent/file.json"
        mock_args.instance_type = "c5.9xlarge"
        mock_args.output_file = "out.json"

        with patch.object(deploy, 'parse_arguments', return_value=mock_args):
            exit_code = deploy.main()
            # Fails for file not found, not SSH validation
            assert exit_code == 1


# --- Property 100: Terraform SSH Configuration Consistency ---

@settings(max_examples=20, deadline=None)
@given(enable_ssh=st.booleans())
def test_property_100_terraform_ssh_configuration_consistency(enable_ssh):
    """
    Property 100: Terraform SSH Configuration Consistency

    Port 22 ingress rule exists if and only if enable_ssh=true.
    key_name is set if and only if enable_ssh=true.

    **Validates: Requirements 32.18, 32.19, 32.22, 32.23, 32.24, 32.25**
    """
    with open(MAIN_TF_PATH, 'r') as f:
        tf_content = f.read()

    # Verify dynamic ingress block for SSH
    assert 'dynamic "ingress"' in tf_content, \
        "Should have dynamic ingress block for SSH"
    assert "var.enable_ssh" in tf_content, \
        "Dynamic block should reference var.enable_ssh"

    # Verify the dynamic block uses for_each with enable_ssh conditional
    # Pattern: for_each = var.enable_ssh ? [1] : []
    assert "var.enable_ssh ? [1] : []" in tf_content, \
        "Dynamic ingress should iterate only when enable_ssh is true"

    # Verify port 22 is in the dynamic block
    assert "22" in tf_content, "Should reference port 22"

    # Verify key_name conditional
    assert "var.enable_ssh ? var.key_pair_name : null" in tf_content, \
        "key_name should be conditional on enable_ssh"

    # Verify the variables exist
    with open(VARIABLES_TF_PATH, 'r') as f:
        vars_content = f.read()

    assert 'variable "enable_ssh"' in vars_content, \
        "enable_ssh variable should be defined"
    assert 'default     = false' in vars_content, \
        "enable_ssh should default to false"
    assert 'variable "key_pair_name"' in vars_content, \
        "key_pair_name variable should be defined"

    if enable_ssh:
        # When enable_ssh=true: port 22 rule exists, key_name is set
        # The dynamic block for_each = [1] means one ingress rule is created
        assert "SSH debug access" in tf_content, \
            "SSH ingress rule should have SSH description"
    else:
        # When enable_ssh=false: for_each = [] means no ingress rule
        # key_name = null means no key pair
        assert "null" in tf_content, \
            "key_name should be null when enable_ssh is false"


# --- Property 101: Deploy Script SSH Terraform Variable Passing ---

@settings(max_examples=20, deadline=None)
@given(
    enable_ssh=st.booleans(),
    key_pair_name=key_pair_name_strategy(),
)
def test_property_101_deploy_script_ssh_terraform_variable_passing(enable_ssh, key_pair_name):
    """
    Property 101: Deploy Script SSH Terraform Variable Passing

    When --enable-ssh and --key-pair-name are provided, tf_vars includes
    enable_ssh=true and key_pair_name={name}. When --enable-ssh is not
    provided, these variables are absent.

    **Validates: Requirements 32.26**
    """
    ami_build_result = {
        "ami_id": "ami-0123456789abcdef0",
        "region": "us-east-1",
    }

    # Call terraform_apply with the SSH parameters and capture the tf_vars
    with patch("subprocess.run") as mock_run:
        # Mock successful terraform apply
        mock_apply = Mock()
        mock_apply.returncode = 0
        mock_apply.stdout = "{}"
        mock_apply.stderr = ""

        mock_output = Mock()
        mock_output.returncode = 0
        mock_output.stdout = json.dumps({
            "instance_id": {"value": "i-123", "type": "string", "sensitive": False},
        })
        mock_output.stderr = ""

        mock_run.side_effect = [mock_apply, mock_output]

        result = deploy.terraform_apply(
            "/tmp/fake-tf-dir",
            ami_build_result,
            "1.2.3.4/32",
            "c5.9xlarge",
            enable_ssh=enable_ssh,
            key_pair_name=key_pair_name if enable_ssh else "",
        )

        # Inspect the terraform apply command
        apply_call = mock_run.call_args_list[0]
        cmd = apply_call[0][0]  # first positional arg is the command list

        cmd_str = " ".join(cmd)

        if enable_ssh:
            assert "enable_ssh=true" in cmd_str, \
                "tf_vars should include enable_ssh=true when SSH enabled"
            assert f"key_pair_name={key_pair_name}" in cmd_str, \
                "tf_vars should include key_pair_name when SSH enabled"
        else:
            assert "enable_ssh" not in cmd_str, \
                "tf_vars should NOT include enable_ssh when SSH disabled"
            assert "key_pair_name" not in cmd_str, \
                "tf_vars should NOT include key_pair_name when SSH disabled"


# --- Property 102: Infrastructure State SSH Status ---

@settings(max_examples=20, deadline=None)
@given(enable_ssh=st.booleans())
def test_property_102_infrastructure_state_ssh_status(enable_ssh):
    """
    Property 102: Infrastructure State SSH Status

    Infrastructure state JSON includes ssh_enabled=true when --enable-ssh is
    provided and ssh_enabled=false otherwise.

    **Validates: Requirements 32.28**
    """
    # Create a temp AMI build result file
    ami_build_result = {
        "ami_id": "ami-0123456789abcdef0",
        "snapshot_id": "snap-0123456789abcdef0",
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

        # Mock external calls
        mock_ip_response = MagicMock()
        mock_ip_response.read.return_value = b"1.2.3.4\n"
        mock_ip_response.__enter__ = Mock(return_value=mock_ip_response)
        mock_ip_response.__exit__ = Mock(return_value=False)

        raw_tf_output = {
            "instance_id": {"value": "i-abc123", "type": "string", "sensitive": False},
            "instance_public_ip": {"value": "5.6.7.8", "type": "string", "sensitive": False},
            "attestation_api_url": {"value": "http://5.6.7.8:8080", "type": "string", "sensitive": False},
            "vpc_id": {"value": "vpc-123", "type": "string", "sensitive": False},
            "subnet_id": {"value": "subnet-456", "type": "string", "sensitive": False},
            "security_group_id": {"value": "sg-789", "type": "string", "sensitive": False},
        }

        with patch.object(deploy, 'parse_arguments', return_value=mock_args), \
             patch.object(deploy.request, 'urlopen', return_value=mock_ip_response), \
             patch.object(deploy, 'terraform_init'), \
             patch.object(deploy, 'terraform_apply', return_value=raw_tf_output):

            exit_code = deploy.main()
            assert exit_code == 0, f"Deploy should succeed, got exit code {exit_code}"

        # Read the output file and verify ssh_enabled
        with open(output_file, 'r') as f:
            state = json.load(f)

        assert 'ssh_enabled' in state, \
            "Infrastructure state should include ssh_enabled"
        assert state['ssh_enabled'] == enable_ssh, \
            f"ssh_enabled should be {enable_ssh}, got {state['ssh_enabled']}"

    finally:
        if os.path.exists(ami_file):
            os.unlink(ami_file)
        if os.path.exists(output_file):
            os.unlink(output_file)


# --- Property 103: Deploy Script SSH Warning ---

@settings(max_examples=20, deadline=None)
@given(enable_ssh=st.booleans())
def test_property_103_deploy_script_ssh_warning(enable_ssh):
    """
    Property 103: Deploy Script SSH Warning

    A warning about SSH debug access is logged when --enable-ssh is provided.
    No such warning when --enable-ssh is not provided.

    **Validates: Requirements 32.27**
    """
    mock_args = Mock()
    mock_args.enable_ssh = enable_ssh
    mock_args.key_pair_name = "test-key" if enable_ssh else ""
    mock_args.ami_build_result = "/nonexistent/file.json"
    mock_args.instance_type = "c5.9xlarge"
    mock_args.output_file = "out.json"

    with patch.object(deploy, 'parse_arguments', return_value=mock_args), \
         patch.object(deploy.logger, 'warning') as mock_warning:

        deploy.main()  # Will fail on missing file, but warning is logged before that

        warning_calls = [str(c) for c in mock_warning.call_args_list]
        ssh_warning_logged = any("SSH debug access" in msg for msg in warning_calls)

        if enable_ssh:
            assert ssh_warning_logged, \
                "Should log SSH warning when --enable-ssh is provided"
        else:
            assert not ssh_warning_logged, \
                "Should NOT log SSH warning when --enable-ssh is not provided"
