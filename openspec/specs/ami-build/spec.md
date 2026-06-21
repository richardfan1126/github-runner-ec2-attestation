# ami-build Specification

## Purpose

Convert the published, attested KIWI image artifact into an AWS AMI, and orchestrate that conversion as a CI job. This capability covers the AMI_Build_Script (`scripts/build-ami.py`) that provisions a temporary EC2 build instance via Terraform, verifies the artifact's GitHub attestation (including optional producing-workflow identity), downloads and validates the artifact, uploads the raw disk image to an EBS snapshot, registers an AMI with the correct boot/TPM attributes, outputs the result, and tears down all infrastructure; the `build-ami` GitHub Actions job that drives it after `build-and-publish`; and the Terraform IAM role that the job assumes via OIDC.

Artifact production and the debug-image annotation are specified in `image-build`; deployment of the resulting AMI in `deployment`.

## Requirements

### Requirement: build-ami job dependency and trigger

The `build-ami` job SHALL declare `needs: build-and-publish` so it runs only after that job succeeds, and SHALL run on pushes to `main`, on `workflow_dispatch` with `enable_ssh: false`, and on `workflow_dispatch` with `enable_ssh: true` (Debug_Build), but SHALL be skipped on pushes to `develop`.

#### Scenario: Runs after build-and-publish on applicable triggers

- **WHEN** the workflow is triggered by a push to `main`, or by `workflow_dispatch` with either value of `enable_ssh`
- **THEN** the `build-ami` job executes after `build-and-publish` completes successfully

#### Scenario: Skipped on develop

- **WHEN** the workflow is triggered by a push to `develop`
- **THEN** the `build-ami` job is skipped

### Requirement: build-ami runner, checkout, and environment

The `build-ami` job SHALL run on `ubuntu-24.04`, check out the repository with `submodules: recursive` as its first step, install Terraform via `hashicorp/setup-terraform` pinned to a specific version, and set up Python by running `uv sync` and invoking the script via `uv run`.

#### Scenario: Job environment prepared

- **WHEN** the `build-ami` job starts
- **THEN** it runs on `ubuntu-24.04`, checks out with `actions/checkout@v4` and recursive submodules, installs a pinned Terraform (e.g. `1.12.2`), runs `uv sync`, and invokes `uv run python scripts/build-ami.py`

### Requirement: build-ami AWS credentials via OIDC

The `build-ami` job SHALL obtain AWS credentials via `aws-actions/configure-aws-credentials` using OIDC to assume the role in `vars.AWS_ROLE_ARN`, setting the region from `vars.AWS_REGION` (default `us-east-1`), and SHALL NOT store long-lived AWS keys.

#### Scenario: Short-lived credentials assumed

- **WHEN** the `build-ami` job acquires credentials
- **THEN** it assumes `vars.AWS_ROLE_ARN` via OIDC, uses `vars.AWS_REGION` (defaulting to `us-east-1`), and stores no long-lived access keys as secrets

#### Scenario: Least-privilege job permissions

- **WHEN** the `build-ami` job declares permissions
- **THEN** it declares `id-token: write`, `contents: read`, and `packages: read`, and does NOT declare `attestations: write` or `packages: write`

### Requirement: AMI_Build_Script invocation and outputs

The `build-ami` job SHALL invoke AMI_Build_Script with the digest-pinned artifact reference and supporting arguments, upload the result JSON, and summarize it, passing `--allow-debug` only for Debug_Builds.

#### Scenario: Script arguments

- **WHEN** the `build-ami` job invokes AMI_Build_Script
- **THEN** it passes `--artifact-ref ${{ needs.build-and-publish.outputs.artifact_ref }}`, `--region <configured region>`, `--output-file ami_build_result.json`, and `--expected-workflow .github/workflows/build-attestable-image.yml`
- **AND** it passes `--allow-debug` only when triggered via `workflow_dispatch` with `enable_ssh: true`, and not otherwise

#### Scenario: Result artifact and summary on success

- **WHEN** AMI_Build_Script exits 0
- **THEN** the job uploads `ami_build_result.json` as artifact `ami-build-result` with 90-day retention and appends the AMI ID, snapshot ID, region, and build timestamp to `$GITHUB_STEP_SUMMARY` (plus an explicit debug warning for a successful Debug_Build)

#### Scenario: Failure handling

- **WHEN** AMI_Build_Script exits non-zero
- **THEN** the job does not upload the artifact, fails the job, and appends a failure notice to `$GITHUB_STEP_SUMMARY`

