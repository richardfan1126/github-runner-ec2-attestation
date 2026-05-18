# Implementation Plan: GitHub Actions Remote Executor

## Overview

This implementation plan breaks down the GitHub Actions Remote Executor into discrete coding tasks. The system is an HTTP server running on an Attestable EC2 instance with NitroTPM that executes scripts from GitHub repositories with cryptographic attestation. The implementation follows an asynchronous execution model with polling-based output retrieval.

## Tasks

- [x] 1. Tasks 1–183 (completed): Project structure, configuration, data models, request validation, repository client, attestation generator, execution management, output collection, script executor, HTTP server, OIDC authentication, PQ Hybrid KEM encryption, anti-replay nonce cache, concurrency enforcement, contextvars logging, Docker container security, rootless Docker migration, KIWI image build infrastructure, GitHub Actions workflow, AMI converter, deployment, cleanup, debug SSH, security hardening (mandatory nonces, request body limits, encrypted error envelopes, execution_id binding, post-clone cleanup, strict boolean parsing, raw filename sanitization, debug gate fail-closed, lockfile-enforced deps, helper source integrity, UID pinning, LimitCORE=0, OutputCollector config passthrough), script environment variable forwarding, health endpoint hardening, container image digest pinning, rootless Docker dependencies built from source, and comprehensive property/unit/integration tests

