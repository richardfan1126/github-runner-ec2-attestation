# Implementation Plan: `ami-build-ci-job`

## Overview

Add the `build-ami` CI job to `.github/workflows/build-attestable-image.yml` and create the supporting Terraform IAM role stack at `terraform/github-actions-iam-role/`. The workflow job is the primary deliverable; the Terraform stack is a one-time bootstrap artifact. Property-based tests cover the job condition logic, the summary generation function, and the debug warning logic.

## Tasks

- [x] 1. Add `artifact_ref` output to the `build-and-publish` job
  - Add `outputs:` block to the existing `build-and-publish` job that exposes `artifact_ref: ${{ steps.push.outputs.artifact_ref }}`
  - This output is consumed by the new `build-ami` job via `needs.build-and-publish.outputs.artifact_ref`
  - _Requirements: 5.1_

- [x] 2. Add the `build-ami` job to the workflow
  - [x] 2.1 Add job skeleton with dependency, runner, and `if` condition
    - Add `build-ami:` as a new top-level job entry in `.github/workflows/build-attestable-image.yml`
    - Set `needs: build-and-publish`
    - Set `runs-on: ubuntu-24.04`
    - Set `if: github.ref == 'refs/heads/main' || (github.event_name == 'workflow_dispatch' && inputs.enable_ssh == false)`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1_

  - [x] 2.2 Add job-level permissions block
    - Declare `permissions:` with `id-token: write`, `contents: read`, `packages: read`
    - Do NOT include `attestations: write` or `packages: write`
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [x] 2.3 Add checkout and AWS credential steps
    - Add `actions/checkout@v4` with `submodules: recursive` as the first step
    - Add `aws-actions/configure-aws-credentials` step after checkout, using `role-to-assume: ${{ vars.AWS_ROLE_ARN }}` and `aws-region: ${{ vars.AWS_REGION || 'us-east-1' }}`
    - Do NOT include `aws-access-key-id` or `aws-secret-access-key`
    - _Requirements: 2.2, 3.1, 3.2, 3.3, 3.4_

  - [x] 2.4 Add Terraform and Python environment setup steps
    - Add `hashicorp/setup-terraform` step with a pinned `terraform_version` (e.g. `1.12.2`)
    - Add a `uv sync` run step to install Python dependencies
    - _Requirements: 4.1, 9.1, 9.2_

  - [x] 2.5 Add the main script invocation step
    - Add a run step that invokes `uv run python scripts/build-ami.py` with flags:
      - `--artifact-ref "${{ needs.build-and-publish.outputs.artifact_ref }}"`
      - `--region "${{ vars.AWS_REGION || 'us-east-1' }}"`
      - `--output-file ami_build_result.json`
      - `--expected-workflow .github/workflows/build-attestable-image.yml`
    - Do NOT include `--allow-debug`
    - _Requirements: 4.2, 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 2.6 Add artifact upload and summary steps
    - Add `actions/upload-artifact@v4` step with `if: success()`, artifact name `ami-build-result`, path `ami_build_result.json`, and `retention-days: 90`
    - Add a success summary step with `if: success()` that reads `ami_build_result.json` with `jq` and appends AMI ID, snapshot ID, region, and build timestamp to `$GITHUB_STEP_SUMMARY`
    - Add a failure summary step with `if: failure()` that appends a failure notice to `$GITHUB_STEP_SUMMARY`
    - _Requirements: 6.1, 6.2, 6.3, 7.1, 7.2_

