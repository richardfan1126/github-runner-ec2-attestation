# Implementation Plan: GitHub Actions Remote Executor Caller

## Overview

Implement the client-side caller for the Remote Executor system: a Python script (`RemoteExecutorCaller` class), a GitHub Actions workflow, and a sample build script. The implementation follows the sequence: project setup → core class with error handling → attestation validation → HTTP methods (health check, execute, polling) → output attestation → orchestration and reporting → workflow YAML → sample script → tests.

## Tasks

- [x] 1. Set up project structure and dependencies
  - [x] 1.1 Create `.github/scripts/pyproject.toml` with `requests` and `cbor2` dependencies
    - Define project metadata and `requires-python >= 3.11`
    - Add `requests>=2.31.0` and `cbor2>=5.6.0` to dependencies
    - Add `hypothesis>=6.0.0` and `pytest>=7.0.0` to optional dev dependencies
    - _Requirements: 3.1, 4.2, 6.2_

  - [x] 1.2 Create `.github/scripts/call_remote_executor.py` with `CallerError` exception and `RemoteExecutorCaller` class skeleton
    - Define `CallerError(Exception)` with `message`, `phase`, and `details` attributes
    - Define `RemoteExecutorCaller.__init__` accepting `server_url`, `timeout`, `poll_interval`, `max_poll_duration`, `max_retries` with defaults from the design
    - Add imports for `requests`, `cbor2`, `base64`, `hashlib`, `json`, `logging`, `time`, `sys`, `argparse`
    - Define `EXPECTED_ATTESTATION_FIELDS` constant list
    - _Requirements: 3.7, 5.2, 5.5, 5.7, 8.5_

- [x] 2. Implement attestation validation methods
  - [x] 2.1 Implement `validate_attestation` method
    - Base64-decode the attestation string to binary
    - CBOR-decode the binary to a Python dict using `cbor2`
    - Verify all `EXPECTED_ATTESTATION_FIELDS` are present as keys
    - Log attestation document fields for audit
    - Raise `CallerError(phase="attestation")` on base64 decode failure, CBOR parse failure, or missing fields
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [x] 2.2 Write property test for attestation decode round-trip
    - **Property 1: Attestation decode round-trip**
    - **Validates: Requirements 4.1, 4.2, 6.1, 6.2**

  - [x] 2.3 Write property test for attestation structural field validation
    - **Property 2: Attestation structural field validation**
    - **Validates: Requirements 4.6**

  - [x] 2.4 Write unit tests for attestation validation edge cases
    - Test invalid base64 raises `CallerError` with phase "attestation" (Req 4.3)
    - Test invalid CBOR raises `CallerError` with phase "attestation" (Req 4.4)
    - _Requirements: 4.3, 4.4_

