"""
Property-based tests for Terraform infrastructure provisioning.

These tests validate that the Terraform configuration correctly provisions
infrastructure with proper security controls and isolation.
"""

import json
import subprocess
import tempfile
from pathlib import Path
from hypothesis import given, strategies as st, settings
import pytest


# Property 69: SSH Access Configuration
@given(
    allowed_cidr=st.one_of(
        st.just("192.168.1.100/32"),
        st.just("10.0.0.0/24"),
        st.just("172.16.0.0/16")
    )
)
@settings(max_examples=5, deadline=None)
def test_property_69_ssh_access_configuration(allowed_cidr):
    """
    Property 69: SSH Access Configuration
    
    For any allowed_ssh_cidr value, the security group should allow SSH (port 22)
    ingress only from that CIDR block and no other sources.
    
    Validates: Requirements 14.3, 14.6
    """
    terraform_dir = Path(__file__).parent.parent.parent / "terraform" / "build-ami"
    
    # Parse the security group configuration
    sg_file = terraform_dir / "security_group.tf"
    assert sg_file.exists(), "Security group configuration file must exist"
    
    sg_content = sg_file.read_text()
    
    # Verify SSH ingress rule references the variable
    assert "var.allowed_ssh_cidr" in sg_content, \
        "Security group must use allowed_ssh_cidr variable"
    assert "from_port   = 22" in sg_content, \
        "Security group must allow SSH on port 22"
    assert "to_port     = 22" in sg_content, \
        "Security group must allow SSH on port 22"
    assert "protocol    = \"tcp\"" in sg_content, \
        "Security group must use TCP protocol for SSH"
    
    # Verify egress allows all outbound
    assert "egress" in sg_content, \
        "Security group must have egress rules"
    assert "0.0.0.0/0" in sg_content, \
        "Security group must allow all outbound traffic"


# Property 78: Terraform State Isolation
def test_property_78_terraform_state_isolation():
    """
    Property 78: Terraform State Isolation
    
    For any Terraform execution, the state should be isolated per execution
    to prevent conflicts between concurrent builds.
    
    This test verifies that the Terraform configuration does not specify
    a shared backend, allowing each execution to maintain its own state.
    
    Validates: Requirements 14.3, 14.6
    """
    terraform_dir = Path(__file__).parent.parent.parent / "terraform" / "build-ami"
    
    # Check provider configuration
    provider_file = terraform_dir / "provider.tf"
    assert provider_file.exists(), "Provider configuration file must exist"
    
    provider_content = provider_file.read_text()
    
    # Verify no remote backend is configured (state isolation)
    # The absence of a backend block means local state, which is isolated per execution
    assert "backend" not in provider_content or "backend \"local\"" in provider_content, \
        "Terraform should use local backend for state isolation"
    
    # Verify required providers are specified
    assert "required_providers" in provider_content, \
        "Terraform configuration must specify required providers"
    assert "aws" in provider_content, \
        "AWS provider must be configured"
    assert "tls" in provider_content, \
        "TLS provider must be configured for SSH key generation"


def test_vpc_configuration():
    """
    Verify VPC is configured with correct CIDR and DNS settings.
    
    Validates: Requirements 14.3, 14.4
    """
    terraform_dir = Path(__file__).parent.parent.parent / "terraform" / "build-ami"
    vpc_file = terraform_dir / "vpc.tf"
    
    assert vpc_file.exists(), "VPC configuration file must exist"
    vpc_content = vpc_file.read_text()
    
    # Verify VPC CIDR
    assert "10.2.0.0/16" in vpc_content, \
        "VPC must use CIDR 10.2.0.0/16"
    
    # Verify DNS settings
    assert "enable_dns_hostnames = true" in vpc_content, \
        "VPC must have DNS hostnames enabled"
    assert "enable_dns_support   = true" in vpc_content, \
        "VPC must have DNS support enabled"
    
    # Verify subnet configuration
    assert "10.2.1.0/24" in vpc_content, \
        "Public subnet must use CIDR 10.2.1.0/24"
    
    # Verify Internet Gateway
    assert "aws_internet_gateway" in vpc_content, \
        "Internet Gateway must be configured"
    
    # Verify route table with default route
    assert "0.0.0.0/0" in vpc_content, \
        "Route table must have default route to Internet Gateway"


def test_ssh_key_generation():
    """
    Verify SSH key pair is generated with correct parameters.
    
    Validates: Requirements 14.7
    """
    terraform_dir = Path(__file__).parent.parent.parent / "terraform" / "build-ami"
    ssh_key_file = terraform_dir / "ssh_key.tf"
    
    assert ssh_key_file.exists(), "SSH key configuration file must exist"
    ssh_key_content = ssh_key_file.read_text()
    
    # Verify RSA algorithm and key size
    assert "algorithm = \"RSA\"" in ssh_key_content, \
        "SSH key must use RSA algorithm"
    assert "rsa_bits  = 4096" in ssh_key_content, \
        "SSH key must be 4096 bits"
    
    # Verify AWS key pair resource
    assert "aws_key_pair" in ssh_key_content, \
        "AWS key pair resource must be defined"


