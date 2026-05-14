# Implementation Plan: GitHub Actions Remote Executor

## Overview

This implementation plan breaks down the GitHub Actions Remote Executor into discrete coding tasks. The system is an HTTP server running on an Attestable EC2 instance with NitroTPM that executes scripts from GitHub repositories with cryptographic attestation. The implementation follows an asynchronous execution model with polling-based output retrieval.

## Tasks

- [x] Tasks 1–179 (completed)
  - Project structure, configuration (ServerConfig dataclass, env var loading, validation)
  - Data models (ExecutionRequest, ExecutionRecord, ExecutionStatus, AttestationDocument, OutputData, CloneResult)
  - Request validation (repository URL, commit hash, script path with path traversal/absolute/null byte rejection)
  - GitHub repository client (git clone with token, validate script exists, token stripping, .git removal)
  - NitroTPM attestation generator (verify_tpm_available, generate_attestation with user_data/nonce/public_key, output attestation with SHA-256 digest, script_env_hash in user_data)
  - Execution management (UUID generation, status tracking, thread-safe storage, retention cleanup, bounded deque for durations)
  - Output collection (streaming capture, offset-based retrieval, stdout/stderr separation, size limits with truncation)
  - Script executor (Docker container-based execution, streaming log capture via Log_Streaming_Thread, timeout enforcement, container removal verification, dangling cleanup)
  - HTTP server (FastAPI, POST /execute with encrypted payload, POST /execution/{id}/output with encrypted payload, GET /attest, GET /health, rate limiting, error handling)
  - OIDC authentication (PyJWT, JWKS fetch/cache, issuer/audience/repository/expiration validation, repository claim binding, branch restrictions, protected ref enforcement)
  - PQ Hybrid KEM encryption (X25519 + ML-KEM-768 via wolfcrypt-py, length-prefixed composite keys, HKDF-SHA256 key derivation, AES-256-GCM, Encryption_Context lifecycle)
  - Anti-replay nonce cache, concurrency enforcement (503 at capacity), script size enforcement (413)
  - Contextvars-based logging, periodic cleanup scheduling, RateLimiter stale IP cleanup
  - Docker container security (cap_drop=ALL, cap_add for 7 build-script capabilities, no-new-privileges, memory/CPU limits, internet access enabled, mandatory image digest pinning)
  - Rootless Docker migration (gha-executor user, user-scoped systemd, /run/user/{uid}/docker.sock, daemon.json at ~/.config/docker/)
  - KIWI image build infrastructure (Dockerfile, appliance.kiwi, config.sh, build-kiwi-image.sh, PCR measurements, offline Python wheel installation)
  - GitHub Actions workflow (build-attestable-image.yml, ORAS push with annotations, Sigstore attestation, debug image annotation, pinned runner/AL2023)
  - AMI converter (build-ami.py: Terraform provisioning, SSH via paramiko, tool installation, signature verification, artifact download, coldsnap upload, AMI registration, cleanup)
  - Deployment (terraform/deploy with VPC/SG/EC2, deploy.py with Terraform orchestration, port 8080 open to world, optional SSH debug)
  - Cleanup (cleanup.py with --keep-ami, Terraform destroy, AMI deregistration, resource verification)
  - Debug SSH (build-time KIWI flag, deploy-time Terraform variables, workflow_dispatch input, conditional sshd enablement)
  - Security hardening (ORAS checksum verification, coldsnap version pinning, Rust GPG verification, secure SSH key deletion, artifact provenance workflow verification, systemd hardening, IAM permission scoping, build environment pinning, AL2023 mirrorlist pinning)
  - Script environment variable forwarding (script_env dict passed to container, sanitization, script_env_hash in attestation)
  - Health endpoint rate limiting, /metrics removal, simplified /health response
  - Container image pull at server startup (skip if present, verify digest, fail startup on mismatch)
  - 176 property tests (hypothesis) and comprehensive unit/integration tests across all components

