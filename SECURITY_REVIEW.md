# Security Review

Date: 2026-05-12

## Findings

### 1. High: `/attest` is an unauthenticated, unthrottled NitroTPM operation

- `src/server.py` exposes `/attest` without authentication.
- `src/server.py` invokes `generate_attestation()` on every request, which calls the NitroTPM attestation binary.
- The rate-limiting middleware applies to `/attest`, but the default `RATE_LIMIT_PER_IP=100` requests per 60 seconds is generous enough that a distributed attacker can still trigger significant TPM load before being blocked.
- Impact: an internet client can repeatedly trigger expensive attestation work and degrade service availability even before reaching the authenticated execution flow.

### 2. High: Artifact-controlled raw filename can become remote shell command injection

- `scripts/build-ami.py` finds the raw image with `cd ~/artifacts/build-output && ls *.raw`.
- It stores raw stdout in `raw_image_path` and interpolates that value into `/home/ec2-user/.cargo/bin/coldsnap upload {raw_image_path}`.
- A malicious but signed artifact can include a crafted `.raw` filename that changes the shell command executed on the AMI build instance.
- Required hardening: enforce exactly one raw artifact with a strict basename allowlist, quote with `shlex.quote`, or avoid shell interpolation entirely.
- Impact: a malicious artifact can execute arbitrary commands on the build instance before snapshot upload and AMI registration.

### 3. Medium: Debug-image production gate fails open

- `scripts/build-ami.py` uses `oras manifest fetch` to inspect the artifact `debug` annotation.
- If manifest fetch fails or JSON parsing fails, the script logs a warning and proceeds without enforcing the debug-image gate.
- Required hardening: fail closed when the debug annotation cannot be verified, unless `--allow-debug` is explicitly provided.
- Impact: a production AMI build can proceed from an artifact whose debug status could not be determined.

### 4. Medium: Default deployment is a public HTTP service with a shared, non-instance-specific audience

- `terraform/deploy/main.tf` allows inbound traffic from anywhere on port 8080 and assigns a public IP.
- `terraform/deploy/outputs.tf` publishes the endpoint as `http://<public-ip>:8080`.
- `kiwi-descriptions/root/etc/github-actions-remote-executor/env` bakes `EXPECTED_AUDIENCE=test-workflow` into the AMI instead of an instance-specific value.
- `README.md` describes `EXPECTED_AUDIENCE` as a value that should ensure tokens were issued for this specific Remote Executor instance.
- Impact: the deployed identity model does not match the documented trust model. Operators can easily deploy multiple instances that all accept the same OIDC audience, and the service is exposed over plain HTTP by default.

### 5. Medium: OIDC policy is repo-scoped only by default and does not restrict branch, workflow, or trust level

- `src/validation.py` verifies signature, issuer, and audience, then authorizes solely on the `repository` claim.
- `src/config.py` supports optional `ALLOWED_BRANCHES` (glob patterns) and `REQUIRE_PROTECTED_REF` controls, and `src/validation.py` enforces them via `_validate_branch_and_ref()`.
- Neither control is set in the default AMI env file (`kiwi-descriptions/root/etc/github-actions-remote-executor/env`), so a default deployment authorizes any workflow in an allowed repository regardless of branch, ref protection status, or workflow identity.
- Impact: any workflow in an allowed repository that can mint a GitHub OIDC token for the configured audience is treated as equally trusted. That broadens authorization to include unprotected branches, less-trusted workflows, and other execution contexts that may not meet the intended security bar.

### 6. Medium: `/execute` has no encrypted request body size limit

- `src/server.py` reads request JSON and base64-decodes `encrypted_payload` before enforcing any request-size limit.
- The decrypted payload can also contain unbounded optional fields such as `script_env`, subject only to downstream processing limits.
- Required hardening: enforce an ingress/proxy body limit and an application-level maximum for `encrypted_payload`, `client_public_key`, nonce, and decrypted JSON size.
- Impact: unauthenticated clients can force avoidable JSON parsing, base64 decoding, and decryption work with oversized requests.

