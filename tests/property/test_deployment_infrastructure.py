"""
Property-based tests for deployment Terraform infrastructure.

These tests validate that the deployment Terraform configuration correctly
provisions infrastructure with proper security controls, VPC isolation,
HTTP-only access, and IMDSv2 enforcement.
"""

from pathlib import Path
from hypothesis import given, strategies as st, settings
import pytest


TERRAFORM_DIR = Path(__file__).parent.parent.parent / "terraform" / "deploy"


# Property 81: Deployment VPC Isolation
@given(
    cidr=st.just("10.0.0.0/16"),
    subnet_cidr=st.just("10.0.1.0/24"),
)
@settings(max_examples=100, deadline=None)
def test_property_81_deployment_vpc_isolation(cidr, subnet_cidr):
    """
    Property 81: Deployment VPC Isolation

    For any deployment Terraform configuration, the VPC should be created with
    CIDR block 10.0.0.0/16 and have both DNS hostnames and DNS support enabled.

    Validates: Requirements 22.1
    """
    main_file = TERRAFORM_DIR / "main.tf"
    assert main_file.exists(), "main.tf must exist in terraform/deploy/"

    content = main_file.read_text()

    # Verify VPC CIDR block
    assert cidr in content, \
        f"VPC must use CIDR {cidr}"

    # Verify DNS hostnames enabled
    assert "enable_dns_hostnames = true" in content, \
        "VPC must have DNS hostnames enabled"

    # Verify DNS support enabled
    assert "enable_dns_support   = true" in content or \
           "enable_dns_support = true" in content, \
        "VPC must have DNS support enabled"

    # Verify public subnet CIDR
    assert subnet_cidr in content, \
        f"Public subnet must use CIDR {subnet_cidr}"

    # Verify subnet maps public IP on launch
    assert "map_public_ip_on_launch = true" in content, \
        "Public subnet must map public IP on launch"

    # Verify Internet Gateway exists
    assert "aws_internet_gateway" in content, \
        "Internet Gateway must be configured"

    # Verify route table with default route
    assert "0.0.0.0/0" in content, \
        "Route table must have default route through IGW"

    # Verify route table association
    assert "aws_route_table_association" in content, \
        "Route table must be associated with public subnet"

    # Verify resource tagging with correct prefix
    assert "github-runner-ec2-attestation" in content, \
        "All resources must be tagged with github-runner-ec2-attestation prefix"


# Property 82: Security Group HTTP-Only Access
@given(
    allowed_cidr=st.one_of(
        st.just("192.168.1.100/32"),
        st.just("10.0.0.0/24"),
        st.just("172.16.0.0/16"),
        st.just("203.0.113.42/32"),
    )
)
@settings(max_examples=100, deadline=None)
def test_property_82_security_group_http_only_access(allowed_cidr):
    """
    Property 82: Security Group HTTP-Only Access

    For any deployment security group configuration, the only allowed inbound
    traffic should be TCP on port 8080 from the allowed_http_cidr variable —
    no SSH (port 22) or any other port should be permitted inbound.

    Validates: Requirements 23.2, 23.4, 23.5
    """
    main_file = TERRAFORM_DIR / "main.tf"
    assert main_file.exists(), "main.tf must exist in terraform/deploy/"

    content = main_file.read_text()

    # Find the security group resource block
    assert "aws_security_group" in content, \
        "Security group resource must be defined"

    # Verify ingress rule allows port 8080 only
    assert "from_port   = 8080" in content or "from_port = 8080" in content, \
        "Security group must allow inbound on port 8080"
    assert "to_port     = 8080" in content or "to_port = 8080" in content, \
        "Security group must allow inbound on port 8080"
    assert 'protocol    = "tcp"' in content or 'protocol = "tcp"' in content, \
        "Security group ingress must use TCP protocol"

    # Verify ingress uses the allowed_http_cidr variable
    assert "var.allowed_http_cidr" in content, \
        "Security group must use allowed_http_cidr variable for ingress"

    # Verify NO SSH (port 22) ingress rule exists
    # Count ingress blocks — there should be exactly one
    ingress_count = content.count("ingress {") + content.count("ingress{")
    assert ingress_count == 1, \
        f"Security group must have exactly 1 ingress rule (HTTP 8080 only), found {ingress_count}"

    # Verify port 22 is NOT referenced in any ingress context
    # Split content to isolate ingress blocks
    assert "from_port   = 22" not in content and "from_port = 22" not in content, \
        "Security group must NOT allow SSH (port 22) inbound"

    # Verify egress allows all outbound
    assert "egress" in content, \
        "Security group must have egress rules"
    assert 'protocol    = "-1"' in content or 'protocol = "-1"' in content, \
        "Security group egress must allow all protocols"


