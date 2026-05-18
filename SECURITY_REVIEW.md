# Security Review

Date: 2026-05-15

## Findings

### 1. High: Requested commit is not bound to the GitHub OIDC `sha` claim

- `src/validation.py` verifies the GitHub OIDC token signature, issuer, audience, repository, branch restrictions, and protected-ref status.
- `src/server.py` then clones and executes the caller-supplied `commit_hash` without checking it against the token's `sha` claim.
- A valid workflow from an allowed/protected ref can request execution of another commit in the same repository.
- Required hardening: reject `/execute` unless `body["commit_hash"] == oidc_result.claims["sha"]`, or explicitly document and attest that arbitrary commit execution is intended.
- Impact: protected-ref checks do not prove the executed code is the same code that minted the OIDC token.

### 2. High: GitHub Actions AWS role trust is repo-wide and workflow-dispatch builds are not branch-restricted

- `terraform/github-actions-iam-role/main.tf` allows any subject matching `repo:${var.github_org}/${var.github_repo}:*` to assume the AMI-builder AWS role.
- `.github/workflows/build-attestable-image.yml` runs the `build-ami` job when `github.event_name == 'workflow_dispatch'`, regardless of branch.
- A less-trusted branch workflow in the same repository can assume broad EC2/IAM build permissions if it can run the workflow.
- Required hardening: restrict the AWS role trust to the exact protected branch and workflow identity, and restrict `workflow_dispatch` AMI builds to `refs/heads/main`.
- Impact: compromise of a less-trusted workflow context can become AWS infrastructure access and AMI publication capability.

### 3. High: Runtime image digest is verified at startup, but execution still uses the tag reference

- `src/main.py` validates/pulls the configured container image with `container_image_digest=config.container_image_digest` during startup.
- `src/script_executor.py` later creates execution containers with `image=self._container_image`.
- If `CONTAINER_IMAGE` is a mutable tag plus a separate `CONTAINER_IMAGE_DIGEST`, startup verification proves the tag matched the digest at startup, but execution is still addressed by the mutable tag.
- Required hardening: normalize the runtime image reference to an immutable `image@sha256:<digest>` reference after verification and use only that immutable reference for `containers.create()`.
- Impact: tag movement after startup can cause execution in an image different from the one operators intended to pin.

### 4. High: Real `ScriptExecutor` wiring does not receive the verified image digest

- `src/main.py` passes `container_image_digest=config.container_image_digest` to the temporary startup executor used for dangling-container cleanup.
- `src/server.py` constructs the real request-handling `ScriptExecutor` without passing `container_image_digest`.
- The digest verification support exists in `ScriptExecutor`, but the production app path does not provide the configured digest to the executor that actually runs submitted scripts.
- Required hardening: pass `container_image_digest=config.container_image_digest` in `create_app()` and add a regression test for production wiring.
- Impact: digest pinning can appear configured and tested at startup while the executor path remains tag-based.

### 5. Medium: Default deployment is a public HTTP service with a shared, non-instance-specific audience

- `terraform/deploy/main.tf` allows inbound traffic from anywhere on port 8080 and assigns a public IP.
- `terraform/deploy/outputs.tf` publishes the endpoint as `http://<public-ip>:8080`.
- `kiwi-descriptions/root/etc/github-actions-remote-executor/env` bakes `EXPECTED_AUDIENCE=test-workflow` into the AMI instead of an instance-specific value.
- `README.md` describes `EXPECTED_AUDIENCE` as a value that should ensure tokens were issued for this specific Remote Executor instance.
- Required hardening: place the service behind private networking, TLS, and an instance-specific audience value.
- Impact: operators can deploy multiple instances that accept the same OIDC audience, and the service is publicly reachable by default.

### 6. Medium: OIDC policy is repo-scoped only by default and does not restrict workflow identity

- `src/validation.py` authorizes primarily on the `repository` claim after signature, issuer, and audience validation.
- `ALLOWED_BRANCHES` and `REQUIRE_PROTECTED_REF` are supported, but the default AMI env file does not set them.
- There is no server-side allow-list for `workflow_ref` or `job_workflow_ref`.
- Required hardening: require protected refs by default for production and add workflow identity allow-listing for trusted caller workflows.
- Impact: any workflow in an allowed repository that can mint a token for the configured audience is treated as equally trusted.

