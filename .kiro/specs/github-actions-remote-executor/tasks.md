# Implementation Plan: GitHub Actions Remote Executor

## Overview

This implementation plan breaks down the GitHub Actions Remote Executor into discrete coding tasks. The system is an HTTP server running on an Attestable EC2 instance with NitroTPM that executes scripts from GitHub repositories with cryptographic attestation. The implementation follows an asynchronous execution model with polling-based output retrieval.

## Tasks

- [x] 1. Tasks 1–183 (completed): Project structure, configuration, data models, request validation, repository client, attestation generator, execution management, output collection, script executor, HTTP server, OIDC authentication, PQ Hybrid KEM encryption, anti-replay nonce cache, concurrency enforcement, contextvars logging, Docker container security, rootless Docker migration, KIWI image build infrastructure, GitHub Actions workflow, AMI converter, deployment, cleanup, debug SSH, security hardening (mandatory nonces, request body limits, encrypted error envelopes, execution_id binding, post-clone cleanup, strict boolean parsing, raw filename sanitization, debug gate fail-closed, lockfile-enforced deps, helper source integrity, UID pinning, LimitCORE=0, OutputCollector config passthrough), script environment variable forwarding, health endpoint hardening, container image digest pinning, rootless Docker dependencies built from source, and comprehensive property/unit/integration tests