- [x] 180. Build rootless Docker dependencies from source
  - [x] 180.1 Update Dockerfile.kiwi-builder with Go toolchain and dev libraries
    - Add `golang` package to the `dnf install` list in `.github/docker/Dockerfile.kiwi-builder` (required for compiling rootlesskit)
    - Add `glib2-devel`, `libslirp-devel`, `libcap-devel`, `libseccomp-devel`, `fuse3-devel` to the `dnf install` list (required for compiling slirp4netns and fuse-overlayfs)
    - Add a comment documenting that these are build-time dependencies for rootless Docker tools compiled from source
    - _Requirements: 11.14, 53.1, 53.2, 53.3, 53.4_

  - [x] 180.2 Update appliance.kiwi package list
    - Remove `rootlesskit`, `slirp4netns`, and `fuse-overlayfs` from the `<packages type="image">` section (they are not available in AL2023 core repos)
    - Retain `uidmap` (available in AL2023 core repos)
    - Add runtime library dependencies: `fuse3`, `libseccomp`, `libslirp`, `glib2`, `libcap`
    - _Requirements: 33.2, 33.11, 33.12, 53.15, 53.16, 53.17_

  - [x] 180.3 Add source compilation section to build-kiwi-image.sh
    - After the wolfcrypt wheel build section and before the KIWI Docker build step, add a new section that runs inside the KIWI builder Docker container to compile rootless Docker dependencies:
    - **rootlesskit** (Go): Clone https://github.com/rootless-containers/rootlesskit at a pinned tag (e.g., `v2.3.1`), build with `go build -o /output/rootlesskit ./cmd/rootlesskit` and `go build -o /output/rootlesskit-docker-proxy ./cmd/rootlesskit-docker-proxy`
    - **slirp4netns** (C/autotools): Clone https://github.com/rootless-containers/slirp4netns at a pinned tag (e.g., `v1.3.3`), build with `./autogen.sh && ./configure --prefix=/usr && make`
    - **fuse-overlayfs** (Rust): Clone https://github.com/containers/fuse-overlayfs at a pinned tag (e.g., `v1.14`), build with `cargo build --release`
    - Copy compiled binaries into `${TEMP_IMAGE_DIR}/root/usr/local/bin/`
    - If any compilation fails, exit with `::error::` and non-zero exit code
    - Add comments documenting each pinned version and how to update
    - _Requirements: 53.5, 53.6, 53.7, 53.8, 53.9, 53.10, 53.11, 53.12, 53.13, 53.14_

  - [x] 180.4 Add binary verification to config.sh
    - In `kiwi-descriptions/config.sh`, after the rootless Docker user setup section and before the Python dependency installation section, add verification that `rootlesskit`, `slirp4netns`, and `fuse-overlayfs` binaries exist and are executable at `/usr/local/bin/`
    - If any binary is missing or not executable, exit with a descriptive error
    - _Requirements: 53.18, 53.19_

  - [x] 180.5 Update property tests for source-compiled rootless Docker binaries
    - Update **Property 116: Docker Package Inclusion in KIWI Image** to verify:
      - `docker` and `uidmap` ARE listed as DNF packages in appliance.kiwi
      - `rootlesskit`, `slirp4netns`, `fuse-overlayfs` are NOT listed as DNF packages
      - Runtime library deps (`fuse3`, `libseccomp`, `libslirp`, `glib2`, `libcap`) ARE listed
    - Add new property test verifying `build-kiwi-image.sh` contains compilation steps for all three tools at pinned versions
    - Add new property test verifying `config.sh` contains binary existence checks for all three tools at `/usr/local/bin/`
    - **Validates: Requirements 33.1, 33.2, 33.11, 33.12, 53.9, 53.14, 53.15, 53.16, 53.17, 53.18, 53.19**

  - [x] 180.6 Write unit tests for rootless Docker source compilation
    - Test that build-kiwi-image.sh contains `git clone` commands for rootlesskit, slirp4netns, and fuse-overlayfs at pinned tags
    - Test that build-kiwi-image.sh contains appropriate build commands (`go build`, `./autogen.sh && ./configure && make`, `cargo build --release`)
    - Test that build-kiwi-image.sh copies binaries to the KIWI image overlay at `/usr/local/bin/`
    - Test that Dockerfile.kiwi-builder includes `golang`, `glib2-devel`, `libslirp-devel`, `libcap-devel`, `libseccomp-devel`, `fuse3-devel`
    - _Requirements: 53.1, 53.2, 53.3, 53.5, 53.6, 53.7, 53.8, 53.9_