### 7. Medium: Artifact provenance verification is optional outside the CI wrapper

- `scripts/build-ami.py` accepts `--expected-workflow`, but the argument defaults to `None`.
- When omitted, the script skips workflow identity verification and accepts any valid attestation from the same repository identity.
- The CI workflow currently passes `--expected-workflow`, but direct script use can bypass that policy.
- Required hardening: make `--expected-workflow` required, or provide a secure default that matches the trusted build workflow.
- Impact: operators can accidentally build AMIs from artifacts produced by a less-trusted workflow.

### 8. Medium: Build-time source supply chain still has unsigned source inputs

- `.github/scripts/build-kiwi-image.sh` clones `rootlesskit`, `slirp4netns`, and `fuse-overlayfs` and checks out pinned commits, but does not verify signed tags, commit signatures, checksums, or vendored source archives.
- `scripts/build-ami.py` clones `awslabs/coldsnap` at a pinned tag and builds with `cargo install --locked`, but does not verify a signed tag or commit.
- Several other inputs are stronger: the builder base image is digest-pinned, `libslirp` and `dockerd-rootless.sh` are checksum-verified, and ORAS is checksum-verified.
- Required hardening: verify signed commits/tags where available, use checksum-verified release archives, or vendor the exact reviewed source artifacts.
- Impact: compromise of an upstream source location or build-time network path can alter rootless Docker or snapshot tooling embedded into the trusted build chain.

### 9. Medium: GitHub token appears in `git clone` process arguments

- `src/repository.py` embeds the GitHub token directly into the clone URL passed to `git clone`.
- The token is later stripped from `.git/config`, but it can still appear in process arguments, `/proc/<pid>/cmdline`, and some failure paths before cleanup.
- Required hardening: use `GIT_ASKPASS`, an isolated credential helper, or a temporary private git config/header mechanism that does not place the token in argv.
- Impact: a same-host observer or diagnostic capture can expose the caller's GitHub token.

### 10. Medium: Nonce and base64 validation are too permissive

- `/execute` and `/execution/{id}/output` require a nonce, but only reject `None` and empty strings.
- Non-string nonce values can be stored in the nonce cache and can cause unexpected behavior or server errors.
- `base64.b64decode()` is called without `validate=True`, allowing malformed base64 encodings to be accepted or normalized before decryption.
- Required hardening: require nonce to be a bounded string with an expected format and entropy, and decode base64 with strict validation.
- Impact: hostile clients can exercise edge cases in replay tracking and request parsing that should be rejected at the protocol boundary.

### 11. Medium: JWKS cache has no TTL

- `src/validation.py` caches the GitHub OIDC JWKS response in `_jwks_cache` on first use.
- The cache is only refreshed when a `kid` lookup misses; there is no time-based expiry.
- Required hardening: add TTL-based JWKS refresh and honor cache-control semantics where practical.
- Impact: key rotation or revocation events are not reflected promptly in a long-running process.

### 12. Medium: Execution container sandbox has residual hardening gaps

- `src/script_executor.py` drops all Linux capabilities but re-adds several capabilities and leaves container networking enabled by default.
- The container is not configured with a read-only root filesystem, explicit non-root user, or tmpfs-only scratch space.
- Required hardening: remove unnecessary capabilities, disable networking unless required, set `read_only=True`, run as a non-root user, and provide explicit tmpfs scratch mounts.
- Impact: malicious scripts have more container-local attack surface and outbound network capability than necessary.

### 13. Medium: Execution containers do not enforce PID limits

- `src/script_executor.py` sets memory and CPU limits when creating execution containers.
- The `containers.create()` call does not set `pids_limit`.
- Required hardening: configure a workload-appropriate `pids_limit` and add a regression test that fork-heavy scripts fail inside the container without exhausting host process resources.
- Impact: a hostile script can create many processes and pressure the container runtime or host process table despite CPU and memory limits.

### 14. Low: The AMI build instance SSH private key is exported through Terraform outputs

