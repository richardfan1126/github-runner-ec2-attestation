# terraform/github-actions-iam-role

One-time bootstrap stack that creates the AWS IAM role assumed by the `build-ami`
GitHub Actions CI job via OIDC. Apply this stack once per AWS account; you do not
need to re-apply it on every CI run.

## What it creates

| Resource | Name |
|---|---|
| `aws_iam_openid_connect_provider` | `token.actions.githubusercontent.com` (conditional) |
| `aws_iam_role` | `github-actions-ami-builder` |
| `aws_iam_policy` | `github-actions-ami-builder-policy` |
| `aws_iam_role_policy_attachment` | attaches the policy to the role |

## Input variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `aws_region` | `string` | `"us-east-1"` | AWS region to deploy into |
| `github_org` | `string` | *(required)* | GitHub organisation or user name that owns the repository (e.g. `my-org`) |
| `github_repo` | `string` | `"github-runner-ec2-attestation"` | GitHub repository name |
| `create_oidc_provider` | `bool` | `true` | Whether to create the GitHub Actions OIDC provider. Set to `false` if the provider already exists in this AWS account (each account can only have one). |

## Prerequisites

- AWS credentials with sufficient permissions to create IAM roles, policies, and
  (optionally) an OIDC provider.
- Terraform ≥ 1.0 installed locally.

## Bootstrap

```bash
terraform init
terraform apply -var="github_org=<your-org>"
```

To deploy into a non-default region:

```bash
terraform apply -var="github_org=<your-org>" -var="aws_region=eu-west-1"
```

If the GitHub Actions OIDC provider already exists in your account (e.g. another
stack created it), skip provider creation to avoid a conflict:

```bash
terraform apply -var="github_org=<your-org>" -var="create_oidc_provider=false"
```

## Setting `vars.AWS_ROLE_ARN` in GitHub

After `terraform apply` completes, copy the `role_arn` output value:

```
Outputs:

role_arn = "arn:aws:iam::123456789012:role/github-actions-ami-builder"
```

Then add it as a repository variable in GitHub:

1. Go to **Settings → Secrets and variables → Actions → Variables**.
2. Click **New repository variable**.
3. Name: `AWS_ROLE_ARN`
4. Value: the ARN from the Terraform output.

The `build-ami` CI job reads this variable via `vars.AWS_ROLE_ARN` to know which
role to assume when requesting AWS credentials.

## Tearing down

```bash
terraform destroy -var="github_org=<your-org>"
```

> **Note:** destroying the stack removes the IAM role. The `build-ami` CI job will
> fail until a new role is created and `vars.AWS_ROLE_ARN` is updated.