- [x] 181. Checkpoint - Ensure rootless Docker source compilation tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 182. Security hardening: Mandatory nonce, request body limits, encrypted error envelopes, execution_id binding, post-clone cleanup, strict boolean parsing, raw filename sanitization, debug gate fail-closed, lockfile-enforced deps, helper source integrity

  - [x] 182.1 Make nonce mandatory on /execute and /execution/{id}/output
    - In `src/server.py`, after decrypting the /execute request payload, validate that the `nonce` field is present and non-empty (not None, not empty string, not whitespace-only); reject with HTTP 400 if missing or empty
    - Apply the same mandatory nonce validation to /execution/{id}/output after decryption
    - The nonce presence check must occur BEFORE the nonce cache duplicate check
    - Update the `DecryptedExecuteRequest` and `DecryptedOutputRequest` models to make `nonce` a required field (remove `Optional` / default `None`)
    - _Requirements: 45.6, 45.7, 45.8, 45.9_

  - [x] 182.2 Add request body size limits
    - Add `max_request_body_bytes` (default 1048576), `max_encrypted_payload_bytes` (default 524288), and `max_decrypted_payload_bytes` (default 262144) to `ServerConfig` in `src/config.py`; read from environment variables MAX_REQUEST_BODY_BYTES, MAX_ENCRYPTED_PAYLOAD_BYTES, MAX_DECRYPTED_PAYLOAD_BYTES
    - In `src/server.py` /execute endpoint: check raw request body length against `max_request_body_bytes` before JSON parsing; reject with HTTP 413 if exceeded
    - After JSON parsing: check `len(body["encrypted_payload"])` against `max_encrypted_payload_bytes` and `len(body["client_public_key"])` against 2048; reject with HTTP 400 if exceeded
    - After decryption: check `len(decrypted_json)` against `max_decrypted_payload_bytes`; reject with HTTP 400 if exceeded
    - Apply the same raw body size check to /execution/{id}/output
    - _Requirements: 8.21, 8.22, 8.23, 8.24, 8.25, 8.26_

  - [x] 182.3 Implement encrypted error envelopes after successful decryption
    - In `src/server.py`, once /execute decryption succeeds (Shared_Key established), wrap all subsequent processing in a try/except that catches application errors and returns them as encrypted JSON envelopes: `{"error": "description", "error_code": 403, "error_details": {...}}`
    - The encrypted error envelope is returned with HTTP 200 at the transport layer (so observers cannot distinguish errors from successes)
    - Errors covered on /execute: OIDC validation (401/403), repository mismatch (403), nonce duplicate (400), validation errors (400), script size exceeded (413), capacity exceeded (503), clone failures, attestation failures
    - Apply the same pattern to /execution/{id}/output after decryption: nonce duplicate (400), execution not found (404), attestation failures
    - Pre-decryption errors (malformed JSON, invalid client_public_key, decryption failure, missing encryption context, body size exceeded) remain as plaintext HTTP errors
    - _Requirements: 42.9, 42.10, 42.11, 42.12_

  - [x] 182.4 Add execution_id to attestation user_data
    - In `src/attestation.py`, add `execution_id` parameter to `generate_attestation()` for execution and output attestation calls
    - Include `execution_id` in the user_data JSON alongside repository_url, commit_hash, script_path, script_env_hash, timestamp
    - In `src/server.py`, pass the execution_id when generating the /execute response attestation and the /execution/{id}/output attestation
    - Update the attestation user_data schema documentation to include execution_id (string, UUID v4)
    - _Requirements: 4.8, 4.16, 4.17, 4.18, 4.19, 4.20, 4.21, 6.14_

  - [x] 182.5 Add post-clone resource cleanup on unexpected errors
    - In `src/server.py` /execute handler, wrap all post-clone processing (script size validation, attestation generation, execution record creation, async execution handoff) in a `try/finally` block
    - The `finally` block removes the clone directory with `shutil.rmtree(clone_path, ignore_errors=True)` ONLY if ownership has NOT been handed to the Script_Executor
    - Use a boolean flag (e.g., `execution_handed_off = False`) set to `True` just before `execute_async()` is called; the `finally` block checks this flag
    - _Requirements: 3.13, 3.14, 3.15, 3.16_

  - [x] 182.6 Implement strict boolean configuration parsing
    - In `src/config.py`, replace the current boolean parsing logic (which treats unrecognized values as `False`) with a strict parser that only accepts: `true`, `1`, `yes` (case-insensitive) → True; `false`, `0`, `no` (case-insensitive) → False
    - If the value is not in the recognized set, raise a `ValueError` with a message including the config key name, the invalid value, and the list of accepted values
    - Apply this strict parsing to all boolean config values (REQUIRE_PROTECTED_REF and any future booleans)
    - The server should fail to start if an invalid boolean value is provided
    - _Requirements: 9.13, 9.14, 9.15_

  - [x] 182.7 Add raw image filename sanitization to build-ami.py
    - In `scripts/build-ami.py`, replace the `ls *.raw` shell command with a programmatic directory listing (Python `glob.glob` or `os.listdir` with filtering)
    - Enforce exactly one `.raw` file; terminate with error if zero or more than one found
    - Validate the basename against regex `^[a-zA-Z0-9][a-zA-Z0-9._-]*\.raw$`; terminate with error if it doesn't match
    - Use `shlex.quote()` when interpolating the filename into shell commands, or use subprocess list arguments to avoid shell interpolation entirely
    - _Requirements: 20.1, 20.2, 20.3, 20.4, 20.5_

  - [x] 182.8 Make debug-image production gate fail closed
    - In `scripts/build-ami.py`, update the debug annotation check: if `oras manifest fetch` fails (non-zero exit code) or JSON parsing of the manifest fails, terminate with an error indicating the debug status could not be verified
    - Only proceed when `--allow-debug` is explicitly provided in the indeterminate case
    - Remove the current "log warning and proceed" behavior on fetch/parse failure
    - _Requirements: 46.6, 46.7, 46.8, 46.9_

  - [x] 182.9 Implement lockfile-enforced Python dependency installation
    - In `.github/scripts/build-kiwi-image.sh`, replace the `pip3 download` from `pyproject.toml` version ranges with a lockfile-enforced path:
      - Export from `uv.lock` using `uv export --frozen --format requirements-txt --no-dev` (hashes are included by default)
      - Install using `pip install --require-hashes -r <exported-requirements> --no-index --find-links <wheels-dir>`
    - Alternatively use `uv sync --frozen` if uv is available in the build context
    - Fail the build if `uv.lock` is missing or the `--frozen` export fails
    - _Requirements: 12.19, 12.20, 12.21, 12.22, 12.23_

  - [x] 182.10 Add rootless Docker helper source integrity verification
    - In `.github/scripts/build-kiwi-image.sh`, replace mutable release tags with immutable full commit SHAs (40-character hex) for rootlesskit, slirp4netns, and fuse-overlayfs
    - After each `git clone`, verify `git rev-parse HEAD` matches the expected SHA; exit with error if mismatch
    - In `.github/docker/Dockerfile.kiwi-builder`, use a release tarball download (via `curl --retry 3 --retry-delay 5`) for libslirp instead of `git clone` to improve resilience against transient GitLab server errors; pin by release tag in the tarball URL
    - Add SHA-256 checksum verification for the `dockerd-rootless.sh` download; exit with error if mismatch
    - Document each pinned commit SHA with a comment indicating the corresponding release tag and date
    - _Requirements: 53.5, 53.21, 53.22, 53.23, 53.24_

  - [x] 182.11 Pin gha-executor UID to 1000 and add LimitCORE=0
    - In `kiwi-descriptions/config.sh`, update the `useradd` command for `gha-executor` to include `--uid 1000` explicitly
    - In `kiwi-descriptions/root/etc/systemd/system/github-actions-remote-executor.service`, add `LimitCORE=0` to the `[Service]` section
    - _Requirements: 33.3, 49.14, 49.15_

  - [x] 182.12 Pass config.max_output_size_bytes to OutputCollector
    - In `src/server.py`, update the `OutputCollector()` constructor call to pass `config.max_output_size_bytes` as a parameter
    - In `src/output_collector.py`, update the `__init__` method to accept `max_output_size_bytes` parameter and use it instead of a hardcoded default
    - _Requirements: 5.17, 5.18_

  - [x] 182.13 Write tests for all security hardening changes
    - **Mandatory nonce tests**: Verify /execute rejects requests without nonce (HTTP 400), with empty nonce (HTTP 400), with whitespace-only nonce (HTTP 400); same for /execution/{id}/output
    - **Body size limit tests**: Verify oversized request body returns HTTP 413; oversized encrypted_payload returns HTTP 400; oversized client_public_key returns HTTP 400; oversized decrypted payload returns HTTP 400
    - **Encrypted error envelope tests**: Verify post-decryption OIDC failure returns encrypted envelope with HTTP 200 (not plaintext 401); verify pre-decryption errors still return plaintext HTTP errors
    - **Execution_id binding tests**: Verify execution_id appears in /execute attestation user_data matching the response; verify execution_id appears in /output attestation user_data matching the URL path
    - **Post-clone cleanup tests**: Simulate unexpected exception after clone but before execution handoff; verify clone directory is removed
    - **Strict boolean tests**: Verify `treu`, `enabled`, `on`, `yess` cause startup failure; verify `true`, `false`, `1`, `0`, `yes`, `no` work correctly
    - **Raw filename tests**: Verify filenames with shell metacharacters (`; rm -rf /`, `$(cmd)`, backticks) are rejected; verify exactly-one-file enforcement
    - **Debug gate fail-closed tests**: Verify manifest fetch failure terminates without `--allow-debug`; verify JSON parse failure terminates without `--allow-debug`
    - **Lockfile tests**: Verify build script uses `uv export --frozen` or equivalent rather than `pip3 download` from version ranges
    - **Source integrity tests**: Verify rootlesskit, slirp4netns, and fuse-overlayfs are pinned to immutable commit SHAs; verify libslirp uses a release tarball download with curl retry in Dockerfile; verify dockerd-rootless.sh has checksum verification
    - **OutputCollector config test**: Verify a configured lower MAX_OUTPUT_SIZE_BYTES is enforced
    - **UID pinning test**: Verify config.sh creates gha-executor with --uid 1000; verify systemd unit has LimitCORE=0
    - _Requirements: 3.16, 4.19, 4.20, 4.21, 5.18, 8.21-8.26, 9.15, 12.19-12.23, 20.1-20.5, 42.12, 45.8, 45.9, 46.9, 49.16, 49.17, 53.21-53.24_