### 7. Medium: Output buffer size configuration is ignored

- `src/config.py` defines `max_output_size_bytes` and parses `MAX_OUTPUT_SIZE_BYTES`.
- `src/server.py` constructs `OutputCollector()` without passing `config.max_output_size_bytes`, so the collector always uses its constructor default.
- Required hardening: pass `config.max_output_size_bytes` into `OutputCollector` and add a regression test that a configured lower limit is enforced.
- Impact: operators cannot lower output memory exposure through configuration, even though the setting appears supported.

### 8. Medium: Artifact provenance verification is not bound to a specific trusted workflow by default

- `scripts/build-ami.py` accepts an optional `--expected-workflow` argument. When provided, it extracts the workflow SAN from the attestation certificate and verifies the expected workflow path appears in it.
- `--expected-workflow` defaults to `None`; when omitted, the workflow identity check is skipped entirely.
- Impact: any attested artifact from the same repository can satisfy the provenance check, even if it was produced by a different workflow or a less-trusted repository state than operators intended to trust for AMI creation.

### 9. Medium: Script path must remain explicitly verifiable from the attestation document

- `src/attestation.py` currently writes `script_path` into the NitroTPM attestation `user_data` alongside `repository_url`, `commit_hash`, and `timestamp`.
- This binding is security-critical: consumers must be able to verify that the attested execution used the expected script path, not just the expected repository and commit.
- Required hardening: document the attestation `user_data` schema, add regression tests that fail if `script_path` is removed or renamed, and require consumers to compare the attested `script_path` against the requested/expected script path.
- Recommended extension: include a canonical digest of other execution-affecting inputs, especially `script_env`, because shell variables can alter what `bash /workspace/<script_path>` executes before the script body runs.
- Impact: if script-path binding regresses or consumers do not validate it, a valid attestation for the same repo and commit may not prove that the intended build script was executed.

### 10. Medium: Execution attestation is not bound to the server execution ID

- `src/server.py` creates an `execution_id` for each `/execute` request and uses that ID for later status/output lookup.
- `src/attestation.py` does not include `execution_id` in the NitroTPM attestation `user_data`; the attested data binds repository URL, commit hash, script path, script environment hash, and timestamp, but not the server-side execution record identity.
- Required hardening: include `execution_id` in execution attestation `user_data` and add a regression test that verifies the attested execution ID matches the `/execute` response.
- Impact: consumers cannot cryptographically bind an execution attestation to the specific server execution record they are polling or storing.

### 11. Medium: Output attestation is not bound to the server execution ID

- `src/server.py` generates output attestations from the output returned by `/execution/{id}/output`.
- `src/attestation.py` signs a digest of stdout, stderr, and exit code, but does not include the `execution_id` in the output attestation payload.
- Required hardening: include `execution_id` in output attestation `user_data` and add a regression test that rejects an output attestation for a different execution record.
- Impact: consumers cannot cryptographically bind an output attestation to the specific execution whose output endpoint was queried.

### 12. Low: Rootless Docker and executor systemd hardening still have residual gaps

- `kiwi-descriptions/root/etc/systemd/system/github-actions-remote-executor.service` waits for `/run/user/1000/docker.sock`, while `kiwi-descriptions/config.sh` creates `gha-executor` without explicitly pinning UID 1000. If the user is assigned a different UID, the service waits on the wrong Docker socket.
- `kiwi-descriptions/config.sh` configures the rootless Docker user service with unlimited core dumps, processes, tasks, and open files.
- The executor unit has useful hardening already (`NoNewPrivileges`, `ProtectSystem`, `ProtectHome`, and restricted address families), but does not set several common compatible protections such as disabling core dumps and kernel/control-group exposure where rootless Docker permits it.
- Required hardening: derive the rootless Docker socket path from the actual `gha-executor` UID or pin the UID deliberately, set `LimitCORE=0`, and add regression checks for the expected hardening directives.
- Impact: a UID mismatch can break service startup, and unlimited core dumps or weaker unit isolation can increase local secret exposure after a crash or service compromise.

