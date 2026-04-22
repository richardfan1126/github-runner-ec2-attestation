# Security Review

Date: 2026-04-21

## Findings

### 1. High: `/attest` is an unauthenticated, unthrottled NitroTPM operation

- `src/server.py` exposes `/attest` without authentication.
- `src/server.py` invokes `generate_attestation()` on every request, which calls the NitroTPM attestation binary.
- The rate-limiting middleware applies to `/attest`, but the default `RATE_LIMIT_PER_IP=100` requests per 60 seconds is generous enough that a distributed attacker can still trigger significant TPM load before being blocked.
- Impact: an internet client can repeatedly trigger expensive attestation work and degrade service availability even before reaching the authenticated execution flow.

### 2. High: Container isolation relies on ambient Docker daemon defaults rather than a pinned hardened host configuration

- `kiwi-descriptions/config.sh` enables the Docker daemon in the AMI.
- There is no Docker `daemon.json`, user-namespace remap, custom seccomp profile, or SELinux/AppArmor policy under `kiwi-descriptions/root/etc`.
- `src/script_executor.py` applies per-container flags (`read_only`, `network_mode="none"`, `no-new-privileges`, `cap_drop=["ALL"]`, `user="nobody"`), but host-level isolation behavior is otherwise whatever the distro Docker defaults are on the built AMI.
- Impact: the safety of running untrusted code depends heavily on ambient daemon/kernel defaults that are not explicitly hardened or locked by this project.

### 3. Medium: Default deployment is a public HTTP service with a shared, non-instance-specific audience

- `terraform/deploy/main.tf` allows inbound traffic from anywhere on port 8080 and assigns a public IP.
- `terraform/deploy/outputs.tf` publishes the endpoint as `http://<public-ip>:8080`.
- `kiwi-descriptions/root/etc/github-actions-remote-executor/env` bakes `EXPECTED_AUDIENCE=test-workflow` into the AMI instead of an instance-specific value.
- `README.md` describes `EXPECTED_AUDIENCE` as a value that should ensure tokens were issued for this specific Remote Executor instance.
- Impact: the deployed identity model does not match the documented trust model. Operators can easily deploy multiple instances that all accept the same OIDC audience, and the service is exposed over plain HTTP by default.

### 4. Medium: OIDC policy is repo-scoped only by default and does not restrict branch, workflow, or trust level

- `src/validation.py` verifies signature, issuer, and audience, then authorizes solely on the `repository` claim.
- `src/config.py` supports optional `ALLOWED_BRANCHES` (glob patterns) and `REQUIRE_PROTECTED_REF` controls, and `src/validation.py` enforces them via `_validate_branch_and_ref()`.
- Neither control is set in the default AMI env file (`kiwi-descriptions/root/etc/github-actions-remote-executor/env`), so a default deployment authorizes any workflow in an allowed repository regardless of branch, ref protection status, or workflow identity.
- Impact: any workflow in an allowed repository that can mint a GitHub OIDC token for the configured audience is treated as equally trusted. That broadens authorization to include unprotected branches, less-trusted workflows, and other execution contexts that may not meet the intended security bar.

### 5. Medium: Artifact provenance verification is not bound to a specific trusted workflow by default

- `scripts/build-ami.py` accepts an optional `--expected-workflow` argument. When provided, it extracts the workflow SAN from the attestation certificate and verifies the expected workflow path appears in it.
- `--expected-workflow` defaults to `None`; when omitted, the workflow identity check is skipped entirely.
- Impact: any attested artifact from the same repository can satisfy the provenance check, even if it was produced by a different workflow or a less-trusted repository state than operators intended to trust for AMI creation.

### 6. Medium: The AMI build path trusts the Rust installer without integrity verification