- [ ] 184. Security hardening: Immutable artifact digest pinning, credential isolation, strict nonce/base64 validation, CI action pinning, script_env deny-list, symlink-safe script validation, runtime image minimization, log sanitization

  - [x] 184.1 Require digest-pinned artifact references in build-ami.py
    - Update `validate_artifact_reference()` regex in `scripts/build-ami.py` to accept and require `@sha256:<hex64>` in the artifact reference (in addition to or instead of `:tag`)
    - If artifact_ref does not contain `@sha256:`, terminate with error indicating digest-pinned references are required
    - Update `verify_artifact_signature()` to verify against the exact `sha256:` digest from the artifact reference, not a mutable tag
    - Update `pull_artifact_from_ghcr()` to pull using the exact `sha256:` digest (e.g., `oras pull ghcr.io/owner/repo/package@sha256:abc...`)
    - If both tag and digest are present, use only the digest for verification and pull
    - _Requirements: 15.17, 15.18, 15.19, 15.20, 15.21_

  - [x] 184.2 Output digest-pinned artifact reference from Build Workflow
    - In `.github/workflows/build-attestable-image.yml`, after ORAS push, capture the full digest-pinned reference (including `@sha256:`) and output it as a workflow output for downstream consumers
    - _Requirements: 15.22_

  - [x] 184.3 Implement git clone credential isolation
    - In `src/repository.py`, replace the token-in-URL clone mechanism with a `GIT_ASKPASS` helper script (or `http.extraHeader` via environment-scoped git config) that provides the token without exposing it in subprocess argv
    - Create a temporary helper script that echoes the token on demand; pass its path via `GIT_ASKPASS` environment variable to the `git clone` subprocess
    - Clean up the helper script in a `finally` block after clone completes (regardless of success/failure)
    - Keep the existing token stripping from `.git/config` as defense-in-depth
    - _Requirements: 3.17_

  - [x] 184.4 Add symlink-safe script path validation
    - In `src/repository.py` `validate_script_exists()`, after confirming the file exists, check `os.path.islink()` on the full script path; reject with error if it's a symlink
    - Resolve the full script path using `os.path.realpath()` and verify the resolved path starts with the clone directory path; reject if it escapes
    - _Requirements: 3.18, 3.19_

  - [x] 184.5 Add strict nonce type, length, and format validation
    - In `src/server.py`, after extracting the nonce from the decrypted payload (on both /execute and /output), validate:
      - Type is `str` (reject int, bool, list, object, null with HTTP 400)
      - Length is between 16 and 256 characters inclusive (reject with HTTP 400)
      - Contains only URL-safe characters: `[a-zA-Z0-9._~-]` (reject with HTTP 400)
    - These checks must occur BEFORE the nonce cache duplicate check
    - _Requirements: 45.10, 45.11, 45.12_

  - [ ] 184.6 Add strict base64 decoding with validate=True
    - In `src/server.py`, update all `base64.b64decode()` calls on `encrypted_payload` and `client_public_key` fields to pass `validate=True`
    - If decoding fails due to malformed base64, return HTTP 400 with error indicating invalid base64 encoding
    - _Requirements: 45.13_

  - [ ] 184.7 Pin GitHub Actions to commit SHAs
    - In `.github/workflows/build-attestable-image.yml`, replace all `uses: action@vN` references with `uses: action@<full-40-char-sha>`
    - Add a comment next to each pinned SHA indicating the version tag and date (e.g., `# v4.2.0 - 2024-10-15`)
    - _Requirements: 11.15_

  - [ ] 184.8 Pin Dockerfile base image to @sha256: digest
    - In `.github/docker/Dockerfile.kiwi-builder`, append `@sha256:<digest>` to the `FROM` directive
    - Add a comment documenting the tag corresponding to the pinned digest
    - _Requirements: 11.16_

  - [ ] 184.9 Add libslirp tarball SHA-256 checksum verification
    - In `.github/docker/Dockerfile.kiwi-builder`, after downloading the libslirp release tarball, verify its SHA-256 checksum against a known expected value before extraction
    - If checksum does not match, fail the build with an integrity error
    - _Requirements: 53.6_

  - [ ] 184.10 Implement script_env deny-list
    - Add `script_env_deny_list` to `ServerConfig` in `src/config.py`; read from `SCRIPT_ENV_DENY_LIST` env var (comma-separated); default to: `BASH_ENV,ENV,SHELLOPTS,BASHOPTS,BASH_FUNC_*,PATH,LD_PRELOAD,LD_LIBRARY_PATH,PROMPT_COMMAND,PS1,PS2,PS4,IFS,CDPATH,GLOBIGNORE,BASH_XTRACEFD`
    - In `src/server.py`, after sanitizing `script_env` (string keys/values), check each key against the deny-list (exact match + prefix match for `*` entries); reject with encrypted error if any key matches
    - _Requirements: 52.7, 52.8, 52.9_

  - [ ] 184.11 Remove unjustified packages from appliance.kiwi
    - Remove `awscli`, `binutils`, `python3.11-pip`, and `pciutils` from `<packages type="image">` in `kiwi-descriptions/appliance.kiwi`
    - Add a comment block at the top of the packages section documenting the allow-list policy
    - Add explicit justification comment next to `git` (required by Repository_Client)
    - Evaluate `tar` and `gzip` — keep if required as transitive deps of Docker, remove otherwise
    - _Requirements: 54.1, 54.2, 54.3, 54.4, 54.5, 54.6, 54.7, 54.8_

  - [ ] 184.12 Implement log and error response sanitization
    - Create or update `src/logging_config.py` with a `LogSanitizer` class that redacts: GitHub tokens (ghp_*, ghs_*, github_pat_*), credentialed URLs (https://*@*), Authorization header values, absolute file paths, environment variable assignments containing tokens, and ASCII control characters
    - In `src/server.py` and `src/repository.py`, pass all subprocess stderr and exception messages through the sanitizer before logging or including in error responses
    - Apply length cap (256 chars) to user-controlled log fields; truncate with `[truncated]` suffix
    - Replace raw nonce logging with truncated hash/prefix (first 8 chars)
    - Ensure encrypted error envelopes contain only categorized descriptions (e.g., "clone_failed") without raw stderr or paths
    - _Requirements: 7.12, 7.13, 7.14, 7.15, 7.16, 7.17, 7.18_

  - [ ] 184.13 Write tests for all new security hardening changes
    - **Digest pinning tests**: Verify artifact refs without `@sha256:` are rejected; verify verification and pull use same digest; verify tag-only refs are rejected
    - **Credential isolation tests**: Verify git clone subprocess args do not contain the token; verify helper is cleaned up after clone; verify clone succeeds with new mechanism
    - **Symlink tests**: Verify symlink script paths are rejected; verify paths escaping clone dir via symlinks are rejected
    - **Strict nonce tests**: Verify non-string nonce rejected (int, bool, list); verify nonce <16 chars rejected; verify nonce >256 chars rejected; verify nonce with control chars rejected
    - **Strict base64 tests**: Verify malformed base64 in encrypted_payload rejected with HTTP 400
    - **CI pinning tests**: Verify no `uses:` directives reference mutable tags (must have 40-char hex after @); verify Dockerfile FROM contains `@sha256:`
    - **Script_env deny-list tests**: Verify BASH_ENV rejected; verify PATH rejected; verify LD_PRELOAD rejected; verify BASH_FUNC_* prefix rejected; verify GITHUB_TOKEN accepted
    - **Package minimization tests**: Verify awscli, binutils, python3.11-pip, pciutils are NOT in appliance.kiwi packages
    - **Log sanitization tests**: Verify subprocess stderr containing GitHub token is redacted; verify error responses don't contain absolute paths; verify multi-line input is escaped; verify nonce values not logged verbatim
    - _Requirements: 3.20, 7.19, 11.17, 15.23, 45.14, 52.11, 54.9_

- [ ] 185. Checkpoint - Ensure all security hardening round 2 tests pass
  - Run the full test suite and verify all new tests from 184.13 pass
  - Verify no regressions in existing tests

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["184.1", "184.3", "184.4", "184.5", "184.6", "184.7", "184.8", "184.9", "184.10", "184.11", "184.12"] },
    { "id": 1, "tasks": ["184.2"] },
    { "id": 2, "tasks": ["184.13"] },
    { "id": 3, "tasks": ["185"] }
  ]
}
```

## Notes

- Tasks 1-183 cover the full initial implementation including: project structure, configuration, data models, request validation, repository client (git clone with token-in-URL, token stripping, .git removal), attestation generator, execution management, output collection, script executor (Docker containers with cap_drop=ALL + minimal cap_add, streaming log capture, timeout enforcement), HTTP server (FastAPI with encrypted endpoints), OIDC authentication (PyJWT, JWKS, all claim validation), PQ Hybrid KEM encryption (X25519 + ML-KEM-768, HKDF-SHA256, AES-256-GCM), anti-replay nonce cache, rootless Docker migration, KIWI image build infrastructure, GitHub Actions workflow, AMI converter, deployment, cleanup, debug SSH, and comprehensive security hardening (mandatory nonces, request body limits, encrypted error envelopes, execution_id binding, post-clone cleanup, strict boolean parsing, raw filename sanitization, debug gate fail-closed, lockfile-enforced deps, helper source integrity, UID pinning, LimitCORE=0)
- Tasks 184+ address findings from the 2026-05-15 security review that were not covered by the initial implementation
- The credential isolation change (184.3) replaces the token-in-URL approach with GIT_ASKPASS to prevent token exposure in /proc/<pid>/cmdline
- The symlink validation (184.4) prevents repository-controlled symlinks from causing the server to read files outside the clone directory
- The strict nonce validation (184.5) prevents non-string types, too-short/too-long values, and non-URL-safe characters from reaching the nonce cache
- The strict base64 validation (184.6) prevents malformed encodings from being silently normalized before decryption
- The CI action pinning (184.7) and base image digest pinning (184.8) prevent tag-movement attacks on the build environment
- The libslirp checksum (184.9) closes the last unsigned source input in the Dockerfile
- The script_env deny-list (184.10) prevents callers from injecting BASH_ENV, PATH, LD_PRELOAD and other execution-altering variables
- The package minimization (184.11) removes awscli, binutils, python3.11-pip, pciutils from the runtime image to reduce post-compromise tooling
- The log sanitization (184.12) prevents GitHub tokens, credentialed URLs, absolute paths, and raw subprocess stderr from appearing in logs or error responses
- The digest pinning (184.1, 184.2) ensures the AMI build verifies and pulls the exact same immutable artifact, preventing TOCTOU attacks via tag movement
