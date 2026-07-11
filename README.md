# GitHub Actions Remote Executor

HTTP server for executing GitHub Actions scripts with NitroTPM attestation and post-quantum hybrid encryption.

## Overview

The GitHub Actions Remote Executor runs on an Attestable EC2 instance with NitroTPM, providing a secure and attestable environment for executing scripts from GitHub repositories. The system generates cryptographic attestation documents proving the execution environment, executes scripts asynchronously inside Docker containers, and encrypts all request/response payloads using a post-quantum hybrid key exchange (X25519 + ML-KEM-768) with AES-256-GCM.

## Requirements

- Python 3.11+
- Attestable EC2 instance with NitroTPM
- Docker daemon (for container-based script execution)
- GitHub personal access token
- GitHub Actions OIDC token (for authenticating workflow requests)

## Flavor Model

Each AMI variant is called a **flavor**. Flavors share a common base image but carry their own execution container image and authorization policy. The `flavors/` directory is the manifest — every subdirectory (except `default`) is a buildable flavor, and `flavors.lock` records the current deployed state of all flavors.

### Directory layout

```
flavors/
  default/
    env             # Shared bucket-② values (ports, timeouts, resource limits)
  <flavor>/
    Dockerfile      # Execution container image definition
    env             # Per-flavor deltas: ALLOWED_REPOSITORIES, EXPECTED_AUDIENCE, overrides
    [extra files]   # Anything else needed by the Dockerfile
flavors.lock        # Durable record: per-flavor {image digest, PCR4, AMI id, producing commit}
```

### Merge precedence

The effective configuration baked into each AMI is produced by a fixed-precedence overlay:

```
code defaults (bucket ①)
  ◀── flavors/default/env   (shared declared values — bucket ②)
  ◀── flavors/<f>/env       (per-flavor deltas — bucket ②)
  ◀── pipeline inject       (CONTAINER_IMAGE + CONTAINER_IMAGE_DIGEST — bucket ③)
      = effective env  →  baked into dm-verity-sealed root  →  PCR4-bound
```

- **Bucket ①** — security-hardened code defaults (`CONTAINER_USER=65534:65534`, `NO_NEW_PRIVILEGES=true`, `CONTAINER_NETWORK_MODE=none`, etc.). Any deviation is a *relaxation* and is surfaced non-silently in the build summary and recorded in `flavors.lock`.
- **Bucket ②** — declared configuration values that live in committed env files. `flavors/default/env` holds shared values inherited by all flavors; `flavors/<f>/env` holds per-flavor overrides and the required authorization keys (`ALLOWED_REPOSITORIES`, `EXPECTED_AUDIENCE`).
- **Bucket ③** — pipeline outputs (`CONTAINER_IMAGE`, `CONTAINER_IMAGE_DIGEST`) injected after the flavor's Dockerfile has been built and pushed. These must **never** appear in a committed env file.

The build-time pre-bake validator (`validate_env.py`) enforces this: it rejects any committed env file that contains a bucket-③ key or an unrecognized key (typo guard).

### Deny-all by design

A flavor that omits `ALLOWED_REPOSITORIES` or `EXPECTED_AUDIENCE` fails the build-time config-resolution gate before any AMI is registered. An executor started without at least one authorized repository in its allowlist refuses to start. There is no way to ship an AMI that accepts arbitrary callers.

### Adding a new flavor

1. Create `flavors/<name>/Dockerfile` — the execution container image. It must run as a non-root user (`65534` recommended), have required tools on `PATH`, and use pinned base images.
2. Create `flavors/<name>/env` with at minimum:
   ```ini
   ALLOWED_REPOSITORIES=owner/repo1,owner/repo2
   EXPECTED_AUDIENCE=<expected-aud-claim>
   ```
   Add any resource or security overrides here. Do **not** set `CONTAINER_IMAGE` or `CONTAINER_IMAGE_DIGEST` — those are injected by the pipeline.
3. Push. The `detect-changes` CI job automatically detects the new flavor directory and schedules an image-level build for it.

### flavors.lock

After each successful AMI build the pipeline commits `flavors.lock`, a JSON file recording the current state of every flavor:

```json
{
  "rust-build": {
    "container_image_digest": "sha256:...",
    "pcr4": "<hex>",
    "ami_id": "ami-...",
    "producing_commit": "<sha>",
    "relaxations": {
      "container_network_mode": "bridge",
      "container_tmpfs_exec": true,
      "container_tmpfs_size": "2g"
    }
  }
}
```

