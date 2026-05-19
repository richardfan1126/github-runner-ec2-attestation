# Implementation Plan: GitHub Actions Remote Executor

## Overview

This implementation plan breaks down the GitHub Actions Remote Executor into discrete coding tasks. The system is an HTTP server running on an Attestable EC2 instance with NitroTPM that executes scripts from GitHub repositories with cryptographic attestation. The implementation follows an asynchronous execution model with polling-based output retrieval.

## Tasks

- [x] 1. Tasks 1–189 (completed): Project structure, configuration, data models, request validation, repository client, attestation generator, execution management, output collection, script executor, HTTP server, OIDC authentication, PQ Hybrid KEM encryption, anti-replay nonce cache, concurrency enforcement, contextvars logging, Docker container security, rootless Docker migration, KIWI image build infrastructure, GitHub Actions workflow, AMI converter, deployment, cleanup, debug SSH, security hardening round 1 (mandatory nonces, request body limits, encrypted error envelopes, execution_id binding, post-clone cleanup, strict boolean parsing, raw filename sanitization, debug gate fail-closed, lockfile-enforced deps, helper source integrity, UID pinning, LimitCORE=0, OutputCollector config passthrough), script environment variable forwarding, health endpoint hardening, container image digest pinning, rootless Docker dependencies built from source, security hardening round 2 (immutable artifact digest pinning, credential isolation via GIT_ASKPASS, strict nonce type/length/format validation, strict base64 decoding, CI action SHA pinning, Dockerfile base image digest pinning, libslirp checksum verification, script_env deny-list, symlink-safe script path validation, runtime image package minimization, log and error response sanitization), build-time package uninstall via KIWI (python3.11-pip in uninstall section, binutils retained for dracut UKI), lockfile-enforced dependency installation (uv export → pip3 download --require-hashes → config.sh --require-hashes), and comprehensive property/unit/integration tests for all of the above

