# deployment Specification

## Purpose

Deploy the attestable AMI as a running Target_Instance that serves the attestation API. This capability covers the `terraform/deploy/` configuration that provisions an isolated VPC with internet access and a security group exposing only port 8080, launches the EC2 instance from the attestable AMI with NitroTPM and IMDSv2, and outputs the resource identifiers and attestation API URL; the `scripts/deploy.py` script that loads the AMI build result, orchestrates Terraform, and persists infrastructure state; and the optional SSH debug access controlled by opt-in flags.

## Requirements

### Requirement: Deployment network infrastructure

The Deploy_Terraform SHALL provision an isolated VPC with internet access so the Target_Instance can serve HTTP while remaining network-isolated from other resources.

#### Scenario: Isolated VPC with internet access

- **WHEN** the Deploy_Terraform is applied
- **THEN** it creates a VPC (10.0.0.0/16, DNS hostnames and support enabled), a public subnet (10.0.1.0/24, map-public-IP-on-launch) in the first AZ, an Internet Gateway, a route table with a default route through the gateway associated with the subnet, and tags all network resources with `github-runner-ec2-attestation`-prefixed Name tags

### Requirement: Deployment security group configuration

The Deploy_Terraform SHALL create a security group exposing only the attestation API port to the world and no other inbound port by default.

#### Scenario: Only port 8080 inbound by default

- **WHEN** the deployment security group is created
- **THEN** it allows inbound TCP 8080 from `0.0.0.0/0`, allows all outbound traffic, and allows no inbound SSH (port 22) or any other inbound port by default

### Requirement: Target EC2 instance provisioning

The Deploy_Terraform SHALL launch the Target_Instance from the attestable AMI with NitroTPM and IMDSv2 enabled, requiring the AMI ID as a mandatory input and supporting GPU-equipped instance types.

#### Scenario: Attestable instance launched

- **WHEN** the Target_Instance is launched
- **THEN** it uses the required `attestable_ami_id` variable (no default), the `instance_type` variable (default `c5.9xlarge`) in the public subnet with a public IP and the deployment security group, with detailed monitoring, IMDSv2 required (`http_tokens=required`, hop limit 1), the `aws_region` variable (default `us-east-1`), and AWS provider `~> 5.0`

#### Scenario: GPU instance types supported

- **WHEN** `instance_type` is a GPU-equipped family (e.g. G4dn, G5, G6, G6e, P5)
- **THEN** it is accepted, since these types support both NitroTPM and NVIDIA GPUs for attestable GPU workloads

### Requirement: Deployment outputs

The Deploy_Terraform SHALL output all key resource identifiers and the constructed attestation API URL so downstream processes can reference the deployment.

#### Scenario: Identifiers and URL output

- **WHEN** the Deploy_Terraform completes
- **THEN** it outputs `vpc_id`, `subnet_id`, `security_group_id`, `instance_id`, `instance_public_ip`, and `attestation_api_url` constructed as `http://{instance_public_ip}:8080`

### Requirement: Deployment script AMI loading

The Deploy_Script SHALL load AMI build results from a JSON file, accepting configurable input/output paths and instance type, and failing clearly on a missing or unparseable file.

#### Scenario: AMI result parsed

- **WHEN** the Deploy_Script runs
- **THEN** it accepts `--ami-build-result` (default `ami_build_result.json`), `--instance-type` (default `c5.9xlarge`), and `--output-file` (default `infrastructure_state.json`), and parses the result file to extract `ami_id`, `snapshot_id`, and `region`

#### Scenario: Missing or invalid result file

- **WHEN** the AMI build result file is absent or cannot be parsed
- **THEN** the Deploy_Script fails with a `FileNotFoundError` or `RuntimeError` respectively

### Requirement: Terraform orchestration and state persistence

The Deploy_Script SHALL run `terraform init` and `apply` in `terraform/deploy`, then persist the resulting infrastructure state to a JSON file, logging operations and advising cleanup on failure.

#### Scenario: Init, apply, and persist

- **WHEN** the Deploy_Script orchestrates Terraform
- **THEN** it runs `terraform init` then `terraform apply -auto-approve` passing `attestable_ami_id`, `instance_type`, and `aws_region` via `-var`, retrieves outputs via `terraform output -json`, extracts each output's `value`, and writes the infrastructure state to the output file as 2-space-indented JSON

#### Scenario: Failure handling

- **WHEN** the `terraform/deploy` directory is missing, or `init`/`apply` exits non-zero, or saving the state file fails
- **THEN** the Deploy_Script fails with a `FileNotFoundError` or `RuntimeError` and logs a message advising the user to run `terraform destroy` to clean up partial resources

### Requirement: Optional SSH debug access at deployment

The Deploy_Script and Deploy_Terraform SHALL support opt-in SSH debug access, attaching a key pair and opening port 22 only when explicitly enabled, and otherwise leaving no key pair and no SSH ingress.

#### Scenario: SSH disabled by default

- **WHEN** `--enable-ssh` is not provided
- **THEN** the Target_Instance has no `key_name` set and the security group contains no inbound rule for port 22

#### Scenario: SSH enabled with key pair

- **WHEN** `--enable-ssh` is provided with `--key-pair-name`
- **THEN** the Deploy_Script detects the operator IP via `https://checkip.amazonaws.com` (5s timeout), passes `enable_ssh`, `key_pair_name`, and `allowed_ssh_cidr={ip}/32` as `-var` flags, Terraform adds an inbound TCP 22 rule from `allowed_ssh_cidr` and attaches the key pair, the script logs a warning that SSH debug access is enabled, and `ssh_enabled` is recorded in the infrastructure state

#### Scenario: Key pair required when SSH enabled

- **WHEN** `--enable-ssh` is provided without `--key-pair-name`
- **THEN** the Deploy_Script fails with an error indicating `--key-pair-name` is required when SSH is enabled