- `scripts/build-ami.py` installs Rust via `curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y`.
- Unlike ORAS (which has a hardcoded SHA-256 checksum verified before extraction) and coldsnap (which is cloned at a pinned version tag), the Rust installer is piped directly to a shell with no checksum or signature verification.
- Impact: a compromise of `sh.rustup.rs` or a network-level attack can execute arbitrary commands on the AMI build instance. Because that instance has IAM permissions for snapshot and AMI operations, this can backdoor the produced image before any later attestation step.

### 7. Medium: Execution container image is not digest-pinned by default

- `kiwi-descriptions/root/etc/github-actions-remote-executor/env` sets `CONTAINER_IMAGE=python:3.11-slim` with no digest.
- `src/config.py` supports an optional `CONTAINER_IMAGE_DIGEST` environment variable, and `src/script_executor.py` verifies the digest when configured.
- `CONTAINER_IMAGE_DIGEST` is not set in the default env file.
- Impact: upstream tag drift or registry compromise can silently change the code, packages, and isolation surface of the runtime container environment.

### 8. Medium: The trusted build path depends on a mutable GitHub-hosted runner image and a floating AL2023 mirrorlist

- `.github/workflows/build-attestable-image.yml` runs on `ubuntu-latest`, which is a moving GitHub-hosted runner image.
- `kiwi-descriptions/appliance.kiwi` uses the AL2023 `latest` mirrorlist path.
- `terraform/build-ami/data.tf` pins the build instance AMI to a specific name filter, which is an improvement, but the runner and mirrorlist remain floating.
- Impact: reproducibility and provenance are weakened because materially different upstream build environments can be selected over time without any repo change.

### 9. Low: The AMI build instance SSH private key is exported through Terraform outputs

- `terraform/build-ami/ssh_key.tf` generates a new private key in Terraform.
- `terraform/build-ami/outputs.tf` exposes that private key as the `ssh_private_key` output (marked sensitive but still present in state).
- `scripts/build-ami.py` reads the private key from Terraform output, writes it to a temp file, and securely overwrites it before deletion after use.
- Impact: the ephemeral build-instance credential is materialized in local Terraform state, increasing the blast radius of workstation or CI compromise during the AMI build flow.

### 10. Low: `ExecutionManager._execution_durations` list grows unboundedly

- `src/execution_manager.py` appends a float to `_execution_durations` for every completed execution.
- `cleanup_expired()` removes execution records and their associated encryption contexts and output buffers, but does not trim `_execution_durations`.
- Impact: in a long-running process with high execution volume, this list accumulates indefinitely, causing slow memory growth.

### 11. Medium: JWKS cache has no TTL — stale or revoked signing keys remain trusted for the process lifetime

- `src/validation.py` caches the GitHub OIDC JWKS response in `_jwks_cache` on first use.
- The cache is only refreshed when a `kid` lookup misses; there is no time-based expiry.
- If GitHub rotates or revokes a signing key, the server continues accepting tokens signed with the old key until the process restarts or a new `kid` triggers a refresh.
- Impact: key rotation events are not reflected promptly. A revoked key remains trusted indefinitely in a long-running deployment.

### 12. Medium: `RateLimiter._requests` dict grows unboundedly under distributed traffic

- `src/server.py` `RateLimiter` maps each source IP to a list of request timestamps.
- Old timestamps are pruned per-IP only when that IP makes a subsequent request; IPs that never make a second request are never evicted from the dict.
- A distributed attacker sending one request from each of many source IPs causes the dict to grow without bound.
- Impact: slow memory exhaustion in a long-running service under distributed or spoofed-source traffic, exploitable by an external attacker without authentication.

### 13. Medium: ORAS installed without checksum verification in the CI build workflow

- `.github/workflows/build-attestable-image.yml` installs ORAS 1.1.0 with a bare `curl | tar` pipeline and no SHA-256 checksum verification.
- This is inconsistent with `scripts/build-ami.py`, which verifies a hardcoded checksum before extracting ORAS 1.3.0.
- The two paths also use different ORAS versions (1.1.0 vs 1.3.0).
- Impact: a compromise of the GitHub releases CDN or a MITM during the CI run can substitute a malicious ORAS binary that tampers with or exfiltrates the artifact pushed to GHCR, undermining the entire provenance chain before any attestation step.

