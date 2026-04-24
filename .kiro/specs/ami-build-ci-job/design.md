# Design Document: `ami-build-ci-job`

## Overview

This feature adds a `build-ami` CI job to `.github/workflows/build-attestable-image.yml`. The job runs automatically after the existing `build-and-publish` job succeeds on production builds (pushes to `main` and non-debug `workflow_dispatch` triggers). It invokes `scripts/build-ami.py` with the artifact reference produced by `build-and-publish`, which provisions an EC2 instance via Terraform, verifies the artifact's GitHub attestation, converts the raw disk image into an EBS snapshot, registers an AMI, and writes the result to a JSON file. The job uploads that JSON as a workflow artifact and appends a summary to the GitHub Actions step summary. EC2 infrastructure is always cleaned up, whether the build succeeds or fails.

The design is intentionally narrow: this is a CI job definition, not a new application. The primary deliverable is a well-structured YAML job block added to the existing workflow file.

## Architecture

```mermaid
flowchart TD
    A[push to main\nor workflow_dispatch\nenable_ssh=false] --> B[build-and-publish job]
    B -->|outputs artifact_ref| C{build-ami\nif condition}
    C -->|main branch or\nnon-debug dispatch| D[build-ami job]
    C -->|develop branch or\ndebug dispatch| E[skipped]

    D --> D1[Checkout repo]
    D1 --> D2[Configure AWS via OIDC]
    D2 --> D3[Setup Terraform]
    D3 --> D4[uv sync]
    D4 --> D5[uv run python scripts/build-ami.py]
    D5 -->|exit 0| D6[Upload ami_build_result.json\nas artifact]
    D5 -->|exit 0| D7[Append success summary\nto GITHUB_STEP_SUMMARY]
    D5 -->|exit != 0| D8[Append failure notice\nto GITHUB_STEP_SUMMARY]
    D5 -->|always| D9[Terraform destroy\ncleans up EC2]
```

The `build-ami` job is a downstream consumer of `build-and-publish`. It does not modify the workflow's trigger conditions or the `build-and-publish` job itself. The job-level `if:` expression encodes all skip logic so that the job is simply absent from the run when not applicable, rather than running and then doing nothing.

## Components and Interfaces

### Job: `build-ami`

Defined in `.github/workflows/build-attestable-image.yml` as a new top-level job entry.

**Key attributes:**

| Attribute | Value |
|---|---|
| `needs` | `build-and-publish` |
| `runs-on` | `ubuntu-24.04` |
| `if` condition | `github.ref == 'refs/heads/main' \|\| (github.event_name == 'workflow_dispatch' && inputs.enable_ssh == false)` |
| Permissions | `id-token: write`, `contents: read`, `packages: read` |

**Inputs consumed from `build-and-publish`:**

- `needs.build-and-publish.outputs.artifact_ref` — the fully-qualified GHCR reference passed as `--artifact-ref` to the script.

**Outputs produced:**

- Workflow artifact `ami-build-result` containing `ami_build_result.json` (retained 90 days).
- GitHub Actions step summary with AMI ID, snapshot ID, region, and build timestamp on success; failure notice on error.

### Script: `scripts/build-ami.py`

The script is pre-existing. The CI job invokes it as:

```bash
uv run python scripts/build-ami.py \
  --artifact-ref "${{ needs.build-and-publish.outputs.artifact_ref }}" \
  --region "${{ vars.AWS_REGION || 'us-east-1' }}" \
  --output-file ami_build_result.json \
  --expected-workflow .github/workflows/build-attestable-image.yml
```

The `--allow-debug` flag is intentionally omitted on production builds. The script exits 0 on success and non-zero on any failure; the CI job relies on this contract for conditional steps.

### Step Ordering

Steps within `build-ami` must follow this order to satisfy dependency constraints:

1. `actions/checkout@v4` (with `submodules: recursive`)
2. `aws-actions/configure-aws-credentials` (OIDC, before any AWS or Terraform calls)
3. `hashicorp/setup-terraform` (pinned version, before script invocation)
4. `uv sync` (install Python deps, before script invocation)
5. `uv run python scripts/build-ami.py` (main build step)
6. Upload artifact (`if: success()`)
7. Write success summary (`if: success()`)
8. Write failure summary (`if: failure()`)

Steps 6–8 are conditional; step 5's cleanup (Terraform destroy) is handled inside the script's `finally` block, so no separate cleanup step is needed in the workflow.

## Data Models

### `ami_build_result.json`