- `terraform/build-ami/ssh_key.tf` generates a new private key in Terraform.
- `terraform/build-ami/outputs.tf` exposes that private key as the `ssh_private_key` output, marked sensitive but still present in Terraform state.
- `scripts/build-ami.py` reads the private key from Terraform output and writes it to a temporary file for SSH.
- Required hardening: prefer SSM Session Manager, EC2 Instance Connect with short-lived keys, or key generation outside Terraform state.
- Impact: the ephemeral build-instance credential is materialized in local or CI Terraform state.

### 15. Low: SSH host key is not verified during AMI build

- `scripts/build-ami.py` uses `paramiko.AutoAddPolicy()`, which silently accepts any SSH host key on first connection.
- An attacker who can intercept traffic between the runner and the EC2 build instance can modify commands executed during AMI creation.
- Required hardening: verify the instance SSH host key out-of-band before running provisioning commands, or avoid SSH by using SSM.
- Impact: the build control channel lacks host authentication.

### 16. Medium: Runtime AMI includes packages that are not justified for executor runtime

- `kiwi-descriptions/appliance.kiwi` installs `pciutils`, `gzip`, `awscli`, `tar`, `binutils`, `python3.11-pip`, and `git` into the runtime image.
- Static usage shows the executor needs `python3.11`, Docker/rootless runtime components, NitroTPM tooling, and boot/verity/systemd packages; there are no runtime references to `awscli`, `binutils`, `python3.11-pip`, or `pciutils`.
- `git` is required by the current `RepositoryClient.clone_repo()` implementation, but it also keeps a broad credential-aware network client in the trusted host environment.
- `tar` and `gzip` appear to be build/archive utilities in the reviewed code paths and should be proven necessary by package dependency analysis before remaining in the runtime image.
- Required hardening: maintain a runtime package allow-list, remove `awscli`, `binutils`, `python3.11-pip`, `pciutils`, and any unneeded archive/debug tools after build-time use, and either remove host-side `git` by switching checkout semantics or keep it with explicit justification and stronger credential isolation.
- Impact: a compromised executor or container escape gains extra post-compromise tooling for cloud interaction, package installation, binary inspection, repository access, and diagnostics.

### 17. Medium: Exception and subprocess error paths bypass the existing log sanitizer

- `src/logging_config.py` defines `sanitize_for_logging()` and `sanitize_error_message()`, and `src/server.py` imports them, but the main request and exception paths log raw interpolated strings instead.
- `src/repository.py` raises `GitHubAPIError(f"Clone failed: {result.stderr.strip()}", 500)` for unclassified clone failures.
- `src/server.py` logs that raw `GitHubAPIError.message` and returns it in an encrypted error envelope to the caller.
- `src/repository.py` also logs raw `git remote set-url` stderr on cleanup failure, and output attestation failure paths return raw `nitro-tpm-attest` stderr in `attestation_error`.
- Required hardening: never log or return raw subprocess stderr; centralize redaction for tokens, authorization headers, credentialed URLs, environment assignments, absolute paths, and control characters; expose allow-listed external error messages while retaining sanitized diagnostic categories internally.
- Impact: GitHub tokens, credentialed URLs, local paths, tool stderr, or environment-derived details can be persisted in logs or disclosed through encrypted error responses.

### 18. Low: User-controlled log fields are not consistently escaped, bounded, or minimized

- `src/server.py` logs user-controlled `repository_url`, `script_path`, validation errors, and duplicate nonce values.
- Nonce strictness is already tracked separately, but the current logging path can still create high-cardinality log entries or multi-line/control-character log injection if hostile values reach these messages.
- Required hardening: use structured logging fields with JSON escaping or `repr()`, apply length caps before logging, avoid logging nonce values, and prefer request/execution IDs over user-supplied identifiers.
- Impact: logs become easier to forge, search pollution increases during hostile traffic, and sensitive repository paths or request metadata are retained unnecessarily.

### 19. Medium: Output polling can generate TPM attestations on every request