- [x] 183. Checkpoint - Ensure all security hardening tests pass
  - Run the full test suite and verify all new tests from 182.13 pass
  - Verify no regressions in existing tests

## Notes

- Each task references specific requirements for traceability
- Property tests validate the 176 correctness properties from the design document
- The runtime implementation (tasks 1-16) uses Python with FastAPI for the HTTP server
- The build implementation (tasks 17-31) uses GitHub Actions, KIWI NG, ORAS, Terraform, and Python
- The deployment implementation (tasks 32-36) uses Terraform and Python to provision the target EC2 instance and supporting infrastructure
- The cleanup implementation (tasks 37-38) covers testing the existing scripts/cleanup.py which is already fully implemented
- The debug SSH implementation (tasks 39-47) adds opt-in SSH debug access across build-time (KIWI image), deploy-time (Terraform + deploy script), and GitHub Actions workflow
- Python dependencies are separated into two configurations:
  - pyproject.toml: Remote executor service dependencies (fastapi, uvicorn, requests, docker, hypothesis, pytest, pytest-asyncio, httpx)
  - scripts/pyproject.toml: Build/deployment script dependencies (boto3, paramiko)
  - The remote executor does NOT use boto3 - it only runs on the EC2 instance and doesn't interact with AWS APIs
  - boto3 is ONLY used by build/deployment scripts (build-ami.py, cleanup.py, deploy.py) that run outside the KIWI image
  - cleanup.py uses boto3 from scripts/pyproject.toml (same dependency configuration as build-ami.py and deploy.py)
  - When building the KIWI image, only dependencies from pyproject.toml are installed via config.sh script
  - The KIWI config.sh phase has no network access; dependency wheels are pre-downloaded by build-kiwi-image.sh (which has network) and installed offline using pip3 install --no-index --find-links
  - build-kiwi-image.sh extracts the dependency list dynamically from pyproject.toml using tomllib — package names are never hardcoded in the build script
  - No uv package manager is needed inside the KIWI image
  - Dependency installation occurs during KIWI image build phase, before image finalization