- [x] 3. Implement health check and execute methods
  - [x] 3.1 Implement `health_check` method
    - Send HTTP GET to `{server_url}/health` with configurable timeout
    - On HTTP 200 with `status == "healthy"`, return parsed JSON
    - On non-200 or `status != "healthy"`, raise `CallerError(phase="health_check")`
    - On connection error, raise `CallerError(phase="health_check")` with connection error message
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 3.2 Implement `execute` method
    - Send HTTP POST to `{server_url}/execute` with JSON body containing `repository_url`, `commit_hash`, `script_path`, `github_token`
    - On HTTP 200, extract and return `execution_id` and `attestation_document` from response
    - On HTTP error status, raise `CallerError(phase="execute")` with status code and error details
    - On connection error, raise `CallerError(phase="execute")` with connection error message
    - Use configurable timeout for the request
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [x] 3.3 Write property test for health check acceptance
    - **Property 4: Health check acceptance**
    - **Validates: Requirements 8.2, 8.3**

  - [x] 3.4 Write property test for execute HTTP error propagation
    - **Property 5: Execute HTTP error propagation**
    - **Validates: Requirements 3.5**

  - [x] 3.5 Write unit tests for health check and execute edge cases
    - Test connection refused raises `CallerError` with phase "health_check" (Req 8.4)
    - Test connection refused raises `CallerError` with phase "execute" (Req 3.6)
    - _Requirements: 8.4, 3.6_

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Upgrade dependencies, class skeleton, and attestation validation for COSE Sign1 / PKI / PCR changes
  - [x] 5.1 Update `.github/scripts/pyproject.toml` with new cryptographic dependencies
    - Add `pycose>=1.0.0`, `pyOpenSSL>=23.0.0`, `pycryptodome>=3.19.0`, `cryptography>=41.0.0` to dependencies
    - _Requirements: 4A.2, 4B.8, 4C.13, 4C.15_

  - [x] 5.2 Update `RemoteExecutorCaller.__init__` to accept `root_cert_pem` and `expected_pcrs` parameters
    - Add `root_cert_pem: str = ""` parameter (PEM string, always provided by workflow)
    - Add `expected_pcrs: dict[int, str] | None = None` parameter (PCR4/PCR7 map, always provided by workflow)
    - Store both as instance attributes
    - Add imports for `pycose`, `OpenSSL.crypto`, `Crypto.Util.number.long_to_bytes`
    - _Requirements: 1.6, 1.7, 4B.8, 4D.17_

  - [x] 5.3 Rewrite `validate_attestation` for COSE Sign1 format with full cryptographic verification
    - Parse decoded CBOR as a 4-element COSE Sign1 array `[protected_header, unprotected_header, payload, signature]` instead of a flat dict
    - CBOR-decode the payload (index 2) to extract attestation fields
    - Validate structural fields on the decoded payload dict
    - Call `_verify_certificate_chain` to validate signing cert against root CA and cabundle
    - Call `_verify_cose_signature` to verify COSE Sign1 signature using cert's EC2 public key (P-384/ES384)
    - Call `_validate_pcrs` to validate PCR4 and PCR7 values
    - Raise `CallerError(phase="attestation")` if CBOR result is not a 4-element array
    - Raise `CallerError(phase="attestation")` if payload CBOR decoding fails
    - _Requirements: 4A.1–4A.7, 4B.8–4B.12, 4C.13–4C.16, 4D.17–4D.19, 4E.20_

  - [x] 5.4 Implement `_verify_certificate_chain` private method
    - Create `OpenSSL.crypto.X509Store` with `root_cert_pem` (PEM) and intermediate certs from `cabundle[1:]` (DER)
    - Load signing certificate from payload's `certificate` field (DER)
    - Verify via `X509StoreContext.verify_certificate()`
    - Raise `CallerError(phase="attestation")` on failure
    - _Requirements: 4B.8, 4B.9, 4B.10, 4B.11, 4B.12_

  - [x] 5.5 Implement `_verify_cose_signature` private method
    - Extract EC2 public key (x, y on P-384) from signing certificate using `long_to_bytes`
    - Construct `pycose.EC2` key with `alg=ES384`, `crv=P_384`
    - Build `pycose.Sign1Message` from protected header, unprotected header, payload, and signature
    - Call `msg.verify_signature(key)`, raise `CallerError(phase="attestation")` if False
    - _Requirements: 4C.13, 4C.14, 4C.15, 4C.16_

  - [x] 5.6 Implement `_validate_pcrs` private method
    - For each `(index, expected_hex)` in `expected_pcrs`, verify index exists in document PCRs and hex value matches
    - Raise `CallerError(phase="attestation")` on missing index or mismatch
    - _Requirements: 4D.17, 4D.18, 4D.19_

  - [x] 5.7 Update property tests for COSE Sign1 attestation format
    - Update Property 1 (decode round-trip) to wrap payloads in COSE Sign1 structure signed with test P-384 key
    - Update Property 2 (structural field validation) to use COSE Sign1 wrapping
    - Add Property 10 (COSE signature rejects tampered payloads)
    - Add Property 11 (PCR validation accepts matching, rejects mismatching)
    - Add Property 12 (certificate chain validation rejects untrusted certs)
    - _Requirements: 4A.1–4A.7, 4B.8–4B.12, 4C.15–4C.16, 4D.17–4D.19_

  - [x] 5.8 Update unit tests for COSE Sign1 attestation edge cases
    - Update existing invalid base64 and invalid CBOR tests for COSE Sign1 format
    - Add test: CBOR result not a 4-element array raises `CallerError` with COSE structure error (Req 4A.5)
    - Add test: payload CBOR decode failure raises `CallerError` (Req 4A.6)
    - Add test: certificate chain validation failure raises `CallerError` with PKI details (Req 4B.12)
    - Add test: COSE signature verification failure raises `CallerError` (Req 4C.16)
    - Add test: PCR index missing from attestation raises `CallerError` (Req 4D.18)
    - Add test: PCR value mismatch raises `CallerError` (Req 4D.19)
    - _Requirements: 4A.4, 4A.5, 4A.6, 4B.12, 4C.16, 4D.18, 4D.19_

