# Requirements Document

## Introduction

This feature adds a `build-ami` CI job to `.github/workflows/build-attestable-image.yml` that runs automatically after the existing `build-and-publish` job succeeds. The new job invokes `scripts/build-ami.py` with the artifact reference and other outputs produced by `build-and-publish`, provisions an EC2 instance via Terraform, verifies the artifact's GitHub attestation, converts the raw disk image into an EBS snapshot, registers an AMI, and uploads the resulting AMI metadata as a workflow artifact. The job runs on pushes to the `main` branch and on `workflow_dispatch` (including when `enable_ssh: true`, which produces a Debug_Build). When triggered as a Debug_Build, the job passes `--allow-debug` to AMI_Build_Script and prints an explicit warning to the GitHub workflow summary. It does not run on pushes to `develop`. The job always cleans up EC2 infrastructure on both success and failure.

## Glossary

- **Workflow**: The GitHub Actions workflow file `.github/workflows/build-attestable-image.yml`.
- **build-and-publish**: The existing job in the Workflow that builds the KIWI image and pushes it to GHCR.
- **build-ami**: The new CI job defined by this feature.
- **AMI_Build_Script**: The Python script at `scripts/build-ami.py`.
- **GHCR**: GitHub Container Registry (`ghcr.io`), where the OCI artifact is stored.
- **Artifact_Ref**: The fully-qualified OCI reference (`ghcr.io/<owner>/<repo>/attestable-image:<tag>`) output by `build-and-publish` as `steps.push.outputs.artifact_ref`.
- **OIDC**: OpenID Connect, used to obtain short-lived AWS credentials without storing long-lived secrets.
- **IAM_Role**: The AWS IAM role assumed by the `build-ami` job via OIDC to obtain AWS credentials.
- **Output_File**: The JSON file produced by AMI_Build_Script containing `ami_id`, `snapshot_id`, `region`, `build_timestamp`, and `pcr_measurements`.
- **Debug_Build**: A build triggered via `workflow_dispatch` with `enable_ssh: true`, producing an artifact annotated `debug=true`.
- **Expected_Workflow**: The workflow file path passed to AMI_Build_Script via `--expected-workflow` to enforce provenance verification.
- **IAM_Role_Stack**: The Terraform configuration at `terraform/github-actions-iam-role/` that provisions the IAM_Role assumed by the `build-ami` job.
- **OIDC_Provider**: The AWS IAM OpenID Connect identity provider for `token.actions.githubusercontent.com`, which must already exist in the AWS account before the IAM_Role_Stack is applied.
- **GitHub_Actions_OIDC**: The OIDC token issued by GitHub Actions to a running job, used to assume the IAM_Role without long-lived credentials.

---

## Requirements

### Requirement 1: Job Dependency and Trigger Condition

**User Story:** As a repository maintainer, I want the `build-ami` job to run after `build-and-publish` succeeds on all applicable builds (including debug), so that AMIs are built from both production and debug artifacts.

#### Acceptance Criteria

1. THE `build-ami` job SHALL declare `needs: build-and-publish` so that it runs only after `build-and-publish` completes successfully.
2. WHEN the Workflow is triggered by a push to the `main` branch, THE `build-ami` job SHALL execute.
3. WHEN the Workflow is triggered by a push to the `develop` branch, THE `build-ami` job SHALL be skipped.
4. WHEN the Workflow is triggered via `workflow_dispatch` with `enable_ssh: false`, THE `build-ami` job SHALL execute.
5. WHEN the Workflow is triggered via `workflow_dispatch` with `enable_ssh: true` (a Debug_Build), THE `build-ami` job SHALL execute.

### Requirement 2: Runner and Checkout

**User Story:** As a CI operator, I want the `build-ami` job to run on a standard GitHub-hosted runner with the repository checked out, so that `scripts/build-ami.py` and Terraform configurations are available.

#### Acceptance Criteria

1. THE `build-ami` job SHALL run on `ubuntu-24.04`.
2. THE `build-ami` job SHALL check out the repository using `actions/checkout@v4` with `submodules: recursive` as its first step.

### Requirement 3: AWS Credential Acquisition via OIDC

**User Story:** As a security engineer, I want the `build-ami` job to obtain AWS credentials via OIDC rather than long-lived secrets, so that credentials are short-lived and scoped to the job.

#### Acceptance Criteria

1. THE `build-ami` job SHALL configure AWS credentials using `aws-actions/configure-aws-credentials` before invoking AMI_Build_Script.
2. THE `build-ami` job SHALL assume an IAM_Role identified by the repository environment variable `vars.AWS_ROLE_ARN` via OIDC.
3. THE `build-ami` job SHALL set the AWS region using the repository environment variable `vars.AWS_REGION`, defaulting to `us-east-1` if the variable is not set.
4. THE `build-ami` job SHALL NOT store long-lived AWS access keys as repository secrets.

### Requirement 4: Python Environment Setup

**User Story:** As a CI operator, I want the `build-ami` job to install the Python dependencies required by AMI_Build_Script, so that the script runs without import errors.

#### Acceptance Criteria

1. THE `build-ami` job SHALL install Python dependencies using `uv sync` before invoking AMI_Build_Script.
2. THE `build-ami` job SHALL invoke AMI_Build_Script via `uv run python scripts/build-ami.py` so that the correct virtual environment is used.

### Requirement 5: AMI_Build_Script Invocation

**User Story:** As a CI operator, I want the `build-ami` job to invoke AMI_Build_Script with the correct arguments derived from `build-and-publish` outputs, so that the correct artifact is converted into an AMI.

#### Acceptance Criteria