For the `rust-build` flavor above, `relaxations` lists the bucket-① fields it overrides (a `bridge` network for the GHCR push, plus a `2g` exec-enabled tmpfs scratch for the Rust build). An empty `relaxations: {}` means the flavor carries the full hardened posture. A verifier reads these to see exactly which security defaults were relaxed without opening the env files.

## Configuration

All configuration is done through environment variables. The effective set of variables baked into each AMI flavor is produced by the merge described above (shared `flavors/default/env` ◀ per-flavor `flavors/<f>/env` ◀ pipeline-injected bucket ③).

- `SERVER_PORT`: HTTP server listening port (default: 8080)
- `MAX_CONCURRENT_EXECUTIONS`: Maximum concurrent script executions (default: 10)
- `EXECUTION_TIMEOUT_SECONDS`: Script execution timeout (default: 300)
- `MAX_SCRIPT_SIZE_BYTES`: Maximum script file size (default: 1048576)
- `RATE_LIMIT_PER_IP`: Rate limit per IP address (default: 100)
- `RATE_LIMIT_WINDOW_SECONDS`: Rate limit window (default: 60)
- `TEMP_STORAGE_PATH`: Temporary file storage location (default: /tmp/gha-executor)
- `OUTPUT_RETENTION_HOURS`: Output retention period (default: 24)
- `TPM_ATTEST_PATH`: NitroTPM attestation tool path (default: /usr/bin/nitro-tpm-attest)
- `ALLOWED_REPOSITORIES`: Comma-separated list of GitHub repositories authorized to execute scripts (e.g., `owner/repo1,owner/repo2`)
- `EXPECTED_AUDIENCE`: Expected `aud` claim in OIDC tokens, used to ensure tokens were issued for this Remote Executor instance (e.g., `https://your-remote-executor.example.com`)
- `CONTAINER_IMAGE`: Docker image used for script execution (e.g., `ubuntu:24.04`)
- `CONTAINER_IMAGE_DIGEST`: **Required.** SHA-256 digest to pin the container image (e.g., `sha256:abc123...`). The server refuses to start if this is not set and the `CONTAINER_IMAGE` reference does not contain `@sha256:`. This prevents tag drift and ensures the server always runs the exact expected image.
- `CONTAINER_MEMORY_LIMIT`: Memory limit for execution containers (e.g., `4g`)
- `CONTAINER_CPU_LIMIT`: CPU limit for execution containers (e.g., `1.0`)
- `MAX_OUTPUT_SIZE_BYTES`: Maximum buffered output size per execution in bytes (default: 10485760 = 10MB)
- `NONCE_CACHE_TTL_SECONDS`: TTL for the anti-replay nonce cache in seconds (default: 300)
- `MAX_CONTAINER_PIDS`: PID limit for execution containers to prevent fork bombs (default: 256)
- `MAX_OUTPUT_ATTESTATIONS_PER_WINDOW`: Maximum output attestation generations per execution within the rate-limit window (default: 10)
- `OUTPUT_ATTESTATION_WINDOW_SECONDS`: Sliding window duration for output attestation rate limiting (default: 60)
- `ALLOW_NO_TPM`: When set to `true`, allows the server to start without a functioning NitroTPM device. **For development/testing only** — do not use in production (default: `false`)
- `MAX_REQUEST_BODY_BYTES`: Maximum raw request body size in bytes (default: 1048576 = 1MB)
- `MAX_ENCRYPTED_PAYLOAD_BYTES`: Maximum encrypted payload size in bytes (default: 524288 = 512KB)
- `MAX_DECRYPTED_PAYLOAD_BYTES`: Maximum decrypted payload size in bytes (default: 262144 = 256KB)
- `SCRIPT_ENV_DENY_LIST`: Comma-separated list of environment variable names (or prefix patterns ending with `*`) that are rejected in `script_env`. Default deny-list includes security-sensitive variables like `PATH`, `LD_PRELOAD`, `BASH_ENV`, etc.
- `CLEANUP_INTERVAL_SECONDS`: Interval in seconds for periodic cleanup of expired executions and stale rate-limiter entries (default: 60)
- `ALLOWED_BRANCHES`: Optional comma-separated list of glob patterns restricting which branches may execute scripts (e.g., `main,release/*`). When unset, any branch in an allowed repository is accepted.
- `REQUIRE_PROTECTED_REF`: When set to `true`, only OIDC tokens from protected refs are accepted (default: `false`)