- [x] 6. Checkpoint - Ensure all attestation tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement polling and output attestation
  - [x] 7.1 Implement `poll_output` method
    - Send HTTP GET to `{server_url}/execution/{execution_id}/output` in a loop
    - While `complete` is false, sleep for `poll_interval` seconds and retry
    - When `complete` is true, extract and return `stdout`, `stderr`, `exit_code`, `output_attestation_document`
    - Enforce `max_poll_duration` timeout, raise `CallerError(phase="polling")` if exceeded
    - On HTTP error, retry up to `max_retries` consecutive times before raising `CallerError(phase="polling")`
    - Log incremental output during polling for real-time feedback
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_

  - [x] 7.2 Implement `validate_output_attestation` method
    - Decode base64 → CBOR → COSE Sign1 4-element array (same parsing as `validate_attestation`)
    - CBOR-decode payload to extract attestation fields
    - Validate certificate chain (PKI) against root cert
    - Verify COSE Sign1 signature using signing certificate's EC2 public key
    - Validate PCR4 and PCR7 values
    - Extract `user_data` from verified payload (SHA-256 hex digest)
    - Reconstruct canonical output: `stdout:{stdout}\nstderr:{stderr}\nexit_code:{exit_code}`
    - Compute SHA-256 hex digest of canonical output
    - Compare computed digest against `user_data` digest
    - Return True if match, raise `CallerError(phase="output_attestation")` if mismatch
    - Raise `CallerError(phase="output_attestation")` on base64/CBOR/COSE/PKI/signature failures
    - _Requirements: 6A.1–6A.7, 6B.8–6B.12, 6C.13, 6C.14_

  - [x] 7.3 Write property test for output integrity verification
    - **Property 3: Output integrity verification**
    - Build COSE Sign1 attestation with user_data digest signed with test key
    - **Validates: Requirements 6B.8, 6B.9, 6B.10, 6B.12**

  - [x] 7.4 Write property test for polling termination on completion
    - **Property 6: Polling termination on completion**
    - **Validates: Requirements 5.3, 5.4**

  - [x] 7.5 Write property test for polling retry on transient errors
    - **Property 7: Polling retry on transient errors**
    - **Validates: Requirements 5.7**

  - [x] 7.6 Write unit tests for polling and output attestation edge cases
    - Test null `output_attestation_document` logs warning and continues (Req 6C.13)
    - Test poll timeout raises `CallerError` after configured duration (Req 5.5, 5.6)
    - Test default poll interval is 5 seconds (Req 5.2)
    - Test default max poll duration is 600 seconds (Req 5.5)
    - _Requirements: 6C.13, 5.2, 5.5, 5.6_

- [x] 8. Implement orchestration, reporting, and CLI entry point
  - [x] 8.1 Implement `run` method and summary generation
    - Orchestrate full flow: `health_check` → `execute` → `validate_attestation` → `poll_output` → `validate_output_attestation` → report results
    - Handle `CallerError` exceptions: print formatted error with phase and details, exit with code 1
    - Handle null/missing `output_attestation_document`: log warning, set verification status to "skipped"
    - Log stdout, stderr, exit code, attestation validation result, and output integrity result
    - Generate GitHub Actions job summary string containing all execution results and verification status
    - Return remote script exit code
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [x] 8.2 Implement `__main__` CLI entry point with `argparse`
    - Parse `--server-url` (required), `--script-path`, `--commit-hash`, `--github-token` arguments
    - Parse `--root-cert-pem` (required, PEM string passed from workflow) and `--expected-pcrs` (required, JSON string passed from workflow) arguments
    - Support environment variable overrides for timeout configuration (`CALLER_HTTP_TIMEOUT`, `CALLER_POLL_INTERVAL`, `CALLER_MAX_POLL_DURATION`, `CALLER_MAX_RETRIES`)
    - Pass `root_cert_pem` and `expected_pcrs` to `RemoteExecutorCaller.__init__`
    - Write job summary to `$GITHUB_STEP_SUMMARY` file if the environment variable is set
    - Call `sys.exit()` with the return value of `run()`
    - _Requirements: 1.5, 1.6, 1.7, 3.1, 3.2, 3.3, 7.7_

  - [x] 8.3 Write property test for exit code propagation
    - **Property 8: Exit code propagation**
    - **Validates: Requirements 7.6**

  - [x] 8.4 Write property test for summary contains execution results
    - **Property 9: Summary contains execution results**
    - **Validates: Requirements 7.7**