Produced by `scripts/build-ami.py` (`generate_build_result` function). Schema:

```json
{
  "ami_id": "ami-0123456789abcdef0",
  "snapshot_id": "snap-0123456789abcdef0",
  "region": "us-east-1",
  "build_timestamp": "2025-01-15T12:34:56.789012+00:00",
  "pcr_measurements": {
    "pcr4": "<hex string>",
    "pcr7": "<hex string>"
  }
}
```

This file is written to the runner's working directory and uploaded as the `ami-build-result` workflow artifact.

### Job Condition Expression

The `if:` expression on the `build-ami` job is a pure boolean function of the GitHub Actions context:

```
github.ref == 'refs/heads/main'
|| (github.event_name == 'workflow_dispatch' && inputs.enable_ssh == false)
```

Truth table:

| Trigger | `github.ref` | `enable_ssh` | Result |
|---|---|---|---|
| push to `main` | `refs/heads/main` | N/A | ✅ run |
| push to `develop` | `refs/heads/develop` | N/A | ⛔ skip |
| `workflow_dispatch` | any | `false` | ✅ run |
| `workflow_dispatch` | any | `true` | ⛔ skip |

### AWS Region Expression

The region value used in both `configure-aws-credentials` and `--region` is:

```
${{ vars.AWS_REGION || 'us-east-1' }}
```

This expression must be identical in both places to ensure consistency.

## Terraform IAM Role Stack (`terraform/github-actions-iam-role/`)

### Purpose

This stack is a one-time bootstrap step. It creates the AWS IAM role that the `build-ami` CI job assumes via OIDC. Once applied, the operator copies the `role_arn` output into the GitHub repository variable `vars.AWS_ROLE_ARN`. The stack does not need to be re-applied on every CI run.

### File Layout

```
terraform/github-actions-iam-role/
├── main.tf          # OIDC provider (conditional) + IAM role + trust policy
├── iam_policy.tf    # IAM policy document and attachment
├── variables.tf     # Input variables
├── outputs.tf       # role_arn output
└── README.md        # Usage instructions
```

No modules. No remote state configuration is prescribed (operators may add a backend block for their environment).

### Input Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `aws_region` | `string` | `"us-east-1"` | AWS region to deploy into |
| `github_org` | `string` | — | GitHub organisation or user name (e.g. `my-org`) |
| `github_repo` | `string` | `"github-runner-ec2-attestation"` | Repository name |
| `create_oidc_provider` | `bool` | `true` | Whether to create the GitHub Actions OIDC provider (set `false` if it already exists) |

### Trust Policy

The role's assume-role policy allows `sts:AssumeRoleWithWebIdentity` from the GitHub Actions OIDC provider, with two conditions:

- `token.actions.githubusercontent.com:aud` equals `sts.amazonaws.com`
- `token.actions.githubusercontent.com:sub` matches `repo:<github_org>/<github_repo>:*`

The wildcard on the subject allows any branch, tag, or environment within the repository to assume the role. Operators who want tighter scoping (e.g. only the `main` branch) can narrow the subject to `repo:<org>/<repo>:ref:refs/heads/main`.

### IAM Permissions

The attached policy grants the minimum permissions needed by `scripts/build-ami.py` and the `terraform/build-ami/` stack it invokes:

**EC2 — instance provisioning (for `terraform/build-ami/`):**
- `ec2:RunInstances`, `ec2:TerminateInstances`, `ec2:DescribeInstances`, `ec2:DescribeInstanceStatus`
- `ec2:CreateKeyPair`, `ec2:DeleteKeyPair`, `ec2:DescribeKeyPairs`
- `ec2:CreateSecurityGroup`, `ec2:DeleteSecurityGroup`, `ec2:AuthorizeSecurityGroupIngress`, `ec2:RevokeSecurityGroupIngress`, `ec2:DescribeSecurityGroups`
- `ec2:CreateVpc`, `ec2:DeleteVpc`, `ec2:DescribeVpcs`, `ec2:ModifyVpcAttribute`
- `ec2:CreateSubnet`, `ec2:DeleteSubnet`, `ec2:DescribeSubnets`, `ec2:ModifySubnetAttribute`
- `ec2:CreateInternetGateway`, `ec2:DeleteInternetGateway`, `ec2:AttachInternetGateway`, `ec2:DetachInternetGateway`, `ec2:DescribeInternetGateways`
- `ec2:CreateRouteTable`, `ec2:DeleteRouteTable`, `ec2:CreateRoute`, `ec2:DeleteRoute`, `ec2:AssociateRouteTable`, `ec2:DisassociateRouteTable`, `ec2:DescribeRouteTables`
- `ec2:CreateTags`, `ec2:DescribeTags`
- `ec2:DescribeAvailabilityZones`, `ec2:DescribeImages` (for AMI lookup in `data.tf`)

