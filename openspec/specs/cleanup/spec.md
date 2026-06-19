# cleanup Specification

## Purpose

Remove all AWS resources created during the build and deployment process. This capability covers the `scripts/cleanup.py` script that loads resource identifiers from the AMI build result file, destroys the Terraform-managed deployment infrastructure, deregisters the attestable AMI and deletes its associated EBS snapshot (unless preservation is requested), and verifies that no resources remain — reporting anything left over for manual cleanup.

## Requirements

### Requirement: Cleanup configuration and input loading

The Cleanup_Script SHALL load resource identifiers from the AMI build result file, support a flag to preserve the AMI/snapshot, log to stdout and a log file, and require interactive confirmation before destroying anything.

#### Scenario: Inputs loaded and confirmed

- **WHEN** the Cleanup_Script runs
- **THEN** it accepts `--ami-build-result` (default `ami_build_result.json`), `--terraform-dir` (default `terraform/deploy`), and `--keep-ami` (default disabled), parses the result file to extract `ami_id`, `snapshot_id`, and `region` (logging them at INFO), configures logging to stdout and `cleanup.log`, and prompts for confirmation before proceeding

#### Scenario: Missing or invalid result file

- **WHEN** the AMI build result file is absent or cannot be parsed
- **THEN** the Cleanup_Script fails with a `FileNotFoundError` or `RuntimeError` respectively

#### Scenario: Aborted without confirmation

- **WHEN** the user does not confirm with `yes` or `y`
- **THEN** the Cleanup_Script exits with return code 0 without deleting resources

### Requirement: Terraform infrastructure destruction

The Cleanup_Script SHALL run `terraform init` then `destroy` in the Terraform directory and verify the state shows no remaining resources, skipping gracefully when there is nothing to destroy.

#### Scenario: Destroy and verify

- **WHEN** the Terraform directory and a `terraform.tfstate` exist
- **THEN** the Cleanup_Script runs `terraform init`, then `terraform destroy -auto-approve` with a dummy `attestable_ami_id` value, and verifies the state file shows no remaining resources (warning if any remain)

#### Scenario: Skip when nothing to destroy

- **WHEN** the Terraform directory does not exist or has no `terraform.tfstate`
- **THEN** the Cleanup_Script logs a warning and skips Terraform destruction

#### Scenario: Init/destroy failure

- **WHEN** `terraform init` or `terraform destroy` exits non-zero
- **THEN** the Cleanup_Script raises a `RuntimeError`

### Requirement: AMI deregistration and snapshot deletion

Unless `--keep-ami` is provided, the Cleanup_Script SHALL deregister the attestable AMI and delete its associated EBS snapshot, checking existence first and verifying propagation.

#### Scenario: AMI deregistered and snapshot deleted

- **WHEN** `--keep-ami` is not provided
- **THEN** the Cleanup_Script creates an EC2 client for the result region, checks the AMI exists via `describe_images`, deregisters it via `DeregisterImage` with `DeleteAssociatedSnapshots=True`, waits 2 seconds, and verifies deregistration and snapshot deletion propagated

#### Scenario: AMI already gone

- **WHEN** the AMI is not found (`InvalidAMIID.NotFound`)
- **THEN** the Cleanup_Script logs a warning and skips deregistration; if `DeregisterImage` fails it logs and raises the `ClientError`

#### Scenario: Preservation requested

- **WHEN** `--keep-ami` is provided
- **THEN** the Cleanup_Script skips AMI deregistration and snapshot deletion entirely and logs at INFO that they were preserved

### Requirement: Cleanup verification and reporting

After deregistration the Cleanup_Script SHALL verify all resources are removed and report anything remaining, returning an appropriate exit code.

#### Scenario: Remaining resources reported

- **WHEN** verification runs
- **THEN** the Cleanup_Script checks for EC2 instances tagged Purpose `AMI Build` or `Attestation Demo` (in pending/running/stopping/stopped states) and, unless `--keep-ami`, the specific AMI and snapshot; if any remain it logs a warning listing each resource type, ID, and status and advises manual deletion

#### Scenario: Clean verification

- **WHEN** no remaining resources are found
- **THEN** the Cleanup_Script logs that verification is complete (noting intentional AMI/snapshot preservation when `--keep-ami` was used)

#### Scenario: Exit codes

- **WHEN** the cleanup process finishes
- **THEN** the Cleanup_Script returns exit code 0 when all steps succeed, or exit code 1 (logging that some resources may still exist) if any step failed