### Requirement: Build instance provisioning

The AMI_Converter SHALL provision a temporary Build_Instance via Terraform in the specified region, isolated in its own VPC with SSH restricted to the operator's IP, using Amazon Linux 2023 with IMDSv2 required and an IAM instance profile scoped to EC2/EBS snapshot operations.

#### Scenario: Isolated build instance

- **WHEN** the AMI_Converter provisions the Build_Instance
- **THEN** it detects the operator's public IP (checkip.amazonaws.com), creates a VPC (10.2.0.0/16) with a public subnet (10.2.1.0/24), Internet Gateway and route table, a security group allowing SSH only from the operator IP `/32`, a 4096-bit RSA key (saved to a 600-permission temp file), and an AL2023 instance with IMDSv2 required, then waits for running + status checks

#### Scenario: Artifact reference validated and digest-pinned

- **WHEN** the AMI_Converter receives the `artifact_ref` argument
- **THEN** it validates the reference against a strict allowlist (rejecting shell metacharacters), requires an `@sha256:` digest component (terminating otherwise), and uses only the digest — ignoring any tag — for both verification and pull

### Requirement: SSH connectivity verification

The AMI_Converter SHALL verify SSH connectivity to the Build_Instance via paramiko before installing tools, connecting as `ec2-user` with retries and keepalive, and failing with a connection error if all retries are exhausted.

#### Scenario: Connectivity retried before proceeding

- **WHEN** the AMI_Converter verifies SSH connectivity
- **THEN** it connects as `ec2-user` with the generated key, retrying up to 10 times with 30-second delays, 30-second keepalive, and 10-second connection/banner timeouts, failing with a connection error if all retries fail

### Requirement: Build tool installation

The AMI_Converter SHALL install the tools needed for verification and AMI creation on the Build_Instance (git, gcc, a signature-verified Rust toolchain, ORAS, GitHub CLI, and coldsnap built from a pinned source), verifying each installation and streaming output to logs.

#### Scenario: Tools installed and verified

- **WHEN** the AMI_Converter installs build tools
- **THEN** it installs git/gcc via dnf, installs Rust from the official standalone tarball after GPG-verifying its detached signature (key 85AB96E6FA1BE5FE), installs ORAS (checksum-verified) to `/usr/local/bin`, installs GitHub CLI, builds coldsnap via `cargo install --locked` from a pinned tag, and verifies `oras version`, `gh version`, and `coldsnap --help`

#### Scenario: Installation failure terminates

- **WHEN** any tool installation fails or an integrity check (ORAS checksum, Rust GPG signature) does not match
- **THEN** the AMI_Converter fails with an integrity/installation error without proceeding

### Requirement: Artifact signature verification

Before downloading the artifact, the Signature_Verifier SHALL verify the artifact's GitHub attestation against the exact `sha256:` digest from the artifact reference, offline using the downloaded bundle, and SHALL NOT proceed with an untrusted artifact under any circumstances.

#### Scenario: Attestation verified against digest

- **WHEN** tools are installed
- **THEN** the Signature_Verifier fetches the manifest digest via `oras manifest fetch`, downloads the attestation bundle from `api.github.com/repos/{owner}/{repo}/attestations/sha256:{digest}`, and runs `gh attestation verify oci://` with `-R` and offline `-b bundle.json`, proceeding only on exit code 0 and terminating without creating an AMI on any non-zero exit

#### Scenario: Verification and pull use the same digest

- **WHEN** the artifact is verified and later pulled
- **THEN** both use the same `sha256:` digest from the artifact reference (not a mutable tag), ensuring cryptographic binding, as covered by regression tests

### Requirement: Optional producing-workflow verification

When `--expected-workflow` is provided, the Signature_Verifier SHALL verify the producing workflow identity from the attestation certificate's SubjectAlternativeName, terminating on mismatch.

#### Scenario: Workflow identity enforced

- **WHEN** `--expected-workflow` is provided
- **THEN** the verifier runs `gh attestation verify --format json` (without `GH_FORCE_TTY`), extracts the SAN via `jq`, and considers identity verified only if the SAN contains the expected workflow path as a substring, terminating with a workflow-mismatch error otherwise; a separate human-readable verification run uses `GH_FORCE_TTY=1`

#### Scenario: Skipped when not requested

- **WHEN** `--expected-workflow` is not provided
- **THEN** workflow identity verification is skipped