### 13. High: Encrypted request replay protection is optional

- `src/server.py` only checks the `/execute` nonce cache when the decrypted request contains a `nonce` field.
- `src/server.py` only checks the `/execution/{id}/output` nonce cache when the decrypted output request contains a `nonce` field.
- Required hardening: make nonce mandatory for encrypted `/execute` and `/execution/{id}/output` payloads, reject missing/empty nonces, and add regression tests for missing-nonce replay attempts.
- Impact: clients that omit `nonce` bypass the nonce cache entirely, so replay protection is not guaranteed by the protocol.

### 14. Medium: Post-decryption failures are returned as plaintext HTTP errors

- Successful `/execute` and `/execution/{id}/output` responses are encrypted with the negotiated shared key.
- After `/execute` decryption succeeds, validation, GitHub authentication, repository fetch, attestation, and capacity failures still raise normal FastAPI `HTTPException` responses.
- Required hardening: once request decryption succeeds, return encrypted error envelopes for all expected application failures that occur after shared-key establishment.
- Impact: a client can distinguish decrypted-processing failures from encrypted success responses at the HTTP layer, and authenticated callers receive protocol details outside the encrypted response channel.

### 15. Medium: AMI image Python dependencies are not locked to `uv.lock`

- `.github/scripts/build-kiwi-image.sh` copies `uv.lock` into the image build context, but then reads dependency ranges from `pyproject.toml`.
- The script runs `pip3 download` against those version ranges instead of installing or exporting a frozen, hash-checked dependency set from `uv.lock`.
- Required hardening: use `uv sync --frozen`, a hash-checked export from `uv.lock`, or another lockfile-enforced installation path for the dependencies embedded in the AMI.
- Impact: the built AMI can silently pick newer dependency versions than the reviewed lockfile, weakening reproducibility and supply-chain review.

### 16. Medium: Rootless Docker helper sources are not verified by signature or checksum

- `.github/scripts/build-kiwi-image.sh` clones `rootlesskit`, `slirp4netns`, and `fuse-overlayfs` from upstream repositories by release tag.
- The same script downloads `dockerd-rootless.sh` from raw GitHub content at a named Moby ref.
- Required hardening: pin immutable commits and verify signed tags, checksums, or vendored artifacts before compiling or embedding these helpers into the AMI.
- Impact: compromise of an upstream tag/ref, raw GitHub content path, or build-time network path can alter rootless Docker components embedded into the trusted image.

### 17. Low: Cloned repositories can be left behind after unexpected post-clone errors

- `src/server.py` explicitly cleans the clone directory on several expected errors, including GitHub API failures, script-size rejection, attestation failure, and capacity rejection.
- If an unexpected exception occurs after cloning but before async execution owns the clone path, the generic exception handler does not clean `clone_result.clone_path`.
- Required hardening: wrap post-clone processing in a `finally` block that cleans the clone unless ownership has been handed to the script executor.
- Impact: repository contents and checked-out scripts can remain on disk longer than intended after rare failure paths.

### 18. Low: Invalid boolean configuration silently disables protected-ref enforcement

- `src/config.py` parses `REQUIRE_PROTECTED_REF` by treating `true`, `1`, and `yes` as true; every other value becomes false.
- Required hardening: reject unrecognized boolean strings during config loading, and add tests for typo values such as `treu` or `enabled`.
- Impact: an operator typo can silently disable protected-ref enforcement instead of failing startup.

### 19. Low: The AMI build instance SSH private key is exported through Terraform outputs

- `terraform/build-ami/ssh_key.tf` generates a new private key in Terraform.
- `terraform/build-ami/outputs.tf` exposes that private key as the `ssh_private_key` output (marked sensitive but still present in state).
- `scripts/build-ami.py` reads the private key from Terraform output, writes it to a temp file, and securely overwrites it before deletion after use.
- Impact: the ephemeral build-instance credential is materialized in local Terraform state, increasing the blast radius of workstation or CI compromise during the AMI build flow.

### 20. Medium: JWKS cache has no TTL — stale or revoked signing keys remain trusted for the process lifetime

