# ami-build — Design Rationale

> Imported from `.kiro/specs/ami-build-ci-job/design.md` and `.kiro/specs/github-actions-remote-executor/design.md` (PART 2: Build Design — AMI conversion phase; Security Hardening Components). Captures the *why* behind `spec.md`; not normative.

## Overview

Phase 2 of the build pipeline converts the published, attested KIWI artifact into an AWS AMI. `scripts/build-ami.py` (AMI_Converter) provisions a temporary EC2 build instance via Terraform, verifies the artifact's GitHub attestation, uploads the raw disk image to an EBS snapshot via coldsnap, and registers an AMI with TPM 2.0 / UEFI support. The `build-ami` CI job drives the script after `build-flavor-image`. The design is intentionally narrow: the CI job is a YAML job block, not a new application — the script already exists.

## Key decisions & trade-offs

- **Digest-pinned, cryptographically bound conversion.** Production builds **require** an `@sha256:` digest in the artifact reference. The *same* digest is used for both attestation verification and the `oras pull`, so the verified artifact and the pulled artifact are provably identical — a mutable tag could otherwise point at different content between verify and pull. The reference is validated against a strict allowlist (rejecting shell metacharacters) before any remote command runs.
- **Fail-closed signature verification.** `gh attestation verify` runs offline against the downloaded bundle; the converter never proceeds with an untrusted artifact under any circumstance. Optional `--expected-workflow` additionally checks the producing-workflow identity from the certificate's SubjectAlternativeName — which is populated from GitHub's OIDC token and **cannot be forged** by the producing workflow. That JSON path runs *without* `GH_FORCE_TTY` (ANSI codes break `jq`); a separate human-readable run uses `GH_FORCE_TTY=1` for logs.
- **Shell-injection-safe artifact handling.** `.raw` files are enumerated programmatically (not `ls *.raw`), exactly one is required, the basename is validated against `^[a-zA-Z0-9][a-zA-Z0-9._-]*\.raw$`, and `shlex.quote()` / subprocess list args are used — regression tests confirm metacharacter filenames are rejected before any shell command is built.
- **Tool-chain trust is explicit.** Rust is installed from the official standalone tarball after GPG-verifying its detached signature (key `85AB96E6FA1BE5FE`); ORAS is SHA-256-verified and version-matched to the build workflow; coldsnap is built from a pinned tag. Trust assumptions for the Rust signing key and GitHub CLI are documented in code comments.
- **AMI registered for attestability.** `BootMode=uefi` + `TpmSupport=v2.0` are what make NitroTPM auto-enable on instances launched from the AMI later (`deployment` relies on this). `EnaSupport`, `x86_64`, and `/dev/xvda` round out a standard Nitro config.
- **Guaranteed cleanup.** Terraform destroy, SSH close, and secure key deletion (overwrite with random bytes, then unlink) run in a `finally` block so a failed conversion never leaks a running instance or key material; cleanup errors are logged but don't mask the original failure or change the exit code. Terraform state is documented as containing sensitive SSH key material.
- **Two IAM boundaries, least privilege.** The **CI role** (assumed via OIDC, scoped to `repo:owner/github-runner-ec2-attestation:*`) only manages the build infrastructure lifecycle and the few boto3 calls the runner makes (`RegisterImage`, snapshot waiter, identity). The privileged EBS-Direct/`coldsnap upload` work runs **on the build instance** under its *own* instance role — so the CI credentials never carry image-write power. `iam:PassRole` is scoped to exactly `build-ami-instance-role`. The Build_Instance IAM policy further scopes EC2/EBS to the build region via resource ARN patterns + `aws:RequestedRegion`, never `Resource = "*"` for snapshot/image ops, limiting blast radius. (Account-level ARN scoping isn't used for snapshots/images because their ARNs use the account-less `::` form.)

## CI job design

- **Trigger.** `needs: build-flavor-image`; the `if:` is a pure boolean — runs on push to `main` or any `workflow_dispatch` (either `enable_ssh` value), skipped on `develop`. `enable_ssh` no longer gates whether the job runs — only whether `--allow-debug` is passed and a debug warning is appended.
- **Step ordering** is dependency-driven: checkout → OIDC creds → setup-terraform → `uv sync` → run script → (on success) upload artifact + summary, (debug) warning, (failure) failure notice. No separate cleanup step — destroy lives in the script's `finally`.
- **Region consistency.** `${{ vars.AWS_REGION || 'us-east-1' }}` must be identical in `configure-aws-credentials` and `--region`; the script validates region format, so the default is safe.

## IAM role Terraform stack

`terraform/github-actions-iam-role/` is a one-time human-applied bootstrap (plain `.tf`, no modules) that creates the OIDC-assumable CI role and outputs `role_arn` for `vars.AWS_ROLE_ARN`. It optionally creates the OIDC provider (`create_oidc_provider`, default true), is parameterized by region/org/repo for forks, and does not share state with `terraform/build-ami/`. The subject wildcard `repo:<org>/<repo>:*` can be narrowed (e.g. to `main`) by operators wanting tighter scoping.

## Data models (shapes)

`ami_build_result.json`: `ami_id`, `snapshot_id`, `region`, `build_timestamp` (ISO 8601), and `pcr_measurements` (`pcr4`/`pcr7`). Uploaded as the `ami-build-result` workflow artifact (90-day retention) and consumed by `deployment` and `cleanup`.