### Requirement: Artifact download and validation

After signature verification, the AMI_Converter SHALL pull the artifact bundle into `~/artifacts` on the Build_Instance and validate that the raw disk image and `pcr_measurements.json` are present and parseable, failing on any missing or unparseable file.

#### Scenario: Artifact contents validated

- **WHEN** signature verification succeeds
- **THEN** the AMI_Converter pulls the artifact via ORAS into `~/artifacts`, verifies a `.raw` image and `pcr_measurements.json` exist in `~/artifacts/build-output`, parses the JSON to extract PCR4/PCR7, logs artifact sizes, and fails with a file-not-found or parsing error if any check fails

### Requirement: Snapshot upload and AMI registration

The AMI_Converter SHALL upload the validated raw disk image to an EBS snapshot via coldsnap and register an AMI with the attributes required for an attestable NitroTPM instance, after validating the raw filename safely.

#### Scenario: Raw filename validated against injection

- **WHEN** the AMI_Converter selects the raw image
- **THEN** it enumerates `.raw` files programmatically (not `ls *.raw`), requires exactly one, validates the basename against `^[a-zA-Z0-9][a-zA-Z0-9._-]*\.raw$`, and uses `shlex.quote()` or subprocess list args — rejecting filenames with shell metacharacters before constructing any shell command (covered by regression tests)

#### Scenario: Snapshot and AMI created with attestable attributes

- **WHEN** the raw image is validated
- **THEN** coldsnap uploads it, the snapshot ID is parsed and waited on to completion, and the AMI is registered with `VirtualizationType=hvm`, `BootMode=uefi`, `Architecture=x86_64`, `TpmSupport=v2.0`, `EnaSupport=True`, `RootDeviceName=/dev/xvda`, a block device mapping to the snapshot, and a name `attestable-ami-imported-{architecture}-{timestamp}` using AWS-allowed characters

#### Scenario: Failure modes

- **WHEN** snapshot upload, the snapshot waiter, or AMI registration fails
- **THEN** the AMI_Converter fails with the corresponding upload/waiter/ClientError error

### Requirement: Build result output and infrastructure cleanup

On successful registration the AMI_Converter SHALL write a result JSON (`ami_id`, `snapshot_id`, `region`, `build_timestamp`, `pcr_measurements`) and SHALL always tear down the Build_Instance and securely delete the temporary SSH key in a `finally` block, logging but not failing on cleanup errors.

#### Scenario: Result written

- **WHEN** AMI registration succeeds
- **THEN** the AMI_Converter writes the result (including PCR4/PCR7 and an ISO 8601 `build_timestamp`) to `--output-file` as 2-space-indented JSON

#### Scenario: Always-cleanup in finally

- **WHEN** the conversion finishes or fails
- **THEN** a `finally` block closes SSH, runs `terraform destroy -auto-approve` in `terraform/build-ami` with the same variables used at apply, overwrites the temp SSH key with random bytes before unlinking, and logs (without failing) any cleanup error; Terraform state is documented as containing sensitive key material

### Requirement: Single-entry verifier record emission

After the AMI is registered, the `build-ami` job SHALL emit a **single-entry verifier record** mapping the baked image's manifest digest → PCR4 → AMI id → producing commit through the surfaces available **after** the AMI exists: the GitHub job log, the step summary, and a tag on the registered AMI. The container image manifest digest is **additionally** carried as an ORAS annotation on the published KIWI artifact, but that annotation is fixed at publish time (alongside the existing `pcr4`/`pcr7` annotations) and SHALL NOT be amended with the AMI id afterward — the published artifact is immutable and digest-bound by its Sigstore attestation, and the AMI id does not exist until publishing has completed. The record SHALL be a clean single-entry seed whose field set is a subset of the multi-flavor `flavors.lock` introduced by the `execution-build-images` change, and SHALL NOT change the runtime NitroTPM attestation or its `user_data`.

#### Scenario: Record emitted across post-AMI surfaces

- **WHEN** AMI registration succeeds
- **THEN** the job emits a record containing the container image manifest digest, PCR4, the registered AMI id, and the producing commit to the job log and the step summary, and tags the registered AMI with the container image manifest digest

#### Scenario: Published-artifact annotation carries only the image digest

- **WHEN** the KIWI artifact was published earlier by the build-and-publish job
- **THEN** the container image manifest digest is the only verifier-record field carried as an ORAS annotation on that artifact (alongside `pcr4`/`pcr7`), and the AMI id is never added to the published artifact's annotations — because amending them would change the artifact digest and break its Sigstore attestation, and because the AMI id does not yet exist at publish time