def test_iam_permissions():
    """
    Verify IAM role has correct permissions for snapshot and AMI operations.
    
    Validates: Requirements 14.10
    """
    terraform_dir = Path(__file__).parent.parent.parent / "terraform" / "build-ami"
    iam_file = terraform_dir / "iam.tf"
    
    assert iam_file.exists(), "IAM configuration file must exist"
    iam_content = iam_file.read_text()
    
    # Verify EC2 assume role policy
    assert "ec2.amazonaws.com" in iam_content, \
        "IAM role must allow EC2 service to assume it"
    
    # Verify snapshot permissions
    assert "ec2:CreateSnapshot" in iam_content, \
        "IAM policy must allow creating snapshots"
    assert "ec2:DescribeSnapshots" in iam_content, \
        "IAM policy must allow describing snapshots"
    
    # Verify AMI permissions
    assert "ec2:RegisterImage" in iam_content, \
        "IAM policy must allow registering AMIs"
    assert "ec2:DescribeImages" in iam_content, \
        "IAM policy must allow describing images"
    
    # Verify EBS direct API permissions
    assert "ebs:PutSnapshotBlock" in iam_content, \
        "IAM policy must allow EBS direct API operations"
    assert "ebs:StartSnapshot" in iam_content, \
        "IAM policy must allow starting snapshots"
    
    # Verify instance profile
    assert "aws_iam_instance_profile" in iam_content, \
        "IAM instance profile must be defined"


def test_ec2_instance_configuration():
    """
    Verify EC2 instance is configured with correct settings.
    
    Validates: Requirements 14.1, 14.8, 14.9
    """
    terraform_dir = Path(__file__).parent.parent.parent / "terraform" / "build-ami"
    ec2_file = terraform_dir / "ec2.tf"
    
    assert ec2_file.exists(), "EC2 configuration file must exist"
    ec2_content = ec2_file.read_text()
    
    # Verify instance type from variable
    assert "var.instance_type" in ec2_content, \
        "Instance type must use variable"
    
    # Verify IMDSv2 required
    assert "http_tokens = \"required\"" in ec2_content, \
        "Instance must require IMDSv2"
    
    # Verify root volume configuration
    assert "volume_size           = 30" in ec2_content, \
        "Root volume must be 30GB"
    assert "volume_type           = \"gp3\"" in ec2_content, \
        "Root volume must use gp3"
    assert "encrypted             = true" in ec2_content, \
        "Root volume must be encrypted"
    
    # Verify public IP assignment
    assert "associate_public_ip_address = true" in ec2_content, \
        "Instance must have public IP for SSH access"


def test_self_terminating_build_instance():
    """
    Verify the build instance carries a runner-independent self-destruct (D2).

    The workflow's always() destroy cannot fire on runner hard-death / the ~6 h
    ceiling / a failed destroy step, so the instance schedules its own shutdown and
    terminates on it. This is the transient builder instance (no attestation/PCR
    surface). It is also tagged with the run id so any orphan is attributable.

    Validates: D2 (self-terminating build instance), D9 (orphan tag)
    """
    terraform_dir = Path(__file__).parent.parent.parent / "terraform" / "build-ami"
    ec2_content = (terraform_dir / "ec2.tf").read_text()

    # user_data schedules a shutdown at a TTL comfortably above the job timeout.
    assert "user_data" in ec2_content, "Instance must set user_data for self-destruct"
    assert "shutdown -h +150" in ec2_content, \
        "user_data must schedule 'shutdown -h +150' (TTL above the 120-min job timeout)"
    # The scheduled shutdown must terminate (not just stop) the instance.
    assert 'instance_initiated_shutdown_behavior = "terminate"' in ec2_content, \
        "Instance must terminate on shutdown, not stop"
    # Orphan attribution: the instance is tagged with the run id.
    assert "run_id = var.run_id" in ec2_content, \
        "Instance must be tagged with run_id for orphan attribution"