1. THE `build-ami` job SHALL pass `--artifact-ref` set to `${{ needs.build-and-publish.outputs.artifact_ref }}` when invoking AMI_Build_Script.
2. THE `build-ami` job SHALL pass `--region` set to the configured AWS region when invoking AMI_Build_Script.
3. THE `build-ami` job SHALL pass `--output-file ami_build_result.json` when invoking AMI_Build_Script.
4. THE `build-ami` job SHALL pass `--expected-workflow .github/workflows/build-attestable-image.yml` when invoking AMI_Build_Script.
5. WHEN the Workflow is triggered via `workflow_dispatch` with `enable_ssh: false` or by a push, THE `build-ami` job SHALL NOT pass `--allow-debug` when invoking AMI_Build_Script.
6. WHEN the Workflow is triggered via `workflow_dispatch` with `enable_ssh: true` (a Debug_Build), THE `build-ami` job SHALL pass `--allow-debug` when invoking AMI_Build_Script.

### Requirement 6: Output Artifact Upload

**User Story:** As a CI operator, I want the `build-ami` job to upload the AMI build result JSON as a workflow artifact, so that the AMI ID and metadata are accessible after the job completes.

#### Acceptance Criteria

1. WHEN AMI_Build_Script exits with code 0, THE `build-ami` job SHALL upload `ami_build_result.json` as a workflow artifact named `ami-build-result`.
2. THE uploaded artifact SHALL have a retention period of 90 days.
3. WHEN AMI_Build_Script exits with a non-zero code, THE `build-ami` job SHALL NOT upload the artifact and SHALL fail the job.

### Requirement 7: Workflow Summary

**User Story:** As a CI operator, I want the `build-ami` job to append a summary of the AMI build result to the GitHub Actions step summary, so that the AMI ID is visible in the workflow run UI and debug builds are clearly flagged.

#### Acceptance Criteria

1. WHEN AMI_Build_Script exits with code 0, THE `build-ami` job SHALL append the AMI ID, snapshot ID, region, and build timestamp from `ami_build_result.json` to `$GITHUB_STEP_SUMMARY`.
2. WHEN AMI_Build_Script exits with a non-zero code, THE `build-ami` job SHALL append a failure notice to `$GITHUB_STEP_SUMMARY`.
3. WHEN the Workflow is triggered via `workflow_dispatch` with `enable_ssh: true` (a Debug_Build) and AMI_Build_Script exits with code 0, THE `build-ami` job SHALL append an explicit warning to `$GITHUB_STEP_SUMMARY` indicating that the AMI was built from a debug artifact and is not intended for production use.

### Requirement 8: Job-Level Permissions

**User Story:** As a security engineer, I want the `build-ami` job to declare only the minimum GitHub token permissions it needs, so that the job follows the principle of least privilege.

#### Acceptance Criteria

1. THE `build-ami` job SHALL declare `id-token: write` permission to enable OIDC credential acquisition.
2. THE `build-ami` job SHALL declare `contents: read` permission to allow repository checkout.
3. THE `build-ami` job SHALL declare `packages: read` permission to allow ORAS to pull from GHCR.
4. THE `build-ami` job SHALL NOT declare `attestations: write` or `packages: write` permissions.

### Requirement 9: Terraform Availability

**User Story:** As a CI operator, I want the `build-ami` job to ensure Terraform is installed on the runner, so that AMI_Build_Script can provision the EC2 build instance.

#### Acceptance Criteria

1. THE `build-ami` job SHALL install Terraform using `hashicorp/setup-terraform` before invoking AMI_Build_Script.
2. THE `build-ami` job SHALL pin the Terraform version to a specific release (e.g., `1.12.2`) to ensure reproducible builds.

### Requirement 10: GitHub Actions IAM Role Terraform Stack

**User Story:** As a repository maintainer, I want a simple Terraform stack that creates the IAM role assumed by the `build-ami` CI job, so that the role's trust policy and permissions are version-controlled, reproducible, and easy to bootstrap in a new AWS account.

#### Acceptance Criteria

1. THE IAM_Role_Stack SHALL be located at `terraform/github-actions-iam-role/` within the repository.
2. THE IAM_Role_Stack SHALL consist of plain Terraform files with no external modules or complex abstractions.
3. THE IAM_Role_Stack SHALL create an AWS IAM role with a trust policy that permits GitHub Actions to assume it via GitHub_Actions_OIDC, scoped to the `github-runner-ec2-attestation` repository.
4. WHEN the trust policy is evaluated, THE IAM_Role_Stack SHALL restrict assumption to the `repo:owner/github-runner-ec2-attestation:*` subject claim so that only jobs from this repository can assume the role.
5. THE IAM_Role_Stack SHALL attach an inline or managed IAM policy granting the permissions required by AMI_Build_Script: EC2 instance provisioning (via Terraform), EBS snapshot creation and management, AMI registration, and IAM pass-role for the EC2 instance profile.
6. THE IAM_Role_Stack SHALL output the ARN of the created IAM role so that the operator can set `vars.AWS_ROLE_ARN` in the GitHub repository settings.
7. THE IAM_Role_Stack SHALL accept the AWS region and the GitHub repository owner/name as input variables so that the stack can be applied to different accounts or forks without modifying source files.
8. IF the OIDC_Provider for `token.actions.githubusercontent.com` does not exist in the target AWS account, THEN THE IAM_Role_Stack SHALL accept a boolean variable that controls whether to create it, defaulting to `true`.
9. THE IAM_Role_Stack SHALL include a `README.md` that documents the required input variables, the one-time `terraform init && terraform apply` usage, and how to set `vars.AWS_ROLE_ARN` from the stack output.