- [x] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Create GitHub Actions workflow and sample build script
  - [x] 10.1 Create `.github/workflows/call-remote-executor.yml`
    - Define `workflow_dispatch` trigger with inputs: `server_url` (required), `script_path` (optional, default `.github/scripts/sample-build.sh`), `commit_hash` (optional, default `${{ github.sha }}`)
    - Hardcode the NitroTPM attestation root CA certificate PEM inline as a multi-line environment variable or step output
    - Hardcode the expected PCR4 and PCR7 values as a JSON map `{"4": "<hex>", "7": "<hex>"}` inline in the workflow
    - Validate `server_url` is not empty, fail with clear error if it is
    - Check out the repository
    - Set up Python and install dependencies from `.github/scripts/pyproject.toml`
    - Invoke `call_remote_executor.py` with `--server-url`, `--script-path`, `--commit-hash`, `--github-token` from `${{ secrets.GITHUB_TOKEN }}`, `--root-cert-pem` from hardcoded env, and `--expected-pcrs` from hardcoded env
    - Write `$GITHUB_STEP_SUMMARY` from the caller script output
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 7.7_

  - [x] 10.2 Create `.github/scripts/sample-build.sh`
    - Create shell script with `#!/usr/bin/env bash` and `set -euo pipefail`
    - Output hostname, date, kernel version, user, and working directory
    - Exit with code 0
    - Ensure the file is executable
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 10.3 Write unit tests for workflow and sample script
    - Test sample build script file exists and is executable (Req 2.1)
    - Test sample build script contains system info commands (Req 2.4)
    - Test empty `server_url` raises error (Req 1.5)
    - _Requirements: 1.5, 2.1, 2.4_

- [x] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Add OIDC support to RemoteExecutorCaller
  - [x] 12.1 Update `RemoteExecutorCaller.__init__` to accept `audience` parameter
    - Add `audience: str = ""` parameter to `__init__`
    - Store as `self.audience` instance attribute
    - Initialize `self._oidc_token: str | None = None` for storing the acquired token
    - _Requirements: 9.2, 9.4_

  - [x] 12.2 Implement `request_oidc_token` method
    - Read `ACTIONS_ID_TOKEN_REQUEST_URL` and `ACTIONS_ID_TOKEN_REQUEST_TOKEN` from environment variables
    - If either is missing, raise `CallerError(phase="oidc")` with message indicating `id-token: write` permission is required
    - Make HTTP GET to `{ACTIONS_ID_TOKEN_REQUEST_URL}?audience={self.audience}` with header `Authorization: Bearer {ACTIONS_ID_TOKEN_REQUEST_TOKEN}`
    - Extract JWT token from response JSON `value` field
    - Store token on `self._oidc_token`
    - Return the token string
    - Raise `CallerError(phase="oidc")` on HTTP errors or connection failures
    - _Requirements: 9.3, 9.4, 9.5, 9.6, 9.7_

  - [x] 12.3 Update `execute` method to include Authorization header
    - Add `Authorization: Bearer {self._oidc_token}` header to the POST /execute request
    - Handle HTTP 401 response: raise `CallerError(phase="execute")` with authentication failure message
    - Handle HTTP 403 response: raise `CallerError(phase="execute")` with repository not authorized message
    - _Requirements: 10.1, 10.4, 10.5_

  - [x] 12.4 Update `poll_output` method to include Authorization header
    - Add `Authorization: Bearer {self._oidc_token}` header to GET /execution/{id}/output requests
    - Handle HTTP 401 response: raise `CallerError(phase="polling")` with authentication failure message (no retry)
    - Handle HTTP 403 response: raise `CallerError(phase="polling")` with repository not authorized message (no retry)
    - _Requirements: 10.2, 10.4, 10.5_

  - [x] 12.5 Ensure `health_check` does NOT include Authorization header
    - Verify that the GET /health request does not include an Authorization header regardless of whether `_oidc_token` is set
    - _Requirements: 10.3_

  - [x] 12.6 Update `run` method to call `request_oidc_token` after `health_check`
    - Insert `request_oidc_token()` call between `health_check()` and `execute()` in the orchestration flow
    - Flow becomes: health_check → request_oidc_token → execute → validate_attestation → poll_output → validate_output_attestation
    - _Requirements: 9.3, 9.7_

  - [x] 12.7 Update `__main__` CLI entry point for OIDC
    - Add `--audience` argument to argparse (optional, default empty string)
    - Pass `audience` to `RemoteExecutorCaller.__init__`
    - _Requirements: 9.2_