- `src/validation.py` caches the GitHub OIDC JWKS response in `_jwks_cache` on first use.
- The cache is only refreshed when a `kid` lookup misses; there is no time-based expiry.
- If GitHub rotates or revokes a signing key, the server continues accepting tokens signed with the old key until the process restarts or a new `kid` triggers a refresh.
- Impact: key rotation events are not reflected promptly. A revoked key remains trusted indefinitely in a long-running deployment.

### 21. Low: SSH host key not verified during AMI build — MITM risk on build connection

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
| 3 | High | Execution output stored in unbounded memory, not reclaimed | `OutputCollector` enforces a default 10 MB cap and truncates at that limit. A `periodic_cleanup` background task in the `lifespan` context manager invokes `cleanup_expired()` every 60 seconds. The config wiring issue is tracked as an open finding above. |
| 4 | Medium | GitHub tokens persisted in cloned repository metadata | `repository.py` strips the token from `.git/config` via `git remote set-url` immediately after cloning, then removes the `.git` directory entirely with `shutil.rmtree`. Process-argument exposure is tracked separately. |
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
| 27 | High | Docker daemon runs rootful | The service now uses the rootless Docker socket at `/run/user/{uid}/docker.sock`, and the systemd unit waits for `/run/user/1000/docker.sock`. |
| 28 | Medium | Rust installed with bare `curl \| sh` | `install_rust()` now downloads the standalone Rust tarball and detached signature, imports the Rust GPG key, and runs `gpg --verify` before installation. |
| 29 | Medium | Execution container image is not digest-pinned by default | The AMI env file sets `CONTAINER_IMAGE_DIGEST`, and `ServerConfig.validate()` now requires a configured or digest-pinned image reference. |
| 30 | Low | `ExecutionManager._execution_durations` list grows unboundedly | Execution durations are stored in a bounded `deque(maxlen=10000)`. |
| 31 | Medium | `RateLimiter._requests` dict grows unboundedly under distributed traffic | `RateLimiter.cleanup_stale_ips()` prunes stale IP entries and is called by periodic cleanup. |
| 32 | Medium | `validate_script_path` does not reject absolute paths or null bytes | `validate_script_path()` now rejects empty, null-byte, absolute, and traversal paths. |

## Coverage Gaps

- There are no tests asserting that branch/ref OIDC restrictions are enforced when `ALLOWED_BRANCHES` or `REQUIRE_PROTECTED_REF` are set.
- There are no tests or deployment safeguards requiring TLS or an instance-specific OIDC audience.
- There are no provenance checks that pin AMI conversion to a specific trusted workflow definition by default.
- There are no tests asserting that `script_path` remains present and consumer-verifiable in attestation `user_data`.
- There are no tests asserting that execution and output attestations bind to the expected server-side `execution_id`.
- There are no tests asserting encrypted requests without a nonce are rejected.
- There are no tests asserting post-decryption application errors are returned in encrypted response envelopes.
- There are no tests asserting AMI image Python dependencies are installed from the reviewed lockfile rather than from version ranges.
- There are no tests asserting rootless Docker helper sources are verified by immutable commit, signature, or checksum.
- There are no tests proving artifact raw filenames cannot alter build-host shell commands.
- There are no tests asserting the debug-image gate fails closed when manifest fetch or parsing fails.
- There are no tests asserting request body size limits before JSON/base64/decryption work.
- There are no tests asserting that `MAX_OUTPUT_SIZE_BYTES` is passed into `OutputCollector`.
- There are no tests asserting the rootless Docker socket path and systemd hardening directives remain aligned with the configured executor user.
- There are no tests asserting cloned repositories are removed on unexpected post-clone errors.
- There are no tests asserting invalid boolean configuration values fail startup instead of being interpreted as false.
- I did not inspect a live built AMI, so I could not verify the effective Docker seccomp profile, user-namespace behavior, or SELinux/AppArmor enforcement at runtime.
- I did not perform dependency CVE triage against the locked package set or the mutable upstream build environments.