#### Scenario: Verifier join is PCR4

- **WHEN** a remote verifier reads the published record
- **THEN** it can obtain a live NitroTPM attestation showing that PCR4 and conclude the instance runs the recorded image digest, with no change to the runtime attestation or `user_data` required

#### Scenario: Single-entry seed for flavors.lock

- **WHEN** the record is serialized
- **THEN** it is a single entry (one image manifest digest → PCR4 → AMI id → producing commit) whose field set is a subset of the per-flavor `flavors.lock` that the `execution-build-images` change introduces, so that change need not retrofit a record format onto an already-shipped AMI

### Requirement: AMI build IAM permission scoping

The Terraform IAM policy for the Build_Instance SHALL scope EC2/EBS permissions to the specific region and account using resource ARN patterns and the `aws:RequestedRegion` condition, never `Resource = "*"` for snapshot and image operations.

#### Scenario: Region- and account-scoped policy

- **WHEN** the Build_Instance IAM policy is defined
- **THEN** it scopes snapshots (`arn:aws:ec2:{region}::snapshot/*`), images (`arn:aws:ec2:{region}::image/*`), and volumes (`arn:aws:ec2:{region}:{account}:volume/*`) with the `aws:RequestedRegion` condition, and does not use a wildcard resource for EC2 snapshot/image operations

### Requirement: Per-flavor AMI build matrix

The `build-ami` stage SHALL run once per flavor selected by the dynamic matrix, producing one attestable AMI per flavor that carries that flavor's baked OCI layout and effective sandbox config. A single AMI SHALL NOT be shared across flavors. The matrix SHALL bound `max-parallel` (each AMI build is an EC2 instance) and SHALL apply the existing `develop`-skip rule (build/publish images on `develop`, register AMIs only on `main`).

#### Scenario: One AMI per selected flavor

- **WHEN** the matrix selects two flavors for full rebuild
- **THEN** the stage registers two distinct AMIs, each carrying its own flavor's baked image and effective config, with distinct PCR4 values

#### Scenario: develop skips AMI registration

- **WHEN** the pipeline runs on `develop`
- **THEN** changed-flavor images are built and published but no AMIs are registered

### Requirement: Multi-flavor flavors.lock aggregation written back by the pipeline

After each per-flavor AMI is registered, the pipeline SHALL aggregate the per-flavor verifier records into the git-committed `flavors.lock`, generalizing the single-entry verifier record (one image manifest digest → PCR4 → AMI id → producing commit) from one image to N flavors using the same field set. The pipeline SHALL write back and commit `flavors.lock`, carrying forward unchanged entries for flavors not rebuilt, serializing updates via a concurrency group, and recording each flavor's `producing commit` as the source commit that supplied its inputs.

#### Scenario: Each registered AMI lands an entry

- **WHEN** a flavor's AMI is registered
- **THEN** its `{image manifest digest, PCR4, AMI id, producing commit}` entry is written into `flavors.lock` and committed back, while the per-AMI post-registration surfaces (job log, step summary, AMI tag) continue to be emitted as in the single-entry record

#### Scenario: Subset rebuild preserves other entries

- **WHEN** a run rebuilds only some flavors
- **THEN** `flavors.lock` retains the existing entries for the flavors not rebuilt

#### Scenario: Debug build does not overwrite production entry

- **WHEN** a `workflow_dispatch` debug/SSH build targets a single flavor
- **THEN** it does not overwrite that flavor's production `flavors.lock` entry

### Requirement: GitHub Actions IAM role Terraform stack

A simple Terraform stack at `terraform/github-actions-iam-role/` SHALL create the IAM role the `build-ami` job assumes via OIDC, restricting trust to this repository and granting only the permissions AMI_Build_Script needs, parameterized for reuse across accounts/forks.

#### Scenario: Repository-scoped trust and outputs

- **WHEN** the IAM_Role_Stack is applied
- **THEN** it creates a role whose trust policy restricts assumption to the `repo:owner/github-runner-ec2-attestation:*` subject claim via GitHub Actions OIDC, attaches a policy granting EC2 provisioning, EBS snapshot management, AMI registration, and IAM pass-role, outputs the role ARN, accepts region and repository owner/name as variables, optionally creates the OIDC provider (boolean default `true`), and ships a `README.md` documenting variables and one-time apply