- `/execution/{id}/output` calls `generate_output_attestation()` for output responses.
- A polling client can repeatedly request output and force repeated NitroTPM attestation work.
- Required hardening: add a dedicated rate limit for output-attestation generation, keyed by execution ID and caller/source, with a lower budget than normal output polling. Return output without a new attestation, or return a rate-limit error, when the attestation budget is exhausted.
- Impact: frequent polling can turn TPM attestation into an avoidable resource-exhaustion path.

### 20. Medium: Service starts even when NitroTPM is unavailable

- `src/main.py` checks NitroTPM availability at startup.
- If the device is unavailable, startup logs an error and warning but continues serving.
- Required hardening: fail closed in production when NitroTPM is unavailable, allowing startup without TPM only behind an explicit development/test configuration flag.
- Impact: the service can accept requests even though its core attestation guarantee cannot be produced.

## Resolved Findings (from previous review dated 2026-04-18)

The following issues identified in prior review passes have been remediated and verified against the current codebase.

| # | Previous Severity | Description | Resolution |
|---|-------------------|-------------|------------|
| 1 | Critical | OIDC authorization not bound to the repository being executed | `server.py` now parses `owner/repo` from `repository_url` and rejects requests where it does not match the OIDC `repository` claim. The output endpoint binds the claim to the stored `execution_record.repository`. |
| 2 | High | `MAX_CONCURRENT_EXECUTIONS` not enforced | `ExecutionManager.try_create_execution()` atomically checks active execution count against the cap under a single lock acquisition. `server.py` returns HTTP 503 when at capacity. |
| 3 | High | Execution output stored in unbounded memory, not reclaimed | `OutputCollector` enforces a default 10 MB cap and truncates at that limit. A `periodic_cleanup` background task in the `lifespan` context manager invokes `cleanup_expired()` every 60 seconds. |
| 4 | Medium | GitHub tokens persisted in cloned repository metadata | `repository.py` strips the token from `.git/config` via `git remote set-url` immediately after cloning, then removes the `.git` directory entirely with `shutil.rmtree`. Process-argument exposure is tracked separately. |
| 5 | Medium | `MAX_SCRIPT_SIZE_BYTES` dead configuration, never enforced | `server.py` checks `os.path.getsize(script_full_path)` against `config.max_script_size_bytes` after cloning and returns HTTP 413 if exceeded. |
| 6 | Medium | Public monitoring endpoints leak operational state | `/health` now returns only `{"status": "healthy"}`. The `/metrics` endpoint has been removed entirely. |
| 7 | Medium | Output buffer size configuration is ignored | `server.py` now passes `config.max_output_size_bytes` to `OutputCollector`. |
| 9 | High | Encrypted requests are replayable | `src/nonce_cache.py` implements a thread-safe TTL-based nonce cache. Both `/execute` and `/execution/{id}/output` reject duplicate nonces with encrypted errors. Nonce strictness is tracked separately. |
| 10 | Medium | Execution attestation is not bound to the server execution ID | `src/attestation.py` includes `execution_id` in execution attestation `user_data` when provided. |
| 11 | High | `scripts/build-ami.py` vulnerable to shell injection via `artifact_ref` | `validate_artifact_reference()` enforces a strict allowlist regex rejecting shell metacharacters before remote command construction. Digest pinning is tracked separately. |
| 12 | Low | Logging context is global and mutable across concurrent requests | `logging_config.py` now uses `contextvars.ContextVar`. Each request and background thread gets an isolated copy; `script_executor.py` creates a fresh `contextvars.copy_context()` per execution thread. |
| 13 | Medium | Execution shared keys persist indefinitely in memory | `cleanup_expired()` now calls `encryption_manager.remove_encryption_context()` for each expired execution. The periodic cleanup task ensures this runs regularly. |
| 14 | Medium | SSH-enabled debug images published under same artifact conventions | The workflow sets a `debug` OCI annotation on every push. `build-ami.py` reads the annotation via `oras manifest fetch` and raises an error if `debug=true` and `--allow-debug` was not passed. |
| 15 | Medium | Post-decryption failures are returned as plaintext HTTP errors | Expected application errors after successful decryption are returned as encrypted error envelopes. |
| 16 | Medium | Output attestation is not bound to the server execution ID | `src/attestation.py` includes `execution_id` in output attestation `user_data` when provided. |
| 17 | Low | Cloned repositories can be left behind after unexpected post-clone errors | `server.py` uses post-clone cleanup with handoff tracking to remove clone directories unless execution ownership has been transferred. |
| 18 | Low | `/execution/{id}/output` does not bind execution record to OIDC repository claim | Output endpoint authorization is now based on possession of the execution-bound shared key; repository binding is enforced during `/execute`. |
| 19 | Low | Invalid boolean configuration silently disables protected-ref enforcement | `parse_strict_bool()` rejects unrecognized boolean strings during config loading. |
| 20 | High | Execution containers do not explicitly drop Linux capabilities | `script_executor.py` now passes `cap_drop=["ALL"]` to `containers.create()`. Residual added capabilities are tracked separately. |
| 21 | Medium | Full cloned repository including VCS metadata exposed inside container | `repository.py` removes the `.git` directory with `shutil.rmtree` after cloning. The bind-mounted workspace contains only the working tree. |
| 22 | Medium | Host executor service not strongly sandboxed | The systemd unit now sets `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=read-only`, `RestrictAddressFamilies`, `LimitCORE=0`, and explicit `ReadWritePaths`. |
| 24 | High | AMI build instance IAM role broadly wildcarded across EC2/EBS operations | `terraform/build-ami/iam.tf` now scopes mutating actions to specific snapshot, image, and volume ARN patterns in the current region/account with `aws:RequestedRegion` conditions. |
| 8 (partial) | Medium | ORAS downloaded without integrity verification | `install_oras()` now verifies a hardcoded SHA-256 checksum before extracting the archive. |
| 14 (partial) | Medium | coldsnap cloned from floating HEAD | `install_coldsnap()` now clones at a pinned version tag (`v0.9.0`) with `--depth 1` and uses `cargo install --locked`. |
| 25 (partial) | Medium | Build instance AMI selected with `most_recent = true` | `terraform/build-ami/data.tf` now pins to a specific AMI name filter rather than floating `most_recent`. |
| 26 | Low | Builder image DNF packages float despite comment claiming pinned versions | `Dockerfile.kiwi-builder` now locks `releasever` to a specific AL2023 snapshot via `/etc/dnf/vars/releasever`, and the comment accurately describes this mitigation. |
| 27 | High | Docker daemon runs rootful | The service now uses the rootless Docker socket at `/run/user/{uid}/docker.sock`, and the systemd unit waits for `/run/user/1000/docker.sock`. |
| 28 | Medium | Rust installed with bare `curl \| sh` | `install_rust()` now downloads the standalone Rust tarball and detached signature, imports the Rust GPG key, and runs `gpg --verify` before installation. |
| 29 | Medium | Execution container image is not digest-pinned by default | The AMI env file sets `CONTAINER_IMAGE_DIGEST`, and `ServerConfig.validate()` now requires a configured or digest-pinned image reference. Runtime use of the verified digest is tracked separately. |
| 30 | Low | `ExecutionManager._execution_durations` list grows unboundedly | Execution durations are stored in a bounded `deque(maxlen=10000)`. |
| 31 | Medium | `RateLimiter._requests` dict grows unboundedly under distributed traffic | `RateLimiter.cleanup_stale_ips()` prunes stale IP entries and is called by periodic cleanup. |
| 32 | Medium | `validate_script_path` does not reject absolute paths or null bytes | `validate_script_path()` now rejects empty, null-byte, absolute, and traversal paths. |
| 33 | High | AMI build verifies and pulls by mutable tag, not immutable digest | `scripts/build-ami.py` now requires digest-pinned GHCR artifact references, strips mutable tags for pull/verify operations, and uses the pinned digest for ORAS and GitHub attestation verification. |
| 34 | Medium | AMI Python dependency handling claims lockfile enforcement, but does not use it for wheel resolution | `.github/scripts/build-kiwi-image.sh` now exports requirements from `uv.lock` with hashes, downloads wheels with `--require-hashes`, and `kiwi-descriptions/config.sh` installs with `pip --require-hashes`. |
| 35 | Medium | GitHub Actions and builder base image are not pinned to immutable revisions | `.github/workflows/build-attestable-image.yml` now pins actions to full commit SHAs, and `.github/docker/Dockerfile.kiwi-builder` pins the Amazon Linux base image with `@sha256:`. |
| 36 | Medium | `script_env` can alter Bash execution semantics without key-level restrictions | `ServerConfig.script_env_deny_list` and `/execute` validation now reject high-impact environment keys such as Bash startup variables, loader variables, `PATH`, and credential-related prefixes. |
| 37 | Medium | Repository script validation follows symlinks | `RepositoryClient.validate_script_exists()` now rejects symlinks and verifies the real script path remains inside the real clone directory. |

