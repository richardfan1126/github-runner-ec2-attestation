# deployment — Design Rationale

> Imported from `.kiro/specs/github-actions-remote-executor/design.md` (PART 3: Deployment Design; PART 5: Debug Design — deploy-time). Captures the *why* behind `spec.md`; not normative.

## Overview

Deployment launches the attestable AMI as a **persistent** Target_Instance inside an isolated VPC, exposing only the attestation API on port 8080 to the world. `scripts/deploy.py` orchestrates: load AMI build result → `terraform init`/`apply` (`terraform/deploy/`) → extract outputs → persist `infrastructure_state.json`.

## Key decisions & trade-offs

- **HTTP-only, open to the world, authenticated at the app layer.** Port 8080 is open to `0.0.0.0/0` because any authorized GitHub Actions workflow must be able to reach it; there is **no** SSH by default. Security lives in the application: OIDC token validation + PQ-hybrid encryption. So network ACLs aren't the trust boundary — the attestation/auth layer is. No port other than 8080 is open by default.
- **NitroTPM comes for free from the AMI.** The instance needs no explicit TPM Terraform config: the AMI was registered with `TpmSupport=v2.0` + `BootMode=uefi` (see `ami-build`), so NitroTPM auto-enables on launch. This keeps the deploy stack simple and ties attestation capability to the verified image rather than to deploy-time configuration.
- **IMDSv2 required.** `http_tokens=required` disables IMDSv1, and `http_put_response_hop_limit=1` prevents a container from forwarding the metadata token — reducing SSRF/credential-theft exposure from inside execution containers.
- **Required AMI, sensible instance default.** `attestable_ami_id` is mandatory (no default) so a deploy can't silently launch the wrong image; `instance_type` defaults to `c5.9xlarge`. GPU families (G4dn, G5, G6, G6e, P5) are explicitly supported because they carry **both** NitroTPM and NVIDIA GPUs, enabling attestable GPU workloads.
- **Isolated from the build VPC.** Deploy uses `10.0.0.0/16` vs the build stack's `10.2.0.0/16`, has no IAM instance profile and no SSH key pair (unless debug), and is persistent rather than ephemeral — a clean separation between "convert an image" and "run the service".
- **State persistence + cleanup advice.** Outputs (`vpc_id`, `subnet_id`, `security_group_id`, `instance_id`, `instance_public_ip`, `attestation_api_url`) are written to `infrastructure_state.json` for downstream use; on any failure the script logs advice to run `terraform destroy` so partial resources aren't orphaned. Errors map to `FileNotFoundError`/`RuntimeError` with descriptive messages, dual-logged to stdout and `deploy.log`.

## Debug SSH (deploy-time)

Opt-in and two-phase (the image must also have been built with SSH — see `image-build`). `--enable-ssh` requires `--key-pair-name`; the script auto-detects the operator IP via `checkip.amazonaws.com` and passes `allowed_ssh_cidr={ip}/32`, so the port-22 ingress rule is whitelisted to the deployer only (HTTP 8080 stays world-open — auth is at the app layer). A prominent warning is logged and `ssh_enabled` is recorded in the infrastructure state. With SSH disabled, no key pair is attached and the security group has no port-22 rule at all.

## Data models (shapes)

Input: `ami_build_result.json` (`ami_id`, `snapshot_id`, `region`). Output: `infrastructure_state.json` (the six Terraform outputs plus `ssh_enabled`). Terraform variables: `attestable_ami_id` (required), `instance_type`, `aws_region`, and the debug trio `enable_ssh`/`key_pair_name`/`allowed_ssh_cidr`.