- [x] 13. Checkpoint - Ensure OIDC implementation compiles and existing tests are updated
  - Update existing tests that construct `RemoteExecutorCaller` to include `audience` parameter where needed
  - Ensure all existing tests pass with the updated signatures
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Write property tests for OIDC
  - [x] 14.1 Write property test for OIDC token acquisition
    - **Property 13: OIDC token acquisition**
    - Generate random audience strings, mock OIDC provider endpoint
    - Verify `request_oidc_token` makes HTTP GET with correct audience query param and Bearer header
    - Verify returned token is stored on the instance
    - **Validates: Requirements 9.3, 9.4, 9.7**

  - [x] 14.2 Write property test for OIDC token transmission
    - **Property 14: OIDC token transmission**
    - Generate random OIDC tokens, set on caller instance, mock HTTP endpoints
    - Verify `execute` and `poll_output` include `Authorization: Bearer <token>` header
    - Verify `health_check` does NOT include Authorization header
    - **Validates: Requirements 10.1, 10.2, 10.3**

  - [x] 14.3 Write property test for OIDC authentication error handling
    - **Property 15: OIDC authentication error handling**
    - Generate random 401/403 responses for `/execute` and `/execution/{id}/output`
    - Verify `CallerError` raised with appropriate auth error messages
    - Test missing env vars cause `CallerError` with `id-token: write` permission message
    - **Validates: Requirements 9.5, 9.6, 10.4, 10.5**

- [x] 15. Write unit tests for OIDC
  - [x] 15.1 Write unit tests for OIDC token acquisition errors
    - Test missing `ACTIONS_ID_TOKEN_REQUEST_URL` raises `CallerError` with phase "oidc" (Req 9.5)
    - Test missing `ACTIONS_ID_TOKEN_REQUEST_TOKEN` raises `CallerError` with phase "oidc" (Req 9.5)
    - Test OIDC provider HTTP error raises `CallerError` with phase "oidc" (Req 9.6)
    - _Requirements: 9.5, 9.6_

  - [x] 15.2 Write unit tests for OIDC-authenticated endpoint error handling
    - Test execute with HTTP 401 raises `CallerError` with authentication failure message (Req 10.4)
    - Test execute with HTTP 403 raises `CallerError` with repository not authorized message (Req 10.5)
    - Test poll output with HTTP 401 raises `CallerError` with authentication failure message (Req 10.4)
    - Test poll output with HTTP 403 raises `CallerError` with repository not authorized message (Req 10.5)
    - _Requirements: 10.4, 10.5_

  - [x] 15.3 Write unit test for health check Authorization header exclusion
    - Test health check does not include Authorization header even when `_oidc_token` is set (Req 10.3)
    - _Requirements: 10.3_

- [x] 16. Update existing tests for OIDC compatibility
  - [x] 16.1 Update `tests/test_caller_unit.py` for OIDC
    - Add `audience` parameter to all `RemoteExecutorCaller` constructor calls
    - Set `_oidc_token` on caller instances where execute/poll_output tests need it
    - Ensure existing unit tests pass with OIDC-aware signatures
    - _Requirements: 9.2, 10.1, 10.2_

  - [x] 16.2 Update `tests/test_caller_properties.py` for OIDC
    - Add `audience` parameter to all `RemoteExecutorCaller` constructor calls in property tests
    - Set `_oidc_token` on caller instances where execute/poll_output property tests need it
    - Ensure existing property tests pass with OIDC-aware signatures
    - _Requirements: 9.2, 10.1, 10.2_

- [x] 17. Update GitHub Actions workflow for OIDC
  - [x] 17.1 Add `id-token: write` permission to workflow
    - Add `id-token: write` to the `permissions` block in `.github/workflows/call-remote-executor.yml`
    - _Requirements: 9.1_

  - [x] 17.2 Add `audience` input to workflow dispatch
    - Add optional `audience` input to `workflow_dispatch` inputs
    - _Requirements: 9.2_

  - [x] 17.3 Pass `--audience` to caller script invocation
    - Add `--audience ${{ inputs.audience }}` to the caller script invocation step
    - _Requirements: 9.2_

- [ ] 18. Update CLI entry point
  - [ ] 18.1 Add `--audience` argument to argparse
    - Add `--audience` optional argument with default empty string
    - Pass `audience` value to `RemoteExecutorCaller` constructor
    - _Requirements: 9.2_

  - [ ] 18.2 Write unit tests for workflow OIDC configuration
    - Test workflow YAML contains `id-token: write` permission (Req 9.1)
    - Test workflow YAML contains `audience` input (Req 9.2)
    - _Requirements: 9.1, 9.2_

- [ ] 19. Final checkpoint - Ensure all OIDC tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- All test files go in `tests/test_caller_properties.py` and `tests/test_caller_unit.py`
- The caller's `pyproject.toml` at `.github/scripts/pyproject.toml` is separate from the existing `scripts/pyproject.toml`
- Tasks 1-11 cover the original caller implementation (all completed)
- Tasks 12-19 cover OIDC authentication support (Requirements 9, 10; Properties 13-15)