## Threat Model

The following trust assumptions should be explicit when evaluating the findings above.

- Trusted operators: AWS account administrators and repository administrators who can configure IAM roles, branch protections, GitHub variables/secrets, GHCR permissions, and deployment settings.
- Trusted build workflow: only the reviewed AMI build workflow on a protected branch should be trusted to publish production AMI artifacts.
- Trusted caller workflows: only explicitly authorized GitHub Actions workflows from allowed repositories should be allowed to request remote execution.
- Partially trusted repository maintainers: maintainers of allowed repositories can influence scripts, commits, and environment values; branch protection and workflow identity determine whether they are trusted for production execution.
- Untrusted network clients: anyone who can reach the public API should be treated as hostile until request encryption, OIDC validation, nonce validation, and repository allow-list checks succeed.
- Untrusted script code: scripts executed inside containers should be considered adversarial with respect to container escape, network egress, output exfiltration, and local resource exhaustion.
- Trusted AMI consumers: consumers must verify NitroTPM attestation chains, PCR values, nonce freshness, server public-key fingerprint, attested request metadata, `execution_id`, `script_env_hash`, and output digest before relying on results.
- Trusted infrastructure roots: AWS NitroTPM, GitHub OIDC/Sigstore infrastructure, pinned package repositories, pinned source artifacts, and Terraform provider hashes are part of the supply-chain root of trust.
- Out of scope unless explicitly hardened: protecting against a compromised AWS account admin, compromised GitHub repository admin, compromised GitHub-hosted runner, or malicious code that successfully escapes the execution container.