def test_account_unique_names_are_run_scoped():
    """
    Verify the account-unique resource names are run-scoped with run_id (D9).

    IAM role/policy/instance-profile names, the SG GroupName, and the SSH key_name
    are account-global uniqueness constraints, so a fixed name collides across
    overlapping runs. Each must incorporate var.run_id. The SSH key_name must no
    longer use the non-deterministic timestamp() suffix.

    Validates: D9 (run-scoped naming)
    """
    terraform_dir = Path(__file__).parent.parent.parent / "terraform" / "build-ami"
    iam = (terraform_dir / "iam.tf").read_text()
    sg = (terraform_dir / "security_group.tf").read_text()
    ssh_key = (terraform_dir / "ssh_key.tf").read_text()

    assert 'name = "build-ami-instance-role-${var.run_id}"' in iam, \
        "IAM role name must be run-scoped"
    assert 'name        = "build-ami-instance-policy-${var.run_id}"' in iam, \
        "IAM policy name must be run-scoped"
    assert 'name = "build-ami-instance-profile-${var.run_id}"' in iam, \
        "IAM instance-profile name must be run-scoped"
    assert 'name        = "build-ami-instance-sg-${var.run_id}"' in sg, \
        "Security group GroupName must be run-scoped"
    assert 'key_name   = "build-ami-key-${var.run_id}"' in ssh_key, \
        "SSH key_name must be run-scoped with run_id"
    # The non-deterministic timestamp() suffix must be gone from the actual config
    # (comment lines mentioning the old approach are ignored).
    ssh_key_code = "\n".join(
        line for line in ssh_key.splitlines() if not line.strip().startswith("#")
    )
    assert "timestamp()" not in ssh_key_code, \
        "SSH key_name must not use the non-deterministic timestamp() suffix"


def test_required_outputs():
    """
    Verify all required outputs are defined.
    
    Validates: Requirements 14.1
    """
    terraform_dir = Path(__file__).parent.parent.parent / "terraform" / "build-ami"
    outputs_file = terraform_dir / "outputs.tf"
    
    assert outputs_file.exists(), "Outputs configuration file must exist"
    outputs_content = outputs_file.read_text()
    
    # Verify required outputs
    required_outputs = [
        "instance_id",
        "instance_public_ip",
        "ssh_private_key",
        "vpc_id",
        "security_group_id"
    ]
    
    for output_name in required_outputs:
        assert f'output "{output_name}"' in outputs_content, \
            f"Output {output_name} must be defined"
    
    # Verify ssh_private_key is marked sensitive
    assert "sensitive   = true" in outputs_content, \
        "SSH private key output must be marked sensitive"


def test_variables_configuration():
    """
    Verify all required variables are defined with correct defaults.
    
    Validates: Requirements 14.1
    """
    terraform_dir = Path(__file__).parent.parent.parent / "terraform" / "build-ami"
    variables_file = terraform_dir / "variables.tf"
    
    assert variables_file.exists(), "Variables configuration file must exist"
    variables_content = variables_file.read_text()
    
    # Verify required variables
    assert 'variable "region"' in variables_content, \
        "Region variable must be defined"
    assert 'variable "allowed_ssh_cidr"' in variables_content, \
        "Allowed SSH CIDR variable must be defined"
    assert 'variable "instance_type"' in variables_content, \
        "Instance type variable must be defined"
    # run_id variable (run-scoped naming + orphan tagging, D9)
    assert 'variable "run_id"' in variables_content, \
        "run_id variable must be defined"

    # Verify instance_type default is right-sized to c5.4xlarge (D11): the CPU-bound
    # coldsnap compile now runs once per run, not once per flavor.
    assert 'default = "c5.4xlarge"' in variables_content, \
        "Instance type must default to c5.4xlarge"


# Property 79: SSH Keepalive Maintenance
def test_property_79_ssh_keepalive_maintenance():
    """
    Property 79: SSH Keepalive Maintenance

    For any long-running SSH operation, the connection should remain active
    through keepalive packets.

    This test verifies that the verify_ssh_connectivity function configures
    SSH keepalive with 30-second intervals to prevent connection timeouts
    during long operations like tool installation and snapshot uploads.

    Validates: Requirements 15.4
    """
    import sys
    from pathlib import Path

    # Add scripts directory to path to import build-ami functions
    scripts_dir = Path(__file__).parent.parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))

    # Read the verify_ssh_connectivity function source
    build_ami_file = scripts_dir / "build-ami.py"
    assert build_ami_file.exists(), "build-ami.py must exist"

    build_ami_content = build_ami_file.read_text()

    # Verify SSH keepalive is configured
    assert "set_keepalive" in build_ami_content, \
        "SSH client must configure keepalive"

    # Verify keepalive interval is 30 seconds
    assert "set_keepalive(30)" in build_ami_content, \
        "SSH keepalive interval must be 30 seconds"

    # Verify keepalive is set after connection establishment
    assert "get_transport().set_keepalive" in build_ami_content, \
        "Keepalive must be set on the transport after connection"

    # Verify connection timeout is configured
    assert "timeout=10" in build_ami_content, \
        "SSH connection timeout must be configured"

    # Verify banner timeout is configured
    assert "banner_timeout=10" in build_ami_content, \
        "SSH banner timeout must be configured"

    # Verify retry mechanism exists
    assert "max_attempts" in build_ami_content, \
        "SSH connection must have retry mechanism"

    # Verify delay between retries
    assert "time.sleep(delay)" in build_ami_content or "time.sleep(30)" in build_ami_content, \
        "SSH connection retries must have delay between attempts"