- [x] 184. Security hardening: Immutable artifact digest pinning, credential isolation, strict nonce/base64 validation, CI action pinning, script_env deny-list, symlink-safe script validation, runtime image minimization, log sanitization

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

  - [x] 184.6 Add strict base64 decoding with validate=True
    - In `src/server.py`, update all `base64.b64decode()` calls on `encrypted_payload` and `client_public_key` fields to pass `validate=True`
    - If decoding fails due to malformed base64, return HTTP 400 with error indicating invalid base64 encoding
    - _Requirements: 45.13_

  - [x] 184.7 Pin GitHub Actions to commit SHAs
    - In `.github/workflows/build-attestable-image.yml`, replace all `uses: action@vN` references with `uses: action@<full-40-char-sha>`
    - Add a comment next to each pinned SHA indicating the version tag and date (e.g., `# v4.2.0 - 2024-10-15`)
    - _Requirements: 11.15_

  - [x] 184.8 Pin Dockerfile base image to @sha256: digest
    - In `.github/docker/Dockerfile.kiwi-builder`, append `@sha256:<digest>` to the `FROM` directive
    - Add a comment documenting the tag corresponding to the pinned digest
    - _Requirements: 11.16_

  - [x] 184.9 Add libslirp tarball SHA-256 checksum verification
    - In `.github/docker/Dockerfile.kiwi-builder`, after downloading the libslirp release tarball, verify its SHA-256 checksum against a known expected value before extraction
    - If checksum does not match, fail the build with an integrity error
    - _Requirements: 53.6_

  - [x] 184.10 Implement script_env deny-list
    - Add `script_env_deny_list` to `ServerConfig` in `src/config.py`; read from `SCRIPT_ENV_DENY_LIST` env var (comma-separated); default to: `BASH_ENV,ENV,SHELLOPTS,BASHOPTS,BASH_FUNC_*,PATH,LD_PRELOAD,LD_LIBRARY_PATH,PROMPT_COMMAND,PS1,PS2,PS4,IFS,CDPATH,GLOBIGNORE,BASH_XTRACEFD`
    - In `src/server.py`, after sanitizing `script_env` (string keys/values), check each key against the deny-list (exact match + prefix match for `*` entries); reject with encrypted error if any key matches
    - _Requirements: 52.7, 52.8, 52.9_

  - [x] 184.11 Remove unjustified packages from appliance.kiwi
    - Remove `awscli`, `binutils`, `python3.11-pip`, and `pciutils` from `<packages type="image">` in `kiwi-descriptions/appliance.kiwi`
    - Add a comment block at the top of the packages section documenting the allow-list policy
    - Add explicit justification comment next to `git` (required by Repository_Client)
    - Evaluate `tar` and `gzip` — keep if required as transitive deps of Docker, remove otherwise
    - _Requirements: 54.1, 54.2, 54.3, 54.4, 54.5, 54.6, 54.7, 54.8_

  - [x] 184.12 Implement log and error response sanitization
    - Create or update `src/logging_config.py` with a `LogSanitizer` class that redacts: GitHub tokens (ghp_*, ghs_*, github_pat_*), credentialed URLs (https://*@*), Authorization header values, absolute file paths, environment variable assignments containing tokens, and ASCII control characters
    - In `src/server.py` and `src/repository.py`, pass all subprocess stderr and exception messages through the sanitizer before logging or including in error responses
    - Apply length cap (256 chars) to user-controlled log fields; truncate with `[truncated]` suffix
    - Replace raw nonce logging with truncated hash/prefix (first 8 chars)
    - Ensure encrypted error envelopes contain only categorized descriptions (e.g., "clone_failed") without raw stderr or paths
    - _Requirements: 7.12, 7.13, 7.14, 7.15, 7.16, 7.17, 7.18_

  - [x] 184.13 Write tests for all new security hardening changes
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

- [x] 185. Checkpoint - Ensure all security hardening round 2 tests pass
  - Run the full test suite and verify all new tests from 184.13 pass
  - Verify no regressions in existing tests

- [x] 186. Add build-time-only packages via KIWI `<packages type="uninstall">` and retain `binutils` for KIWI create step
  - [x] 186.1 Add `python3.11-pip` to `<packages type="image">` in `kiwi-descriptions/appliance.kiwi`
    - Add `<package name="python3.11-pip"/>` to the `<packages type="image">` section with a comment explaining it is a build-time dependency required by `config.sh` for `pip3.11 install` and will be removed by the uninstall section
    - _Requirements: 54.1, 54.2_

  - [x] 186.2 Add `<packages type="uninstall">` section to `kiwi-descriptions/appliance.kiwi`
    - Add a new `<packages type="uninstall">` element after the `<packages type="image">` section
    - Include `<package name="python3.11-pip"/>` in the uninstall section
    - Add a comment block explaining that KIWI processes the uninstall section after `config.sh` runs, so these packages are available during image configuration but absent from the final runtime image
    - _Requirements: 54.1, 54.2, 54.3_

  - [x] 186.3 Retain `binutils` in `<packages type="image">` with documented justification
    - Ensure `<package name="binutils"/>` is present in the `<packages type="image">` section in `kiwi-descriptions/appliance.kiwi`
    - Add a justification comment: required by `dracut --uefi` during the KIWI create step for UKI assembly (`objcopy`); cannot be removed because `pre_disk_sync.sh` runs before dracut/UKI generation (not after), and there is no KIWI hook that runs after UKI assembly but before the root tree is written to disk
    - Do NOT create a `pre_disk_sync.sh` script to remove `binutils` — the KIWI execution order is: `pre_disk_sync.sh` → sync → dracut UKI → final image, so removing `binutils` at any hook point breaks the build
    - _Requirements: 54.1, 54.2_

  - [x] 186.4 Update package minimization test to reflect new package policy
    - In the existing test that verifies removed packages are NOT in `appliance.kiwi` (from task 184.13), update assertions:
      - `python3.11-pip`: verify it is present in `<packages type="image">` AND present in `<packages type="uninstall">` (installed for build-time use, removed from final image)
      - `binutils`: verify it is present in `<packages type="image">` with a comment documenting its justification (required by dracut --uefi for UKI assembly); it must NOT be in `<packages type="uninstall">` or removed by any script
      - `awscli` and `pciutils`: verify they remain completely absent (not in any packages section)
    - _Requirements: 54.1, 54.2, 54.9_

- [x] 187. Checkpoint - Ensure all tests pass after build-time package changes
  - Run the full test suite and verify the updated package minimization tests pass
  - Verify no regressions in existing tests

- [ ] 188. Fix KIWI image Python dependency installation to use uv.lock via hash-verified path

  - [ ] 188.1 Replace pip3 download with uv export-based wheel acquisition in build-kiwi-image.sh
    - Remove the `pip3 download` block that reads dependency names from `pyproject.toml` version ranges and downloads wheels using `--only-binary` / platform flags
    - Remove the `python3 -c "import tomllib..."` block that extracts dependency names from `pyproject.toml`
    - Instead, use the already-exported `requirements.txt` (from `uv export --frozen --format requirements-txt --no-dev`) as the single source of truth for downloading wheels
    - Split the exported `requirements.txt` into two files programmatically:
      - `requirements-binary.txt`: all lines except wolfcrypt (packages with pre-built wheels)
      - `requirements-wolfcrypt.txt`: only the wolfcrypt line (source-only distribution)
    - Download binary wheels: `pip3 download --require-hashes --only-binary=:all: --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform linux_x86_64 --platform any --python-version 3.11 --implementation cp --abi cp311 -r requirements-binary.txt -d wheels/`
    - Download wolfcrypt sdist with hash verification: `pip3 download --require-hashes --no-binary=:all: -r requirements-wolfcrypt.txt -d wolfcrypt-src/` — this verifies the source tarball against the hash from `uv.lock`
    - Build wolfcrypt wheel inside the builder container from the hash-verified sdist (same as current approach but now the input is integrity-checked)
    - After building the wolfcrypt wheel, compute its SHA-256 hash and append a `wolfcrypt==X.Y.Z --hash=sha256:<wheel-hash>` line to a final `requirements-install.txt` that combines the binary requirements (with their original hashes) and the wolfcrypt wheel hash
    - Copy `requirements-install.txt` into the image overlay at `/tmp/kiwi-build/requirements.txt` for use by `config.sh`
    - _Requirements: 12.19, 12.20, 12.21, 12.22_

  - [ ] 188.2 Update config.sh to install with --require-hashes using the exported requirements.txt
    - Replace the current `pip3.11 install --no-index --find-links /tmp/kiwi-build/wheels /tmp/kiwi-build/wheels/*.whl` with `pip3.11 install --no-index --find-links /tmp/kiwi-build/wheels --require-hashes -r /tmp/kiwi-build/requirements.txt`
    - The `requirements.txt` at this point contains hashes for all packages: original sdist/wheel hashes from `uv.lock` for binary deps, plus the computed wheel hash for wolfcrypt (appended by build-kiwi-image.sh after building the wolfcrypt wheel)
    - This ensures that even the offline installation step verifies every wheel's integrity against known hashes
    - Keep the existing post-install verification checks (import fastapi, uvicorn, etc.)
    - _Requirements: 12.19, 12.22, 12.23_

  - [ ] 188.3 Add regression tests for lockfile-enforced dependency installation
    - Verify that `build-kiwi-image.sh` does NOT contain `pip3 download` commands that read version ranges from `pyproject.toml` (i.e., no `pip3 download ... ${BINARY_DEPS}` or similar variable expansion from pyproject.toml parsing)
    - Verify that `build-kiwi-image.sh` uses the exported `requirements.txt` (from `uv export --frozen`) for wheel downloads
    - Verify that `config.sh` uses `--require-hashes` with the requirements file during installation
    - Verify that `config.sh` does NOT use bare `*.whl` glob patterns without hash verification
    - _Requirements: 12.24_

- [ ] 189. Checkpoint - Ensure lockfile-enforced dependency tests pass
  - Run the full test suite and verify all new tests from 188.3 pass
  - Verify no regressions in existing tests

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["184.1", "184.3", "184.4", "184.5", "184.6", "184.7", "184.8", "184.9", "184.10", "184.11", "184.12"] },
    { "id": 1, "tasks": ["184.2"] },
    { "id": 2, "tasks": ["184.13"] },
    { "id": 3, "tasks": ["185"] },
    { "id": 4, "tasks": ["186.1", "186.2", "186.3"] },
    { "id": 5, "tasks": ["186.4"] },
    { "id": 6, "tasks": ["187"] },
    { "id": 7, "tasks": ["188.1", "188.2"] },
    { "id": 8, "tasks": ["188.3"] },
    { "id": 9, "tasks": ["189"] }
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
- The package minimization (184.11) removes awscli, python3.11-pip, pciutils from the runtime image to reduce post-compromise tooling
- The log sanitization (184.12) prevents GitHub tokens, credentialed URLs, absolute paths, and raw subprocess stderr from appearing in logs or error responses
- The digest pinning (184.1, 184.2) ensures the AMI build verifies and pulls the exact same immutable artifact, preventing TOCTOU attacks via tag movement
- The build-time package uninstall (186) re-adds `python3.11-pip` to `<packages type="image">` (needed by `config.sh` for `pip3.11 install`) while also listing it in `<packages type="uninstall">` so KIWI removes it after `config.sh` completes; `binutils` is retained in `<packages type="image">` permanently because `dracut --uefi` needs `objcopy` for UKI assembly during the KIWI create step, and there is no hook that runs after UKI generation but before the root tree is written to disk (`pre_disk_sync.sh` runs before dracut, not after)
- The lockfile-enforced dependency fix (188) addresses a gap where `build-kiwi-image.sh` exports a hashed `requirements.txt` from `uv.lock` but then ignores it, instead reading version ranges from `pyproject.toml` via `pip3 download`. The fix splits the exported requirements into binary deps (downloaded with `pip3 download --require-hashes --only-binary=:all: --platform ...`) and wolfcrypt (downloaded as a hash-verified sdist with `pip3 download --require-hashes --no-binary=:all:`, then built into a wheel inside the builder container). After building the wolfcrypt wheel, its SHA-256 is computed and appended to a final `requirements-install.txt` so that `config.sh` can install everything with `--require-hashes`. This creates an unbroken integrity chain: uv.lock → hash-verified download → hash-verified install.