### 14. Medium: `validate_script_path` does not reject absolute paths or null bytes

- `src/validation.py` `validate_script_path()` checks for `../` and `..\\` traversal sequences but does not reject absolute paths (e.g., `/etc/passwd`) or null bytes (`\x00`).
- `os.path.join(clone_path, script_path)` silently discards the clone prefix when `script_path` is absolute, so the size check in `server.py` reads the host file at the absolute path rather than a file inside the clone.
- Impact: a caller supplying an absolute `script_path` bypasses the intent of the path validation. The script executor passes the path to Docker as `bash /workspace/<script_path>`, which is sandboxed, but the pre-execution `os.path.getsize` check runs on the host and will read arbitrary host files.

### 15. Low: `terraform.tfstate` files are committed to the repository

- `terraform/deploy/terraform.tfstate`, `terraform/deploy/terraform.tfstate.backup`, `terraform/build-ami/terraform.tfstate`, and `terraform/build-ami/terraform.tfstate.backup` are present in the repository.
- Terraform state files store resource metadata in plaintext, including the SSH private key from `terraform/build-ami/outputs.tf` (marked `sensitive` but stored unencrypted in state), AMI IDs, instance IDs, VPC/subnet IDs, and security group IDs.
- Impact: anyone with read access to the repository or its git history can extract infrastructure topology and the ephemeral build-instance SSH credential. This is a concrete materialization of the blast-radius concern noted in Finding 9.

### 16. Low: SSH host key not verified during AMI build — MITM risk on build connection

- `scripts/build-ami.py` `verify_ssh_connectivity()` uses `paramiko.AutoAddPolicy()`, which silently accepts any host key on first connection.
- An attacker who can intercept traffic between the build workstation and the EC2 instance can observe or modify commands executed on the build instance, including the artifact pull and AMI creation steps.
- The build instance holds IAM permissions for snapshot and AMI operations, so a successful MITM can backdoor the produced image.
- Impact: the SSH channel that drives the trusted build path provides no host authentication guarantee.

## Resolved Findings (from previous review dated 2026-04-18)

The following issues identified in the prior review have been remediated and verified against the current codebase.