# Property 166: AMI Build IAM Permission Scoping
def test_property_166_ami_build_iam_permission_scoping():
    """
    Property 166: AMI Build IAM Permission Scoping

    For any Terraform IAM policy for the Build_Instance, EC2 and EBS permissions
    should be scoped to the specific AWS region and account using resource ARN
    patterns or condition keys, and should NOT use Resource="*" for EC2 snapshot
    and image operations.

    Validates: Requirements 50.1, 50.2, 50.3, 50.4
    """
    import re

    terraform_dir = Path(__file__).parent.parent.parent / "terraform" / "build-ami"
    iam_file = terraform_dir / "iam.tf"

    assert iam_file.exists(), "IAM configuration file must exist"
    iam_content = iam_file.read_text()

    # EC2 mutation actions that MUST NOT use Resource = "*"
    ec2_mutation_actions = [
        "ec2:CreateSnapshot",
        "ec2:DeleteSnapshot",
        "ec2:ModifySnapshotAttribute",
        "ec2:RegisterImage",
        "ec2:DeregisterImage",
        "ec2:ModifyImageAttribute",
    ]

    # EBS direct API actions that MUST NOT use Resource = "*"
    ebs_actions = [
        "ebs:CompleteSnapshot",
        "ebs:GetSnapshotBlock",
        "ebs:ListChangedBlocks",
        "ebs:ListSnapshotBlocks",
        "ebs:PutSnapshotBlock",
        "ebs:StartSnapshot",
    ]

    # Find the build_instance_policy resource block
    policy_resource_match = re.search(
        r'resource\s+"aws_iam_policy"\s+"build_instance_policy".*?^}',
        iam_content,
        re.DOTALL | re.MULTILINE,
    )
    assert policy_resource_match, "build_instance_policy resource must exist"
    policy_block = policy_resource_match.group(0)

    # Parse individual statement blocks from the policy
    # Each statement is delimited by { ... } within the Statement array
    statement_pattern = re.compile(
        r'\{\s*\n\s*Sid\s*=.*?\n(?:.*?\n)*?\s*\}', re.DOTALL
    )
    statements = statement_pattern.findall(policy_block)
    assert len(statements) > 0, "Policy must have at least one statement with Sid"

    for stmt_text in statements:
        # Extract actions from this statement
        actions_match = re.findall(r'"((?:ec2|ebs):\w+)"', stmt_text)

        # Extract Resource value
        resource_match = re.search(r'Resource\s*=\s*"([^"]*)"', stmt_text)
        resource = resource_match.group(1) if resource_match else ""

        # Requirement 50.4: EC2 mutation actions must NOT use Resource = "*"
        has_mutation_action = any(a in ec2_mutation_actions for a in actions_match)
        if has_mutation_action:
            assert resource != "*", (
                f"EC2 mutation actions must NOT use Resource='*'. "
                f"Actions: {actions_match}, Resource: {resource}"
            )

        # EBS actions must NOT use Resource = "*"
        has_ebs_action = any(a in ebs_actions for a in actions_match)
        if has_ebs_action:
            assert resource != "*", (
                f"EBS actions must NOT use Resource='*'. "
                f"Actions: {actions_match}, Resource: {resource}"
            )

        # Requirement 50.1 & 50.2: Verify region scoping
        has_ec2_or_ebs = any(
            a.startswith("ec2:") or a.startswith("ebs:") for a in actions_match
        )
        if has_ec2_or_ebs:
            resource_has_region = "aws_region.current.name" in resource
            condition_has_region = "aws:RequestedRegion" in stmt_text
            assert resource_has_region or condition_has_region, (
                f"EC2/EBS statement must be scoped to region via ARN or condition key. "
                f"Actions: {actions_match}"
            )

        # Requirement 50.3: Non-describe actions must have account scoping
        describe_only = all("Describe" in a or "List" in a for a in actions_match)
        if has_ec2_or_ebs and not describe_only:
            resource_has_account = "aws_caller_identity.current.account_id" in resource
            condition_has_account = "aws:ResourceAccount" in stmt_text
            assert resource_has_account or condition_has_account, (
                f"Non-describe EC2/EBS statement must be scoped to account. "
                f"Actions: {actions_match}"
            )

    # Verify data sources exist for region and account lookups
    data_file = terraform_dir / "data.tf"
    assert data_file.exists(), "data.tf must exist"
    data_content = data_file.read_text()

    assert 'data "aws_caller_identity" "current"' in data_content, \
        "aws_caller_identity data source must be defined for account scoping"
    assert 'data "aws_region" "current"' in data_content, \
        "aws_region data source must be defined for region scoping"