## Coverage Gaps

- There are no tests asserting that requested `commit_hash` must match the GitHub OIDC `sha` claim.
- There are no tests asserting the AWS OIDC trust policy is restricted to the trusted branch and workflow identity.
- There are no tests asserting runtime execution uses an immutable container image digest reference after startup verification.
- There are no tests asserting the production `create_app()` path passes `container_image_digest` into `ScriptExecutor`.
- There are no tests or deployment safeguards requiring TLS, private networking, or an instance-specific OIDC audience.
- There are no tests requiring production defaults for `ALLOWED_BRANCHES`, `REQUIRE_PROTECTED_REF`, or workflow identity allow-listing.
- There are no tests asserting all build-time source artifacts are signature- or checksum-verified.
- There are no tests asserting GitHub tokens are never placed in subprocess argv.
- There are no tests asserting strict nonce type/length/format validation and strict base64 decoding.
- There are no tests asserting JWKS cache entries expire.
- There are no tests asserting execution containers use read-only root filesystems, non-root users, no unnecessary capabilities, disabled networking where possible, and tmpfs-only scratch space.
- There are no tests asserting execution containers set a `pids_limit`.
- There is no automated runtime package allow-list check for `appliance.kiwi`, nor a dependency-based assertion that build/debug/admin tools are absent from the final AMI.
- There are no tests asserting subprocess stderr, exception strings, credentialed URLs, environment-style values, absolute paths, and user-controlled fields are redacted before logging or response construction.
- There are no tests asserting output attestation generation has a dedicated rate limit.
- There are no tests asserting production startup fails closed when NitroTPM is unavailable.
- I did not inspect a live built AMI, so I could not verify the effective Docker seccomp profile, user-namespace behavior, or SELinux/AppArmor enforcement at runtime.
- I did not perform dependency CVE triage against the locked package set or the mutable upstream build environments.