- Debug SSH feature requires coordination between build-time and deploy-time: both --enable-ssh flags must be used for SSH to work end-to-end
- SSH key provisioning uses cloud-init and ec2-instance-connect (no baked-in keys)
- NitroTPM attestation requires running on an Attestable EC2 instance with NitroTPM
- Docker container execution replaces direct subprocess execution: each script runs in an ephemeral container with memory limits, CPU limits, writable root filesystem, no privilege escalation, root user, and internet access enabled by default (no network_mode restriction) since scripts may need to download dependencies or upload artifacts
- Internet access change (task 169): Removes `network_mode="none"` from container creation so scripts can download dependencies or upload artifacts to artifact stores; all other security constraints (cap_drop=ALL with cap_add for build-script capabilities, writable root filesystem, no-new-privileges, root user, memory/CPU limits) remain unchanged
- Docker SDK (`docker` Python package) manages container lifecycle: create, run, capture output, remove, and verify removal
- Container naming convention uses `gare-exec-{execution_id}` prefix for identification and dangling cleanup
- OIDC authentication (tasks 58-65) adds GitHub Actions OIDC JWT validation for request authentication
- PyJWT[crypto] is used for JWT decoding and JWKS-based signature verification
- OIDC tokens are validated for signature (JWKS), issuer, audience, repository, and expiration claims
- Protected endpoint /execute requires OIDC token in encrypted body; /execution/{id}/output authenticates via Shared_Key possession (no OIDC token needed); /health remains unauthenticated
- Docker daemon provisioning (tasks 74-75) adds the docker package to the KIWI image and enables the docker service so the Script_Executor can manage Execution_Containers at runtime
- Git package provisioning (task 90) adds the git package to the KIWI image so the Repository_Client can clone repositories at runtime using git commands
- Container image pre-pull (tasks 76-79) originally baked the configured Container_Image into the KIWI image during build; tasks 80-84 reverse this by removing the build-time pre-pull code and implementing server-startup pull instead — the GHA_Server now pulls the Container_Image from the registry at startup before accepting requests
- All 176 properties should be tested with hypothesis library (minimum 100 iterations each)
- Checkpoints ensure incremental validation throughout implementation
- Build tasks (17-32) can be implemented independently from runtime tasks (1-16)
- AMI build process uses Terraform to provision temporary EC2 infrastructure with complete VPC/networking setup
- Build instance uses Amazon Linux 2023 with IMDSv2 enforcement
- Signature verification is mandatory before AMI creation - no bypass mechanism
- Tool installation includes specific versions: ORAS 1.3.0, Rust via GPG-verified standalone tarball, GitHub CLI via dnf, coldsnap from source at pinned tag
- Coldsnap installed to /home/ec2-user/.cargo/bin/coldsnap (full path required for execution)
- SSH connectivity uses paramiko with keepalive (30s intervals) and retries (10 attempts, 30s delay)
- Infrastructure cleanup guaranteed via finally block, executes even on build failure
- Terraform state isolated per build for concurrent build support
- Deployment Terraform (terraform/deploy/) creates persistent infrastructure unlike build Terraform which is temporary
- Deployment VPC uses CIDR 10.0.0.0/16 (distinct from build VPC 10.2.0.0/16)
- Target instance has port 8080 open to the world (0.0.0.0/0); authentication is handled at the application layer via PQ Hybrid KEM + OIDC
- When SSH debug is enabled, port 22 is restricted to the deployer's IP via `allowed_ssh_cidr`
- IMDSv2 enforced on target instance with http_tokens = "required" and hop limit = 1
- NitroTPM automatically enabled via AMI registration settings (TpmSupport = v2.0, BootMode = uefi)
- Deploy script only detects user IP when `--enable-ssh` is provided (for SSH CIDR whitelisting); HTTP access does not use IP whitelisting
- The `allowed_http_cidr` Terraform variable has been removed from terraform/deploy/; replaced by `allowed_ssh_cidr` (only used when `enable_ssh = true`)
- Cleanup script's terraform destroy no longer passes `allowed_http_cidr` as a dummy variable
- On deployment failure, user must manually run terraform destroy (no automated cleanup — infrastructure is meant to persist)
- Cleanup script (scripts/cleanup.py) is already fully implemented; tasks 37-38 focus exclusively on writing property and unit tests for the existing code
- Cleanup script supports --keep-ami flag to skip AMI deregistration and snapshot deletion while still destroying Terraform infrastructure (tasks 38a-38b)
- Cleanup script uses subprocess to invoke Terraform and boto3 for AWS API calls (deregister AMI, describe resources)
- Cleanup verification checks for EC2 instances, AMIs, and EBS snapshots using project-specific tags and resource IDs
- PQ Hybrid KEM encryption (tasks 91-124) provides end-to-end encryption using X25519 + ML-KEM-768 for post-quantum resistance; the `wolfcrypt-py` package (via `wolfcrypt.ciphers` module: `MlKemType`, `MlKemPrivate`, `MlKemPublic`) provides FIPS 203 ML-KEM-768 key generation, encapsulation, and decapsulation
- Security hardening (tasks 125-168, 182) implements fixes from the security review: OIDC repository claim binding, extended OIDC claim restrictions, token stripping/.git removal, output buffer limits, execution output repository binding, contextvars logging, concurrency enforcement, script size enforcement, periodic cleanup, capability drop-and-add-back (cap_drop=ALL then cap_add for 7 build-script capabilities: CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, NET_BIND_SERVICE, KILL), health endpoint hardening, /metrics removal, anti-replay nonce cache, /attest rate limiting, container image digest pinning, artifact ref validation, ORAS checksum verification, coldsnap pinning, secure SSH key deletion, debug image annotation, artifact provenance workflow verification, Docker daemon security configuration, systemd service hardening, AMI build IAM permission scoping, build environment pinning, mandatory nonces, request body size limits, encrypted error envelopes, execution_id binding in attestations, post-clone cleanup, strict boolean parsing, raw filename sanitization, debug gate fail-closed, lockfile-enforced deps, and helper source integrity verification
- The `cryptography` library (already included via PyJWT[crypto]) provides X25519 key generation support; `wolfcrypt-py` provides ML-KEM-768 support
- Server_Keypair is a composite key (X25519 + ML-KEM-768) generated once at startup and held in memory; never persisted to disk
- Server_Public_Key is serialized as length-prefixed concatenation (4-byte big-endian length + component bytes) of X25519 public key (32 bytes) + ML-KEM-768 encapsulation key (1184 bytes)
- SHA-256 fingerprint of the composite Server_Public_Key is used in attestation documents because the composite key exceeds the 1024-byte public_key field limit
- /attest is the only endpoint that includes Server_Public_Key in attestation documents; /execute and /output attestations exclude it
- /attest attestation documents do NOT include user_data — only public_key and optional nonce are included
- OIDC token is included in encrypted request body `oidc_token` field on /execute only; /execution/{id}/output does not require or validate OIDC tokens (Shared_Key possession serves as authentication)
- /execution/{id}/output changes from GET to POST to support encrypted request bodies
- Encryption_Context (Shared_Key per execution_id) is stored in memory and cleaned up with execution records
- /attest, /health remain unencrypted plain JSON endpoints
- Output attestation every-poll change (tasks 125-128): Output_Attestation_Document is now generated on EVERY /execution/{id}/output poll response, not just when execution is complete; the SHA-256 digest covers the current Script_Output (stdout + stderr + exit_code) at the time of each poll regardless of execution status (running, completed, failed, timed_out)
- Streaming output capture (tasks 129-130): Replaces the batch log capture pattern (capture all output after container.wait() returns) with a Log_Streaming_Thread that uses `container.logs(stream=True, follow=True)` to incrementally feed output chunks to the Output_Collector during execution; this ensures polling clients observe partial output while the script is still running rather than seeing empty output until the container exits
- Rootless Docker migration (task 174): Migrates from rootful Docker (system-wide daemon at /var/run/docker.sock) to rootless Docker running as `gha-executor` user; daemon.json moves to `~gha-executor/.config/docker/daemon.json`, Docker socket moves to `/run/user/{uid}/docker.sock`, and the systemd service unit runs as `gha-executor` with `ProtectHome=read-only`
- Mandatory container image digest pinning (task 176): Changes CONTAINER_IMAGE_DIGEST from optional to required; server fails startup if digest is empty and CONTAINER_IMAGE does not contain `@sha256:`; removes the "skip if no digest" code path from pull_container_image
- Attestation user_data schema with script_env_hash (task 178): Adds `script_env_hash` field to attestation user_data containing SHA-256 hex digest of canonicalized script_env (sorted keys, compact JSON); enables consumers to verify no unexpected environment variables were injected
- Encrypted error envelopes (task 182.3): After successful PQ_Hybrid_KEM decryption, ALL application errors are returned as encrypted JSON envelopes with HTTP 200 at the transport layer; pre-decryption errors remain plaintext HTTP responses
- Mandatory nonces (task 182.1): Nonce field is required (not optional) on both /execute and /execution/{id}/output encrypted payloads; missing or empty nonces are rejected with HTTP 400 before the nonce cache check
- Request body size limits (task 182.2): MAX_REQUEST_BODY_BYTES (1 MB), MAX_ENCRYPTED_PAYLOAD_BYTES (512 KB), MAX_DECRYPTED_PAYLOAD_BYTES (256 KB), and client_public_key max (2048 bytes) are enforced at progressive stages before expensive processing
- Execution_id in attestation user_data (task 182.4): Both execution and output attestation documents now include `execution_id` in user_data, enabling consumers to cryptographically bind attestations to specific server execution records
- Post-clone cleanup (task 182.5): A `finally` block ensures cloned repository directories are removed on unexpected exceptions after cloning but before the Script_Executor takes ownership
- Strict boolean parsing (task 182.6): Boolean config values only accept true/1/yes/false/0/no; unrecognized values (typos) cause startup failure instead of silently defaulting to false
- Raw filename sanitization (task 182.7): The AMI build script enumerates .raw files programmatically, enforces exactly one file, validates the basename against a strict regex, and uses shlex.quote() or subprocess list arguments
- Debug gate fail-closed (task 182.8): If the debug annotation cannot be determined (manifest fetch or JSON parse failure), the AMI build terminates unless --allow-debug is explicitly provided
- Lockfile-enforced deps (task 182.9): Python dependencies in the AMI are installed from uv.lock via hash-checked export, not from pyproject.toml version ranges
- Helper source integrity (task 182.10): Rootless Docker helpers (rootlesskit, slirp4netns, fuse-overlayfs) are pinned to immutable commit SHAs with post-clone verification; libslirp uses a release tarball download with curl retry logic for CI resilience; dockerd-rootless.sh is checksum-verified

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["180.1"] },
    { "id": 1, "tasks": ["180.2", "180.3"] },
    { "id": 2, "tasks": ["180.4"] },
    { "id": 3, "tasks": ["180.5", "180.6"] },
    { "id": 4, "tasks": ["181"] },
    { "id": 5, "tasks": ["182.1", "182.2", "182.4", "182.5", "182.6", "182.7", "182.8", "182.9", "182.10", "182.11", "182.12"] },
    { "id": 6, "tasks": ["182.3"] },
    { "id": 7, "tasks": ["182.13"] },
    { "id": 8, "tasks": ["183"] }
  ]
}
```