**EBS — snapshot upload (coldsnap via EBS Direct API):**
- `ebs:StartSnapshot`, `ebs:PutSnapshotBlock`, `ebs:CompleteSnapshot`
- `ebs:ListSnapshotBlocks`, `ebs:ListChangedBlocks`, `ebs:GetSnapshotBlock`
- `ec2:CreateSnapshot`, `ec2:DescribeSnapshots`, `ec2:DescribeSnapshotAttribute`

**AMI — registration:**
- `ec2:RegisterImage`, `ec2:DeregisterImage`, `ec2:DescribeImages`, `ec2:ModifyImageAttribute`

**IAM — pass-role (so Terraform can attach the EC2 instance profile):**
- `iam:PassRole` scoped to the `build-ami-instance-role` ARN
- `iam:CreateRole`, `iam:DeleteRole`, `iam:GetRole`, `iam:ListRolePolicies`, `iam:ListAttachedRolePolicies`
- `iam:CreatePolicy`, `iam:DeletePolicy`, `iam:GetPolicy`, `iam:GetPolicyVersion`, `iam:ListPolicyVersions`
- `iam:AttachRolePolicy`, `iam:DetachRolePolicy`
- `iam:CreateInstanceProfile`, `iam:DeleteInstanceProfile`, `iam:GetInstanceProfile`, `iam:AddRoleToInstanceProfile`, `iam:RemoveRoleFromInstanceProfile`

**STS — identity check (used by Terraform AWS provider and boto3):**
- `sts:GetCallerIdentity`

### Output

```hcl
output "role_arn" {
  description = "ARN of the IAM role to set as vars.AWS_ROLE_ARN in GitHub"
  value       = aws_iam_role.github_actions.arn
}
```

### Relationship to Existing Stacks

```
terraform/
├── build-ami/          # Invoked at runtime by scripts/build-ami.py
│                       # Creates the EC2 build instance; its IAM instance
│                       # profile is managed here, not by the CI role stack.
├── deploy/             # Separate deployment stack (unrelated)
└── github-actions-iam-role/   # NEW — one-time bootstrap; creates the role
                                # that the CI job assumes via OIDC
```

The `github-actions-iam-role` stack is applied once by a human operator with sufficient AWS permissions. It does not depend on `terraform/build-ami/` and does not share state with it.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

This feature is a GitHub Actions workflow configuration. Most acceptance criteria are structural (SMOKE) checks on the YAML. However, two areas involve logic that varies meaningfully with input and is worth property-based testing:

1. **The job condition expression** — a pure boolean function of trigger context that must correctly classify all trigger combinations.
2. **The summary generation script** — a shell/Python snippet that parses `ami_build_result.json` and formats output; correctness must hold for any valid JSON content.

Property-based testing library: **Hypothesis** (Python), consistent with the existing test suite in this repository.

---

### Property 1: Job condition correctly classifies all trigger contexts

*For any* GitHub Actions trigger context (event name, ref, and `enable_ssh` input), the `build-ami` job's `if:` condition expression SHALL evaluate to `true` if and only if the trigger is a push to `main` OR a `workflow_dispatch` with `enable_ssh == false`.

**Validates: Requirements 1.2, 1.3, 1.4, 1.5**

---

### Property 2: Summary script extracts all required fields from any valid build result

*For any* valid `ami_build_result.json` containing `ami_id`, `snapshot_id`, `region`, and `build_timestamp`, the summary generation step SHALL produce output that contains each of those four field values.

**Validates: Requirements 7.1**

---

### Property Reflection

- Property 1 subsumes requirements 1.2, 1.3, 1.4, and 1.5 — all four are cases of the same boolean expression evaluated over different inputs. A single property with a generator covering all trigger combinations is more comprehensive than four separate examples.
- Property 2 is independent of Property 1 and covers a different code path (shell/Python JSON parsing vs. YAML expression logic).
- No redundancy between the two properties.

## Error Handling

### Script Failure

`scripts/build-ami.py` exits non-zero on any error (attestation failure, Terraform failure, snapshot failure, etc.). The CI job propagates this as a job failure. The `if: success()` guard on the artifact upload step ensures the artifact is not uploaded on failure. The `if: failure()` guard on the failure summary step ensures a notice is written to the step summary.