- [x] 3. Checkpoint — verify workflow YAML is valid
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Write property and unit tests for the `build-ami` job logic
  - [x] 4.1 Implement `evaluate_job_condition` helper function
    - Create `tests/test_ami_build_ci_job_properties.py`
    - Implement a pure Python `evaluate_job_condition(event_name, ref, enable_ssh)` function that mirrors the YAML `if:` expression: `ref == "refs/heads/main" or (event_name == "workflow_dispatch" and not enable_ssh)`
    - _Requirements: 1.2, 1.3, 1.4, 1.5_

  - [x] 4.2 Write property test for job condition (Property 1)
    - **Property 1: Job condition correctly classifies all trigger contexts**
    - Use `@given` with `event_name` sampled from `["push", "workflow_dispatch", "pull_request"]`, `ref` drawn from `st.one_of(st.just("refs/heads/main"), st.just("refs/heads/develop"), st.text(...))`, and `enable_ssh` as `st.booleans()`
    - Assert `evaluate_job_condition(event_name, ref, enable_ssh) == ((ref == "refs/heads/main") or (event_name == "workflow_dispatch" and not enable_ssh))`
    - Use `@settings(max_examples=200)`
    - **Validates: Requirements 1.2, 1.3, 1.4, 1.5**

  - [x] 4.3 Implement `generate_summary` helper function
    - In the same test file, implement a `generate_summary(build_result: dict) -> str` function that formats the four required fields (`ami_id`, `snapshot_id`, `region`, `build_timestamp`) into a summary string, matching the logic used in the workflow's success summary step
    - _Requirements: 7.1_

  - [x] 4.4 Write property test for summary generation (Property 2)
    - **Property 2: Summary script extracts all required fields from any valid build result**
    - Use `@given` with `ami_id` from `st.from_regex(r"ami-[0-9a-f]{17}", fullmatch=True)`, `snapshot_id` from `st.from_regex(r"snap-[0-9a-f]{17}", fullmatch=True)`, `region` sampled from a list of valid regions, and `build_timestamp` from `st.datetimes(timezones=st.just(timezone.utc)).map(lambda d: d.isoformat())`
    - Assert that `ami_id`, `snapshot_id`, `region`, and `build_timestamp` all appear in the generated summary string
    - Use `@settings(max_examples=100)`
    - **Validates: Requirements 7.1**

  - [x] 4.5 Write unit tests for workflow YAML structure
    - In `tests/test_ami_build_ci_job_unit.py`, parse `.github/workflows/build-attestable-image.yml` with PyYAML and assert:
      - `build-ami` job has `needs: build-and-publish`
      - `runs-on: ubuntu-24.04`
      - First step uses `actions/checkout@v4` with `submodules: recursive`
      - `aws-actions/configure-aws-credentials` step uses `role-to-assume: ${{ vars.AWS_ROLE_ARN }}`
      - `hashicorp/setup-terraform` step is present with a pinned `terraform_version`
      - Script invocation includes `--output-file ami_build_result.json` and `--expected-workflow .github/workflows/build-attestable-image.yml`
      - `--allow-debug` is absent from the script invocation
      - Upload step uses `if: success()`, artifact name `ami-build-result`, and `retention-days: 90`
      - Failure summary step uses `if: failure()`
      - Job permissions include `id-token: write`, `contents: read`, `packages: read`
      - Job permissions do NOT include `attestations: write` or `packages: write`
      - `aws-access-key-id` and `aws-secret-access-key` are absent from all steps
    - _Requirements: 1.1, 2.1, 2.2, 3.1, 3.2, 3.4, 4.2, 5.3, 5.4, 5.5, 6.1, 6.2, 7.2, 8.1, 8.2, 8.3, 8.4, 9.1, 9.2_

- [x] 5. Checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Create the `terraform/github-actions-iam-role/` stack
  - [x] 6.1 Create `variables.tf`
    - Define input variables: `aws_region` (string, default `"us-east-1"`), `github_org` (string, no default), `github_repo` (string, default `"github-runner-ec2-attestation"`), `create_oidc_provider` (bool, default `true`)
    - _Requirements: 10.2, 10.7, 10.8_

  - [x] 6.2 Create `main.tf`
    - Define `aws` provider block using `var.aws_region`
    - Conditionally create `aws_iam_openid_connect_provider` for `token.actions.githubusercontent.com` when `var.create_oidc_provider == true`
    - Create `aws_iam_role` with a trust policy allowing `sts:AssumeRoleWithWebIdentity` from the OIDC provider, with conditions: `aud == "sts.amazonaws.com"` and `sub` matches `repo:${var.github_org}/${var.github_repo}:*`
    - _Requirements: 10.2, 10.3, 10.4, 10.8_

  - [x] 6.3 Create `iam_policy.tf`
    - Define `aws_iam_policy_document` data source with all required permissions: EC2 instance provisioning, EBS snapshot (Direct API), AMI registration, IAM pass-role and instance profile management, and `sts:GetCallerIdentity`
    - Create `aws_iam_policy` resource from the document and attach it to the role via `aws_iam_role_policy_attachment`
    - _Requirements: 10.2, 10.5_

  - [x] 6.4 Create `outputs.tf`
    - Define `output "role_arn"` that outputs `aws_iam_role.github_actions.arn` with a description instructing the operator to set it as `vars.AWS_ROLE_ARN` in GitHub
    - _Requirements: 10.6_

  - [x] 6.5 Create `README.md` for the IAM role stack
    - Document all input variables with types, defaults, and descriptions
    - Provide the one-time bootstrap commands: `terraform init && terraform apply -var="github_org=<your-org>"`
    - Explain how to copy the `role_arn` output into the GitHub repository variable `vars.AWS_ROLE_ARN`
    - Note the `create_oidc_provider` flag for accounts where the provider already exists
    - _Requirements: 10.9_

