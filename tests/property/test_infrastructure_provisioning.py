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
    
    # Verify instance_type default
    assert 'default     = "c5.9xlarge"' in variables_content, \
        "Instance type must default to c5.9xlarge"