| # | Previous Severity | Description | Resolution |
|---|-------------------|-------------|------------|
| 1 | Critical | OIDC authorization not bound to the repository being executed | `server.py` now parses `owner/repo` from `repository_url` and rejects requests where it does not match the OIDC `repository` claim. The output endpoint binds the claim to the stored `execution_record.repository`. |
| 2 | High | `MAX_CONCURRENT_EXECUTIONS` not enforced | `ExecutionManager.try_create_execution()` atomically checks active execution count against the cap under a single lock acquisition. `server.py` returns HTTP 503 when at capacity. |
| 3 | High | Execution output stored in unbounded memory, not reclaimed | `OutputCollector` enforces a configurable `max_output_size_bytes` cap (default 10 MB) and truncates at that limit. A `periodic_cleanup` background task in the `lifespan` context manager invokes `cleanup_expired()` every 60 seconds. |
| 4 | Medium | GitHub tokens exposed through clone URL handling | `repository.py` strips the token from `.git/config` via `git remote set-url` immediately after cloning, then removes the `.git` directory entirely with `shutil.rmtree`. |
| 5 | Medium | `MAX_SCRIPT_SIZE_BYTES` dead configuration, never enforced | `server.py` checks `os.path.getsize(script_full_path)` against `config.max_script_size_bytes` after cloning and returns HTTP 413 if exceeded. |
| 6 | Medium | Public monitoring endpoints leak operational state | `/health` now returns only `{"status": "healthy"}`. The `/metrics` endpoint has been removed entirely. |
| 9 | High | Encrypted requests are replayable | `src/nonce_cache.py` implements a thread-safe TTL-based nonce cache. Both `/execute` and `/execution/{id}/output` reject duplicate nonces with HTTP 400. |
| 11 | High | `scripts/build-ami.py` vulnerable to shell injection via `artifact_ref` | `validate_artifact_reference()` enforces a strict allowlist regex (`^ghcr\.io/[a-zA-Z0-9._-]+/...`) rejecting all shell metacharacters before any remote command is constructed. |
| 12 | Low | Logging context is global and mutable across concurrent requests | `logging_config.py` now uses `contextvars.ContextVar`. Each request and background thread gets an isolated copy; `script_executor.py` creates a fresh `contextvars.copy_context()` per execution thread. |
| 13 | Medium | Execution shared keys persist indefinitely in memory | `cleanup_expired()` now calls `encryption_manager.remove_encryption_context()` for each expired execution. The periodic cleanup task ensures this runs regularly. |
| 14 | Medium | SSH-enabled debug images published under same artifact conventions | The workflow sets a `debug` OCI annotation on every push. `build-ami.py` reads the annotation via `oras manifest fetch` and raises an error if `debug=true` and `--allow-debug` was not passed. |
| 18 | Low | `/execution/{id}/output` does not bind execution record to OIDC repository claim | The output endpoint now compares `oidc_result.claims["repository"]` against `execution_record.repository` and returns HTTP 403 on mismatch. |
| 20 | High | Execution containers do not explicitly drop Linux capabilities | `script_executor.py` now passes `cap_drop=["ALL"]` to `containers.create()`. |
| 21 | Medium | Full cloned repository including VCS metadata exposed inside container | `repository.py` removes the `.git` directory with `shutil.rmtree` after cloning. The bind-mounted workspace contains only the working tree. |
| 22 | Medium | Host executor service not strongly sandboxed | The systemd unit now sets `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`, `RestrictAddressFamilies`, and explicit `ReadWritePaths`. |
| 24 | High | AMI build instance IAM role broadly wildcarded across EC2/EBS operations | `terraform/build-ami/iam.tf` now scopes all mutating actions to specific snapshot, image, and volume ARN patterns in the current region/account with `aws:RequestedRegion` conditions. |
| 8 (partial) | Medium | ORAS downloaded without integrity verification | `install_oras()` now verifies a hardcoded SHA-256 checksum before extracting the archive. |
| 14 (partial) | Medium | coldsnap cloned from floating HEAD | `install_coldsnap()` now clones at a pinned version tag (`v0.9.0`) with `--depth 1` and uses `cargo install --locked`. |
| 25 (partial) | Medium | Build instance AMI selected with `most_recent = true` | `terraform/build-ami/data.tf` now pins to a specific AMI name filter rather than floating `most_recent`. |
| 26 | Low | Builder image DNF packages float despite comment claiming pinned versions | `Dockerfile.kiwi-builder` now locks `releasever` to a specific AL2023 snapshot via `/etc/dnf/vars/releasever`, and the comment accurately describes this mitigation. |

## Coverage Gaps

- There are no tests asserting that branch/ref OIDC restrictions are enforced when `ALLOWED_BRANCHES` or `REQUIRE_PROTECTED_REF` are set.
- There are no tests or deployment safeguards requiring TLS or an instance-specific OIDC audience.
- There are no provenance checks that pin AMI conversion to a specific trusted workflow definition by default.
- There are no tests constraining the container image to a digest-pinned reference.
- There are no tests for `RateLimiter` memory growth under high-cardinality source IPs.
- There are no tests asserting that absolute `script_path` values are rejected by `validate_script_path`.
- I did not inspect a live built AMI, so I could not verify the effective Docker seccomp profile, user-namespace behavior, or SELinux/AppArmor enforcement at runtime.
- I did not perform dependency CVE triage against the locked package set or the mutable upstream build environments.