## Attestable EC2 Deployment

This application requires an Attestable EC2 instance with NitroTPM for attestation capabilities. Supported instance types include:
- C5, C5a, C5n, C6i, C6a, C7g
- M5, M5a, M5n, M6i, M6a, M7g
- R5, R5a, R5n, R6i, R6a, R7g
- And other NitroTPM-compatible instances

## Building the AMI

The attestable AMI is built in two phases: first a KIWI disk image is built and published via GitHub Actions, then a local script converts it to an AMI.

### Prerequisites

- AWS credentials configured (`aws configure` or environment variables)
- [Terraform](https://developer.hashicorp.com/terraform/install) installed
- Python 3.11+ with [uv](https://astral.sh/uv) installed
- Build script dependencies (boto3, paramiko) are managed via `scripts/pyproject.toml`

### Step 1: Build the KIWI Image

The GitHub Actions workflow builds the KIWI image in a reproducible Docker environment, publishes it to GHCR with PCR measurement annotations, and generates a Sigstore attestation.

The workflow triggers on:
- Push to `main` or `develop` branches
- Manual trigger via `workflow_dispatch` (with optional `enable_ssh` flag for debug images)

After the workflow completes, find the artifact reference in the workflow run summary. It will look like:

```
ghcr.io/<owner>/<repo>/attestable-image:<branch>-<timestamp>-<short-sha>
```

The summary also includes the artifact digest and PCR measurement values (PCR4, PCR7).

### Step 2: Convert to AMI

The AMI build can run automatically via CI or manually from your workstation.

#### Option A: Automatic (CI)

On every push to `main` (and on non-debug `workflow_dispatch` runs), the `build-ami` CI job runs automatically after the KIWI image is published. It calls `scripts/build-ami.py` with `--expected-workflow .github/workflows/build-attestable-image.yml` to enforce provenance, and uploads `ami_build_result.json` as a workflow artifact.

**One-time IAM setup required.** The CI job assumes an AWS IAM role via OIDC. Bootstrap it once per AWS account:

```bash
cd terraform/github-actions-iam-role
terraform init
terraform apply -var="github_org=<your-org>"
```

Variables:

| Variable | Required | Default | Description |
|---|---|---|---|
| `github_org` | Yes | — | GitHub organisation or user name that owns the repository |
| `github_repo` | No | `github-runner-ec2-attestation` | GitHub repository name |
| `aws_region` | No | `us-east-1` | AWS region to deploy into |
| `create_oidc_provider` | No | `true` | Set to `false` if the GitHub Actions OIDC provider already exists in your account |

After `terraform apply`, copy the `role_arn` output and add it as a repository variable in GitHub (**Settings → Secrets and variables → Actions → Variables**, name: `AWS_ROLE_ARN`). Optionally set `AWS_REGION` the same way to target a non-default region.

#### Option B: Manual

Run the build script locally to convert the KIWI image into an AMI:

```bash
uv run --project scripts python scripts/build-ami.py \
  --artifact-ref ghcr.io/<owner>/<repo>/attestable-image:<tag>@sha256:<digest>
```

CLI arguments:

| Argument | Required | Default | Description |
|---|---|---|---|
| `--artifact-ref` | Yes | — | GHCR artifact reference with a `@sha256:` digest pin (e.g., `ghcr.io/owner/repo/attestable-image:tag@sha256:abcdef...` or `ghcr.io/owner/repo/attestable-image@sha256:abcdef...`). Tag-only references without a digest are rejected. |
| `--region` | No | `us-east-1` | AWS region for AMI creation |
| `--instance-type` | No | `c5.9xlarge` | EC2 instance type for the temporary build instance |
| `--output-file` | No | `ami_build_result.json` | Path for the JSON build result |
| `--allow-debug` | No | `false` | Allow building an AMI from a debug (SSH-enabled) artifact. Without this flag, debug artifacts are rejected. |
| `--expected-workflow` | No | — | Expected workflow file path for provenance verification (e.g., `.github/workflows/build-attestable-image.yml`). When provided, the attestation workflow identity is verified against this path. |

The script performs the following steps:

1. Provisions a temporary EC2 instance via Terraform (VPC, subnet, security group, IAM role)
2. Installs required tools on the instance (ORAS, GitHub CLI, Rust, coldsnap)
3. Verifies the artifact's GitHub attestation signature
4. Downloads the artifact from GHCR
5. Uploads the raw disk image as an EBS snapshot via coldsnap
6. Registers the snapshot as an AMI (UEFI boot, TPM 2.0, ENA support)
7. Cleans up all temporary infrastructure via `terraform destroy`

### Build Output

On success, the script writes `ami_build_result.json` (or the path specified by `--output-file`) for each flavor:

```json
{
  "ami_id": "ami-0123456789abcdef0",
  "snapshot_id": "snap-0123456789abcdef0",
  "region": "us-east-1",
  "build_timestamp": "2026-03-25T12:00:00+00:00",
  "pcr_measurements": {
    "pcr4": "<hex>",
    "pcr7": "<hex>"
  },
  "verifier_record": {
    "container_image_digest": "sha256:...",
    "pcr4": "<hex>",
    "ami_id": "ami-...",
    "producing_commit": "<sha>",
    "relaxations": {}
  }
}
```

The CI pipeline also commits an updated `flavors.lock` to the repository after all flavors are built, recording the durable deployed state of every flavor.

## Deploying the Instance

Once you have a built AMI (from the build step above), deploy it as a running EC2 instance with full network infrastructure.

### Prerequisites

- AMI build result file (`ami_build_result.json`) from the build step
- [Terraform](https://developer.hashicorp.com/terraform/install) installed
- AWS credentials configured (`aws configure` or environment variables)
- Python 3.11+ with [uv](https://astral.sh/uv) installed
- Script dependencies (boto3, paramiko) managed via `scripts/pyproject.toml`

### Running the Deployment

```bash
uv run --project scripts python scripts/deploy.py \
  --ami-build-result ami_build_result.json \
  --instance-type c5.9xlarge \
  --output-file infrastructure_state.json
```

CLI arguments:

| Argument | Required | Default | Description |
|---|---|---|---|
| `--ami-build-result` | No | `ami_build_result.json` | Path to the AMI build result JSON from the build step |
| `--instance-type` | No | `c5.9xlarge` | EC2 instance type (must be NitroTPM-compatible) |
| `--output-file` | No | `infrastructure_state.json` | Path for the infrastructure state output |
| `--enable-ssh` | No | `false` | Enable SSH debug access (requires `--key-pair-name`) |
| `--key-pair-name` | No | — | EC2 key pair name for SSH access (required when `--enable-ssh` is set) |

### Deployment Flow

1. Loads the AMI build result file to read the AMI ID and region
2. If `--enable-ssh` is set, detects your public IP via `checkip.amazonaws.com` for SSH security group whitelisting
3. Runs `terraform init` in `terraform/deploy/`
4. Runs `terraform apply` with the AMI ID, instance type, SSH settings, and region
5. Saves Terraform outputs to the infrastructure state file

The Terraform configuration provisions:
- A VPC (`10.0.0.0/16`) with DNS support
- A public subnet (`10.0.1.0/24`) with auto-assign public IP
- An Internet Gateway and route table
- A security group allowing HTTP on port 8080 (and optionally SSH on port 22 from your IP)
- An EC2 instance from the attestable AMI with IMDSv2 required

### Deployment Output

On success, the script writes `infrastructure_state.json`:

```json
{
  "vpc_id": "vpc-0123456789abcdef0",
  "subnet_id": "subnet-0123456789abcdef0",
  "security_group_id": "sg-0123456789abcdef0",
  "instance_id": "i-0123456789abcdef0",
  "instance_public_ip": "203.0.113.42",
  "attestation_api_url": "http://203.0.113.42:8080",
  "ssh_enabled": false
}
```

You can then reach the Remote Executor API at the `attestation_api_url`.

## Encryption

All protected endpoints use end-to-end encryption based on a post-quantum hybrid key encapsulation mechanism (X25519 + ML-KEM-768).

The client flow is:

1. Call `GET /attest` to retrieve the server's attestation document and composite public key.
2. Verify the attestation document against the NitroTPM root of trust. Confirm the SHA-256 fingerprint of the returned `server_public_key` matches the `public_key` field inside the attestation.
3. Perform a hybrid KEM encapsulation against the server's composite public key to derive a shared key and a client public key blob.
4. Encrypt the request payload with AES-256-GCM using the shared key, then send `{ "encrypted_payload": "<base64>", "client_public_key": "<base64>" }` to `POST /execute`.
5. The server decapsulates, derives the same shared key, decrypts the payload, and returns `{ "encrypted_response": "<base64>" }` encrypted with the shared key.
6. For subsequent `POST /execution/{id}/output` calls, the server looks up the shared key by execution ID. Send `{ "encrypted_payload": "<base64>" }` encrypted with the same shared key.

## Attestation Claims

The NitroTPM `user_data` field is hard-capped at 1024 bytes, so `/execute` and `/execution/{id}/output` attestations carry a compact, fixed-shape **envelope** in `user_data` rather than the execution/output details themselves:

```json
{
  "v": 1,
  "claims_digest": "sha256:<hex>",
  "timestamp": "2025-01-01T00:00:00+00:00",
  "execution_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

`v` is the envelope-format version; `execution_id` and `timestamp` are signed but secondary (correlation and staleness only — see [Freshness](#freshness) below). The full, variable-length details live in a separate **claims document**, transmitted alongside the attestation as a base64-encoded opaque field, `claims_raw`, and bound to the envelope by `claims_digest`. This generalizes the same digest-and-preimage idiom already used for `server_public_key` (fingerprint in the attestation, full key delivered alongside) and `script_env_hash` (digest of an external preimage).

An execution claims document looks like:

```json
{
  "schema_version": "1.0",
  "repository_url": "https://github.com/owner/repo",
  "commit_hash": "abc123...",
  "script_path": "scripts/build.sh",
  "script_env_hash": "sha256-hex-of-canonicalized-script_env",
  "container_user": "65534:65534",
  "container_allow_root": false,
  "container_cap_add": ["CHOWN", "DAC_OVERRIDE", "..."],
  "no_new_privileges": true,
  "container_read_only_rootfs": true,
  "container_tmpfs_size": "256m",
  "container_tmpfs_exec": false,
  "workspace_mount_mode": "ro",
  "container_network_mode": "none",
  "gpu": { "enabled": false }
}
```

An output claims document replaces the execution-specific fields with `output_digest`, a `sha256:`-prefixed digest of the canonical JSON object `{ "stdout": ..., "stderr": ..., "exit_code": ... }` (keys sorted, no whitespace, `exit_code` a JSON number) — not a delimiter-glued string, so a `stdout`/`stderr` value that happens to contain `stdout:`/`stderr:`/`exit_code:`-like text can never be forged into colliding with a different genuine output.

### Verifying the binding

A verifier MUST perform these steps, in order, before trusting any claim field — this mirrors the existing `server_public_key` fingerprint check (hash raw bytes, transmit base64, decode-then-hash to verify):

1. Base64-decode `claims_raw` to recover `claims_bytes`.
2. Compute `sha256(claims_bytes)` and compare against `claims_digest` from the signed `user_data` envelope (after stripping the `sha256:` prefix). Reject if they don't match, or if the digest carries an algorithm prefix other than `sha256:`.
3. Parse `claims_bytes` as JSON and check `schema_version`. Reject if the MAJOR component is not one this verifier understands. A higher, known-MAJOR MINOR is fine — read the fields you recognize and **ignore unknown fields**; do not reject on their presence.
4. Only after steps 2–3 pass, read claim fields.

If `claims_raw` is missing, or the recomputed digest does not match `claims_digest`, the verifier MUST read **no** claim fields and reject the attestation — stripping or altering the preimage must never yield a trusted-but-empty read. `claims_raw` always travels in the same (sealed) response body as its attestation, so this check never requires an extra round trip.

### GPU claims

The `gpu` block reports GPU identity collected via NVML from the PCR4-measured NVIDIA driver at attestation time:

```json
{
  "enabled": true,
  "visible_devices": "all",
  "devices": [
    {
      "uuid": "GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "name": "NVIDIA A10G",
      "driver_version": "550.90.07",
      "cuda_version": "12.4",
      "vbios_version": "94.02.00.00.02",
      "compute_capability": "8.6",
      "memory_total_mib": 23028
    }
  ]
}
```

When `ENABLE_GPU` is false or unset, the block is exactly `{ "enabled": false }`. This is a **measured-driver self-report, not hardware attestation** — the driver is measured (PCR4-bound via the dm-verity roothash in the UKI cmdline), but genuine silicon/firmware attestation would require NVIDIA's separate root of trust (NRAS / confidential-compute mode), which this does not provide; a reserved, currently-unpopulated `gpu.attestation.report_digest` slot is left for that in the future. The block also only asserts device *availability to* the workload, not proof the computation executed on it. NitroTPM attestation itself only exists on specific virtualized accelerated instance types (G4dn, G5, G6, G6e/G6f, Gr6, G7/G7e, P5/P5e/P5en, P6-B200/B300, P6e-GB200) — it is **not** available on P4d/P4de (A100), P3 (V100), or any bare-metal instance, so a `gpu` block (or its absence) must never be read as implying support for an unattestable accelerator.

### Freshness

`timestamp` and `execution_id` in the envelope are secondary signals (staleness bound and correlation) — the server chooses both, so a replayer can re-present them unchanged. The actual anti-replay mechanism is the mandatory per-request client `nonce`, which the server validates and rejects on duplicate, and which is bound into the attestation's native COSE `nonce` field (outside `user_data`, unaffected by the envelope described above).

## API Endpoints

All endpoints are rate-limited per source IP (configurable via `RATE_LIMIT_PER_IP` and `RATE_LIMIT_WINDOW_SECONDS`). Rate limit headers are included on every response:

| Header | Description |
|---|---|
| `X-RateLimit-Limit` | Maximum requests allowed in the window |
| `X-RateLimit-Remaining` | Requests remaining in the current window |
| `X-RateLimit-Window` | Window duration in seconds |

When the limit is exceeded the server returns `429 Too Many Requests` with a `retry_after_seconds` hint.

All error responses share a consistent structure:

```json
{
  "error": "error_code",
  "message": "Human-readable description",
  "details": {}
}
```

---

### GET /attest

Returns a NitroTPM attestation document and the server's composite public key for establishing encrypted communication. The attestation document's `public_key` field contains the SHA-256 fingerprint of the composite key (the full key exceeds the 1024-byte attestation field limit). No authentication required.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `nonce` | string | null | Client-provided nonce for attestation freshness |

**Response (200):**

```json
{
  "attestation_document": "base64-encoded-cbor",
  "server_public_key": "base64-encoded-composite-key"
}
```

| Field | Type | Description |
|---|---|---|
| `attestation_document` | string | Base64-encoded CBOR attestation document from NitroTPM |
| `server_public_key` | string | Base64-encoded composite public key (X25519 + ML-KEM-768) for hybrid KEM |

**Error responses:**

| Status | Error code | Cause |
|---|---|---|
| 500 | `attestation_failed` | NitroTPM attestation generation failed |

---

### POST /execute

Fetches a script from a GitHub repository at a specific commit, generates a NitroTPM attestation document binding the request parameters to the execution environment, and starts asynchronous execution inside a Docker container. The response is returned immediately without waiting for the script to finish.

All request and response payloads are encrypted (see [Encryption](#encryption)).

**Request body (encrypted envelope):**

```json
{
  "encrypted_payload": "base64-encoded-ciphertext",
  "client_public_key": "base64-encoded-client-public-key"
}
```

**Decrypted payload fields:**

| Field | Type | Required | Description |
|---|---|---|---|
| `repository_url` | string | Yes | GitHub repository URL (e.g. `https://github.com/owner/repo`) |
| `commit_hash` | string | Yes | Full 40-character hex SHA of the commit (must match the OIDC token's `sha` claim) |
| `script_path` | string | Yes | Path to the script inside the repository (no path traversal) |
| `github_token` | string | Yes | GitHub personal access token for repository access |
| `oidc_token` | string | Yes | GitHub Actions OIDC token for authentication |
| `nonce` | string | Yes | Client-provided nonce included in the attestation document. Must be 16–256 characters, URL-safe characters only (`[a-zA-Z0-9._~-]`) |
| `script_env` | object | No | Dictionary of environment variables to pass to the script execution container. Keys matching the deny-list (`SCRIPT_ENV_DENY_LIST`) are rejected. |

**Success response (200, encrypted):**

```json
{
  "encrypted_response": "base64-encoded-ciphertext"
}
```

Decrypted response:

```json
{
  "execution_id": "550e8400-e29b-41d4-a716-446655440000",
  "attestation_document": "base64-encoded-cbor",
  "claims_raw": "base64-encoded-claims-document",
  "status": "queued"
}
```

The `attestation_document` is a base64-encoded CBOR document produced by NitroTPM. Its `user_data` carries the compact signed envelope `{ v, claims_digest, timestamp, execution_id }`; `claims_raw` is the base64-encoded claims document that `claims_digest` binds — containing the repository URL, commit hash, script path, `script_env_hash`, the container-security posture, and the `gpu` block. See [Attestation Claims](#attestation-claims) for the full shape and the verifier binding-check contract.

**Error responses:**

| Status | Error code | Cause |
|---|---|---|
| 400 | `malformed_request` | Request body is not valid JSON or missing `encrypted_payload`/`client_public_key` |
| 400 | `decryption_failed` | Server could not decrypt the request payload |
| 400 | `validation_failed` | Missing or invalid fields (details include per-field errors) |
| 400 | `missing_nonce` | Nonce is required but was missing or empty |
| 400 | `invalid_nonce` | Nonce fails type, length (16–256 chars), or format (URL-safe only) validation |
| 400 | `duplicate_nonce` | Nonce has already been used; request rejected as a potential replay |
| 400 | `denied_env_key` | `script_env` contains keys on the deny-list (details include `denied_keys`) |
| 400 | `commit_hash_mismatch` | Request `commit_hash` does not match the OIDC token's `sha` claim |
| 401 | `authentication_failed` | GitHub token authentication failed |
| 401 | `oidc_authentication_failed` | Missing, invalid, or expired OIDC token; signature verification failure; wrong issuer or audience |
| 403 | `oidc_authentication_failed` | Valid OIDC token from a repository not in `ALLOWED_REPOSITORIES` |
| 403 | `repository_mismatch` | OIDC token `repository` claim does not match the `repository_url` in the request |
| 404 | `github_api_error` | Repository, commit, or file not found |
| 413 | `script_too_large` | Script file exceeds `MAX_SCRIPT_SIZE_BYTES` |
| 429 | `rate_limit_exceeded` | Too many requests from this IP |
| 500 | `encryption_not_configured` | Server encryption is not configured |
| 500 | `attestation_failed` | NitroTPM attestation generation failed |
| 500 | `internal_server_error` | Unexpected server error |
| 503 | `at_capacity` | Server has reached `MAX_CONCURRENT_EXECUTIONS`; retry later |

---

### POST /execution/{execution_id}/output

Retrieves execution status and output. Supports incremental polling via the `offset` field in the decrypted payload — pass the `stdout_offset` / `stderr_offset` from the previous response to receive only new output.

All request and response payloads are encrypted using the shared key established during the `/execute` call (see [Encryption](#encryption)). Authentication is implicit: only the original caller who performed the PQ Hybrid KEM exchange during `/execute` possesses the shared key, so successful decryption proves caller identity.

When the execution is complete, the response includes an `output_attestation_document` — a NitroTPM attestation whose `user_data` carries the same compact signed envelope `{ v, claims_digest, timestamp, execution_id }` described in [Attestation Claims](#attestation-claims). The accompanying `claims_raw` binds the output claims document, including `output_digest` — a `sha256:`-prefixed digest of the canonical JSON object `{ stdout, stderr, exit_code }` (keys sorted, no whitespace, `exit_code` a JSON number), not a delimiter-glued string. If output attestation generation fails, `output_attestation_document` is `null` and an `attestation_error` field describes the failure. Output attestation is subject to rate limiting (`MAX_OUTPUT_ATTESTATIONS_PER_WINDOW` / `OUTPUT_ATTESTATION_WINDOW_SECONDS`) to prevent TPM resource exhaustion.

Note: `output_attestation_document` is included on **every** poll response, not only when execution is complete. This allows callers to attest incremental output.

**Request body (encrypted envelope):**

```json
{
  "encrypted_payload": "base64-encoded-ciphertext"
}
```

**Decrypted payload fields:**

| Field | Type | Default | Description |
|---|---|---|---|
| `nonce` | string | — | Client-provided nonce for output attestation freshness (required). Must be 16–256 characters, URL-safe characters only (`[a-zA-Z0-9._~-]`) |
| `offset` | int | 0 | Byte offset to start retrieving output from |

**Response (200, encrypted):**

```json
{
  "encrypted_response": "base64-encoded-ciphertext"
}
```

**Decrypted response fields:**

| Field | Type | Description |
|---|---|---|
| `execution_id` | string | UUID of the execution |
| `status` | string | One of: `queued`, `running`, `completed`, `failed`, `timed_out` |
| `stdout` | string | Standard output (from offset) |
| `stderr` | string | Standard error (from offset) |
| `stdout_offset` | int | Next byte offset for stdout (use in subsequent polls) |
| `stderr_offset` | int | Next byte offset for stderr (use in subsequent polls) |
| `complete` | bool | `true` when execution has finished |
| `exit_code` | int \| null | Process exit code (present only when complete) |
| `output_attestation_document` | string \| null | Base64-encoded CBOR attestation of the current output snapshot (present on every response; `null` if attestation failed or rate-limited) |
| `claims_raw` | string | Base64-encoded output claims document that `output_attestation_document`'s `claims_digest` binds (present whenever `output_attestation_document` is non-null) |
| `attestation_error` | string | Error message if output attestation failed (present only on failure) |
| `attestation_rate_limited` | bool | `true` when output attestation was skipped due to rate limiting (present only when rate-limited) |

**Error responses:**

| Status | Error code | Cause |
|---|---|---|
| 400 | `malformed_request` | Request body is not valid JSON or missing `encrypted_payload` |
| 400 | `invalid_base64` | Invalid base64 encoding in `encrypted_payload` |
| 400 | `decryption_failed` | Server could not decrypt the request payload |
| 400 | `no_encryption_context` | No encryption context available for this execution ID |
| 400 | `missing_nonce` | Nonce is required but was missing or empty |
| 400 | `invalid_nonce` | Nonce fails type, length (16–256 chars), or format (URL-safe only) validation |
| 400 | `duplicate_nonce` | Nonce has already been used; request rejected as a potential replay |
| 400 | `invalid_offset` | Negative offset value |
| 404 | `execution_not_found` | No execution with this ID exists |
| 413 | `request_body_too_large` | Request body exceeds `MAX_REQUEST_BODY_BYTES` |
| 429 | `rate_limit_exceeded` | Too many requests from this IP |
| 500 | `encryption_not_configured` | Server encryption is not configured |
| 500 | `internal_server_error` | Unexpected server error |

---

### GET /health

Returns operational status of the server. This endpoint does not require authentication.

**Response (200):**
```json
{
  "status": "healthy"
}
```

If the health check itself encounters an error, the server returns 200 with `"status": "unhealthy"`.

| Field | Type | Description |
|---|---|---|
| `status` | string | `healthy` or `unhealthy` |

## Development

Run tests:
```bash
uv run pytest
```

Run with hot reload:
```bash
uv run uvicorn src.main:app --reload --port 8080
```

## Cleaning Up

When you're done, remove all deployed resources (Terraform infrastructure, AMI, and EBS snapshot) using the cleanup script.

### Prerequisites

- AMI build result file (`ami_build_result.json`) from the build step
- [Terraform](https://developer.hashicorp.com/terraform/install) installed
- AWS credentials configured (`aws configure` or environment variables)
- Python 3.11+ with [uv](https://astral.sh/uv) installed
- Script dependencies (boto3) managed via `scripts/pyproject.toml`

### Running the Cleanup

```bash
uv run --project scripts python scripts/cleanup.py \
  --ami-build-result ami_build_result.json \
  --terraform-dir terraform/deploy
```

CLI arguments:

| Argument | Required | Default | Description |
|---|---|---|---|
| `--ami-build-result` | No | `ami_build_result.json` | Path to the AMI build result JSON from the build step |
| `--terraform-dir` | No | `terraform/deploy` | Path to the Terraform configuration directory |
| `--keep-ami` | No | `false` | Preserve AMI and snapshot during cleanup (skip deregistration) |

### Cleanup Flow

1. Loads the AMI build result file to read the AMI ID, snapshot ID, and region
2. Prompts for confirmation before proceeding (type `yes` to confirm)
3. Runs `terraform init` and `terraform destroy -auto-approve` in the Terraform directory
4. Unless `--keep-ami` is set, deregisters the AMI and deletes the associated EBS snapshot via the AWS API
5. Verifies that all resources (EC2 instances, AMI, snapshot) have been removed
6. Reports any remaining resources that may need manual cleanup

## License

See LICENSE file for details.