- [ ] 190. Security hardening round 3: OIDC commit hash binding, immutable container image reference, production executor digest wiring, container PID limits, output attestation rate limiting, NitroTPM availability enforcement

  - [x] 190.1 Add OIDC commit hash binding to Request Validator
    - In `src/validation.py`, add a `validate_commit_hash_binding(self, oidc_claims: dict, commit_hash: str) -> bool` method that compares `commit_hash.lower()` against `oidc_claims["sha"].lower()`; returns False on mismatch
    - In `src/server.py`, after the repository binding check (which verifies `repository` claim matches `repository_url`), call `validate_commit_hash_binding()` with the OIDC claims and the request's `commit_hash`; if it returns False, return encrypted error envelope with error_code 403 and message indicating commit hash mismatch
    - The check must occur after OIDC validation succeeds and before repository cloning begins
    - _Requirements: 2.33, 2.34, 2.35, 2.36_

  - [x] 190.2 Normalize container image to immutable digest reference after startup verification
    - In `src/script_executor.py` `__init__`, after receiving `container_image_digest`, compute the Immutable_Image_Reference: strip any tag from `container_image` (everything after `:` but before `@`), then append `@sha256:<digest>` to produce `<repository>@sha256:<digest>`
    - Store the normalized reference as `self._immutable_image_ref`
    - In `_execute_in_container()`, use `self._immutable_image_ref` (not `self._container_image`) when calling `self._docker_client.containers.create(image=...)`
    - If `container_image_digest` is None but `container_image` already contains `@sha256:`, use it directly as the immutable reference
    - If `container_image_digest` is None and `container_image` is a mutable tag, log a warning and fall back to the tag (backward compatibility)
    - _Requirements: 34.13, 34.14, 34.15, 34.16_

  - [ ] 190.3 Pass container_image_digest to production ScriptExecutor in create_app()
    - In `src/server.py` `create_app()`, when constructing the request-handling `ScriptExecutor`, pass `container_image_digest=config.container_image_digest`
    - Verify both the startup executor (in `src/main.py`) and the request-handling executor (in `src/server.py`) receive the same `container_image_digest` value from config
    - _Requirements: 34.17_

  - [ ] 190.4 Add container PID limits
    - Add `container_pids_limit: int` to `ServerConfig` in `src/config.py`; read from `MAX_CONTAINER_PIDS` env var; default to 256; validate is a positive integer at startup (fail to start if non-positive or non-integer)
    - In `src/script_executor.py` `__init__`, accept `container_pids_limit: int = 256` parameter
    - In `_execute_in_container()`, pass `pids_limit=self._container_pids_limit` to `self._docker_client.containers.create()`
    - In `src/server.py` `create_app()`, pass `container_pids_limit=config.container_pids_limit` to the ScriptExecutor constructor
    - _Requirements: 8.27, 8.28, 8.29, 8.30_

  - [ ] 190.5 Add output attestation rate limiting
    - Create `src/output_attestation_rate_limiter.py` with an `OutputAttestationRateLimiter` class:
      - `__init__(self, max_per_window: int, window_seconds: int)` — stores config
      - `check_and_record(self, execution_id: str) -> bool` — returns True if within budget (attestation allowed), False if budget exhausted; thread-safe
      - Uses a dict mapping execution_id → list of generation timestamps within the current window
      - Prunes expired timestamps on each check
    - Add `max_output_attestations_per_window: int` (default 10) and `output_attestation_window_seconds: int` (default 60) to `ServerConfig`; read from `MAX_OUTPUT_ATTESTATIONS_PER_WINDOW` and `OUTPUT_ATTESTATION_WINDOW_SECONDS` env vars
    - In `src/server.py`, instantiate the rate limiter at app startup; in the output handler, before calling `generate_output_attestation()`, check the rate limiter; if budget exhausted, set `output_attestation_document` to null and add `attestation_rate_limited: true` to the response
    - _Requirements: 55.1, 55.2, 55.3, 55.4, 55.5, 55.6, 55.7, 55.8_

  - [ ] 190.6 Add NitroTPM availability enforcement at startup
    - Add `allow_no_tpm: bool` to `ServerConfig` in `src/config.py`; read from `ALLOW_NO_TPM` env var; default to false; use the same strict boolean parsing as other boolean config values
    - In `src/main.py`, after the existing NitroTPM availability check: if TPM is unavailable and `allow_no_tpm` is False, log an error and exit with non-zero code; if TPM is unavailable and `allow_no_tpm` is True, log a prominent warning and continue
    - _Requirements: 9.16, 9.17, 9.18, 9.19, 9.20_

  - [ ] 190.7 Write tests for all security hardening round 3 changes
    - **OIDC commit hash binding tests**: Verify request with matching `commit_hash` and OIDC `sha` claim is accepted; verify mismatch is rejected with 403; verify comparison is case-insensitive (uppercase hex in commit_hash matches lowercase in sha claim)
    - **Immutable image reference tests**: Verify when `container_image_digest` is configured, `containers.create()` receives `image@sha256:<digest>` reference; verify mutable tag is NOT passed when digest is available; verify warning is logged when no digest is configured
    - **Production executor wiring tests**: Verify `create_app()` passes `container_image_digest` to ScriptExecutor; verify both startup and request-handling executors receive the same digest value
    - **PID limit tests**: Verify `pids_limit` is passed to `containers.create()` with configured value; verify non-positive MAX_CONTAINER_PIDS fails startup; verify non-integer MAX_CONTAINER_PIDS fails startup
    - **Output attestation rate limiting tests**: Verify attestation is generated normally within rate limit; verify after exceeding rate limit, output is returned with `output_attestation_document: null` and `attestation_rate_limited: true`; verify budget resets after window expires
    - **NitroTPM enforcement tests**: Verify startup fails when TPM unavailable and ALLOW_NO_TPM is false; verify startup succeeds with warning when TPM unavailable and ALLOW_NO_TPM is true; verify startup succeeds normally when TPM is available regardless of ALLOW_NO_TPM
    - _Requirements: 2.37, 8.31, 9.21, 34.18, 55.9_

- [ ] 191. Checkpoint - Ensure all security hardening round 3 tests pass
  - Run the full test suite and verify all new tests from 190.7 pass
  - Verify no regressions in existing tests

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["190.1", "190.2", "190.4", "190.5", "190.6"] },
    { "id": 1, "tasks": ["190.3"] },
    { "id": 2, "tasks": ["190.7"] },
    { "id": 3, "tasks": ["191"] }
  ]
}
```

## Notes

- Tasks 1-189 cover the full implementation through two rounds of security hardening. See git history for the detailed subtask breakdowns of tasks 184-189.
- Tasks 190+ address findings 1, 3, 4, 13, 19, and 20 from the 2026-05-15 security review that were not covered by the first two hardening rounds
- The OIDC commit hash binding (190.1) ensures the executed code is the same code that minted the OIDC token, closing the gap where a valid workflow could request execution of a different commit
- The immutable image reference (190.2) prevents tag movement after startup from causing execution in an unintended container image
- The production executor wiring (190.3) depends on 190.2 because it ensures the digest-normalized reference is actually used on the production code path
- The PID limits (190.4) prevent fork bombs from exhausting the host process table despite existing CPU/memory limits
- The output attestation rate limiting (190.5) prevents frequent polling from turning TPM attestation into a resource-exhaustion path; when rate-limited, `output_attestation_document` is null (not a cached previous attestation)
- The NitroTPM enforcement (190.6) ensures the service fails closed in production when its core attestation guarantee cannot be produced, with an explicit `ALLOW_NO_TPM` escape hatch for dev/test