- [x] 7. Final checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Add debug build support to the `build-ami` workflow job
  - [x] 8.1 Update job `if` condition to allow debug builds
    - Update the `if:` condition on the `build-ami` job in `.github/workflows/build-attestable-image.yml`
    - Change from: `if: github.ref == 'refs/heads/main' || (github.event_name == 'workflow_dispatch' && inputs.enable_ssh == false)`
    - Change to: `if: github.ref == 'refs/heads/main' || github.event_name == 'workflow_dispatch'`
    - This allows debug builds (`enable_ssh: true`) to run the `build-ami` job
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 8.2 Update script invocation step for conditional `--allow-debug`
    - Update the run step that invokes `uv run python scripts/build-ami.py` to conditionally include `--allow-debug`:
      - Add shell logic to set `ALLOW_DEBUG_FLAG=""` by default
      - WHEN `github.event_name == 'workflow_dispatch'` AND `inputs.enable_ssh == 'true'`, set `ALLOW_DEBUG_FLAG="--allow-debug"`
      - Pass `$ALLOW_DEBUG_FLAG` as part of the script invocation
    - Retain existing flags:
      - `--artifact-ref "${{ needs.build-and-publish.outputs.artifact_ref }}"`
      - `--region "${{ vars.AWS_REGION || 'us-east-1' }}"`
      - `--output-file ami_build_result.json`
      - `--expected-workflow .github/workflows/build-attestable-image.yml`
    - _Requirements: 5.5, 5.6_

  - [x] 8.3 Add debug warning step to workflow summary
    - Add a new step after the success summary step with `if: success() && github.event_name == 'workflow_dispatch' && inputs.enable_ssh == true`
    - The step appends an explicit warning to `$GITHUB_STEP_SUMMARY` indicating the AMI was built from a debug artifact and is not intended for production use
    - _Requirements: 7.3_

- [x] 9. Checkpoint — verify workflow YAML is valid after debug build changes
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Update property and unit tests for debug build support
  - [x] 10.1 Update `evaluate_job_condition` helper function
    - In `tests/test_ami_build_ci_job_properties.py`, update the `evaluate_job_condition(event_name, ref, enable_ssh)` function to mirror the new YAML `if:` expression: `ref == "refs/heads/main" or event_name == "workflow_dispatch"` (the `enable_ssh` parameter no longer affects the job condition)
    - _Requirements: 1.2, 1.3, 1.4, 1.5_

  - [x] 10.2 Update property test for job condition (Property 1)
    - **Property 1: Job condition correctly classifies all trigger contexts**
    - Update the assertion to: `evaluate_job_condition(event_name, ref, enable_ssh) == ((ref == "refs/heads/main") or (event_name == "workflow_dispatch"))`
    - The `enable_ssh` parameter is still generated but no longer affects the expected result
    - Use `@settings(max_examples=200)`
    - **Validates: Requirements 1.2, 1.3, 1.4, 1.5**

  - [x] 10.3 Update unit tests for workflow YAML structure
    - In `tests/test_ami_build_ci_job_unit.py`, update assertions:
      - Update the `if:` condition check to match the new expression: `github.ref == 'refs/heads/main' || github.event_name == 'workflow_dispatch'`
      - Update the script invocation test: `--allow-debug` should now be conditionally present (check that the step contains the conditional shell logic for `ALLOW_DEBUG_FLAG`)
      - Add a test verifying a debug warning step exists with a condition checking for `workflow_dispatch` and `enable_ssh == true`
      - Add a test verifying the debug warning step writes to `$GITHUB_STEP_SUMMARY`
    - _Requirements: 1.1, 1.5, 5.5, 5.6, 7.3_

- [x] 11. Final checkpoint — ensure all tests pass after debug build changes
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP
- Tasks 1–7 are the original implementation (completed); tasks 8–11 add debug build support
- The `build-ami` job's `if:` expression (after task 8.1) allows all `workflow_dispatch` triggers (including debug builds); the `enable_ssh` input only controls the `--allow-debug` flag and the debug warning
- The `terraform/github-actions-iam-role/` stack is applied once by a human operator; it is not invoked by the CI job itself
- Property tests use Hypothesis, consistent with the existing test suite
- The `evaluate_job_condition` and `generate_summary` helpers are test-only utilities that mirror the YAML/shell logic for property verification