### Infrastructure Cleanup

Terraform destroy runs inside the script's `finally` block unconditionally. If `terraform destroy` itself fails, the script logs the error but does not re-raise it (to avoid masking the original error). This means cleanup failures are visible in logs but do not change the job's exit code.

### Missing `vars.AWS_REGION`

The expression `${{ vars.AWS_REGION || 'us-east-1' }}` defaults to `us-east-1` when the variable is unset or empty. This is safe because `us-east-1` is a valid AWS region and the script validates the region format before use.

### Debug Artifact Gate

The script enforces a production gate: if the artifact has `debug=true` annotation and `--allow-debug` is not passed, the script exits non-zero before creating any AWS resources. Since the CI job never passes `--allow-debug`, debug artifacts are always rejected at the script level, providing defense-in-depth beyond the job-level `if:` condition.

## Testing Strategy

### Dual Testing Approach

Unit/property tests verify the logic components in isolation; integration tests (manual or in a staging environment) verify the end-to-end workflow.

### Property-Based Tests (Hypothesis)

**Property 1 — Job condition expression:**

```python
# Feature: ami-build-ci-job, Property 1: job condition correctly classifies all trigger contexts
@given(
    event_name=st.sampled_from(["push", "workflow_dispatch", "pull_request"]),
    ref=st.one_of(
        st.just("refs/heads/main"),
        st.just("refs/heads/develop"),
        st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")), min_size=1)
    ),
    enable_ssh=st.booleans(),
)
@settings(max_examples=200)
def test_build_ami_job_condition(event_name, ref, enable_ssh):
    result = evaluate_job_condition(event_name, ref, enable_ssh)
    expected = (ref == "refs/heads/main") or (event_name == "workflow_dispatch" and not enable_ssh)
    assert result == expected
```

The `evaluate_job_condition` function is a pure Python implementation of the YAML `if:` expression, extracted for testability.

**Property 2 — Summary script field extraction:**

```python
# Feature: ami-build-ci-job, Property 2: summary script extracts all required fields
@given(
    ami_id=st.from_regex(r"ami-[0-9a-f]{17}", fullmatch=True),
    snapshot_id=st.from_regex(r"snap-[0-9a-f]{17}", fullmatch=True),
    region=st.sampled_from(["us-east-1", "eu-west-1", "ap-southeast-2"]),
    build_timestamp=st.datetimes(timezones=st.just(timezone.utc)).map(lambda d: d.isoformat()),
)
@settings(max_examples=100)
def test_summary_generation(ami_id, snapshot_id, region, build_timestamp):
    build_result = {
        "ami_id": ami_id,
        "snapshot_id": snapshot_id,
        "region": region,
        "build_timestamp": build_timestamp,
        "pcr_measurements": {"pcr4": "aabbcc", "pcr7": "ddeeff"},
    }
    summary = generate_summary(build_result)
    assert ami_id in summary
    assert snapshot_id in summary
    assert region in summary
    assert build_timestamp in summary
```

### Unit Tests (Example-Based)

- Verify the `build-ami` job YAML block contains `needs: build-and-publish`.
- Verify `runs-on: ubuntu-24.04`.
- Verify `actions/checkout@v4` with `submodules: recursive` is the first step.
- Verify `aws-actions/configure-aws-credentials` uses `role-to-assume: ${{ vars.AWS_ROLE_ARN }}`.
- Verify `hashicorp/setup-terraform` is present with a pinned `terraform_version`.
- Verify the script invocation includes `--output-file ami_build_result.json` and `--expected-workflow .github/workflows/build-attestable-image.yml`.
- Verify `--allow-debug` is absent from the script invocation.
- Verify the upload step uses `if: success()`, artifact name `ami-build-result`, and `retention-days: 90`.
- Verify the failure summary step uses `if: failure()`.
- Verify job permissions: `id-token: write`, `contents: read`, `packages: read`; and that `attestations: write` and `packages: write` are absent.
- Verify `aws-access-key-id` and `aws-secret-access-key` are absent from the job.

### Integration Tests

End-to-end validation requires a real GitHub Actions run with AWS credentials configured. These are not automated in the unit test suite:

- Trigger a push to `main` and verify the `build-ami` job runs and produces the `ami-build-result` artifact.
- Trigger a `workflow_dispatch` with `enable_ssh: true` and verify `build-ami` is skipped.
- Verify the step summary contains the AMI ID after a successful run.
- Verify EC2 infrastructure is cleaned up after both success and failure runs.