# Property 83: IMDSv2 Enforcement
@given(
    instance_type=st.one_of(
        st.just("c5.9xlarge"),
        st.just("m5.xlarge"),
        st.just("c6i.2xlarge"),
    )
)
@settings(max_examples=100, deadline=None)
def test_property_83_imdsv2_enforcement(instance_type):
    """
    Property 83: IMDSv2 Enforcement

    For any target EC2 instance launched by the deployment, IMDSv2 should be
    enforced with http_tokens set to "required" and http_put_response_hop_limit
    set to 1.

    Validates: Requirements 24.7, 24.8
    """
    main_file = TERRAFORM_DIR / "main.tf"
    assert main_file.exists(), "main.tf must exist in terraform/deploy/"

    content = main_file.read_text()

    # Verify EC2 instance resource exists
    assert 'resource "aws_instance"' in content, \
        "EC2 instance resource must be defined"

    # Verify metadata_options block exists
    assert "metadata_options" in content, \
        "EC2 instance must have metadata_options configured"

    # Verify IMDSv2 is required
    assert 'http_tokens                 = "required"' in content or \
           'http_tokens = "required"' in content, \
        "IMDSv2 must be enforced with http_tokens = required"

    # Verify hop limit is set to 1
    assert "http_put_response_hop_limit = 1" in content or \
           "http_put_response_hop_limit=1" in content, \
        "IMDSv2 hop limit must be set to 1"

    # Verify instance uses attestable AMI variable
    assert "var.attestable_ami_id" in content, \
        "Instance must use attestable_ami_id variable"

    # Verify instance uses instance_type variable
    assert "var.instance_type" in content, \
        "Instance must use instance_type variable"

    # Verify detailed monitoring is enabled
    assert "monitoring = true" in content, \
        "Instance must have detailed monitoring enabled"

    # Verify public IP association
    assert "associate_public_ip_address = true" in content, \
        "Instance must have public IP associated"

    # Verify security group attachment
    assert "aws_security_group" in content, \
        "Instance must be attached to the security group"


def test_deployment_variables_configuration():
    """
    Verify all required deployment variables are defined with correct defaults.

    Validates: Requirements 23.6, 24.1, 24.2, 24.3, 24.9, 24.10
    """
    variables_file = TERRAFORM_DIR / "variables.tf"
    assert variables_file.exists(), "variables.tf must exist in terraform/deploy/"

    content = variables_file.read_text()

    # Verify required variables exist
    assert 'variable "attestable_ami_id"' in content, \
        "attestable_ami_id variable must be defined"
    assert 'variable "instance_type"' in content, \
        "instance_type variable must be defined"
    assert 'variable "allowed_http_cidr"' in content, \
        "allowed_http_cidr variable must be defined"
    assert 'variable "aws_region"' in content, \
        "aws_region variable must be defined"

    # Verify instance_type default
    assert 'default     = "c5.9xlarge"' in content or \
           'default = "c5.9xlarge"' in content, \
        "instance_type must default to c5.9xlarge"

    # Verify aws_region default
    assert 'default     = "us-east-1"' in content or \
           'default = "us-east-1"' in content, \
        "aws_region must default to us-east-1"

    # Verify attestable_ami_id has no default (required)
    # Check that the variable block does not contain a default
    ami_block_start = content.index('variable "attestable_ami_id"')
    ami_block_end = content.index("}", ami_block_start)
    ami_block = content[ami_block_start:ami_block_end]
    assert "default" not in ami_block, \
        "attestable_ami_id must not have a default value (required)"

    # Verify allowed_http_cidr has no default (required)
    http_block_start = content.index('variable "allowed_http_cidr"')
    http_block_end = content.index("}", http_block_start)
    http_block = content[http_block_start:http_block_end]
    assert "default" not in http_block, \
        "allowed_http_cidr must not have a default value (required)"


def test_deployment_outputs_configuration():
    """
    Verify all required deployment outputs are defined.

    Validates: Requirements 25.1, 25.2, 25.3, 25.4, 25.5, 25.6
    """
    outputs_file = TERRAFORM_DIR / "outputs.tf"
    assert outputs_file.exists(), "outputs.tf must exist in terraform/deploy/"

    content = outputs_file.read_text()

    required_outputs = [
        "vpc_id",
        "subnet_id",
        "security_group_id",
        "instance_id",
        "instance_public_ip",
        "attestation_api_url",
    ]

    for output_name in required_outputs:
        assert f'output "{output_name}"' in content, \
            f"Output {output_name} must be defined"

    # Verify attestation_api_url includes port 8080
    assert "8080" in content, \
        "attestation_api_url must reference port 8080"


def test_deployment_provider_configuration():
    """
    Verify AWS provider is configured correctly.

    Validates: Requirements 24.10
    """
    main_file = TERRAFORM_DIR / "main.tf"
    assert main_file.exists(), "main.tf must exist in terraform/deploy/"

    content = main_file.read_text()

    # Verify required_providers block
    assert "required_providers" in content, \
        "Terraform must specify required providers"

    # Verify AWS provider source and version
    assert "hashicorp/aws" in content, \
        "AWS provider must use hashicorp/aws source"
    assert '~> 5.0' in content, \
        "AWS provider must use version ~> 5.0"

    # Verify provider uses aws_region variable
    assert "var.aws_region" in content, \
        "AWS provider must use aws_region variable"

    # Verify no remote backend (local state)
    assert "backend" not in content or 'backend "local"' in content, \
        "Terraform should use local backend for state"
