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

- [x] 18. Update CLI entry point
  - [x] 18.1 Add `--audience` argument to argparse
    - Add `--audience` optional argument with default empty string
    - Pass `audience` value to `RemoteExecutorCaller` constructor
    - _Requirements: 9.2_

  - [x] 18.2 Write unit tests for workflow OIDC configuration
    - Test workflow YAML contains `id-token: write` permission (Req 9.1)
    - Test workflow YAML contains `audience` input (Req 9.2)
    - _Requirements: 9.1, 9.2_

- [x] 19. Final checkpoint - Ensure all OIDC tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 20. Implement ClientEncryption class
  - [x] 20.1 Create `ClientEncryption` class in `call_remote_executor.py`
    - Add imports for `X25519PrivateKey`, `X25519PublicKey`, `HKDF`, `SHA256`, `AESGCM`, `Encoding`, `PublicFormat` from `cryptography`
    - Implement `__init__` to generate a fresh X25519 keypair via `X25519PrivateKey.generate()`
    - Implement `client_public_key_bytes` property returning raw 32-byte public key via `public_bytes(Encoding.Raw, PublicFormat.Raw)`
    - Implement `derive_shared_key(server_public_key_bytes)` performing ECDH + HKDF-SHA256 with `salt=None`, `info=b"hpke-shared-key"`, `length=32`; raise `CallerError(phase="encryption")` if server key is not valid 32-byte X25519
    - Implement `encrypt_payload(payload_dict)` serializing dict to JSON, encrypting with AES-256-GCM using 12-byte random nonce, returning base64-encoded `nonce || ciphertext`; raise `CallerError(phase="encryption")` if shared key not derived
    - Implement `decrypt_response(encrypted_response_b64)` base64-decoding, splitting 12-byte nonce + ciphertext, decrypting with AES-256-GCM, deserializing JSON; raise `CallerError(phase="encryption")` on decryption failure or invalid JSON
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 13.1, 13.2, 13.3, 13.4, 13.5, 14.1, 14.2, 14.3, 14.5, 15.3, 15.4, 15.5, 15.6, 15.7_

  - [x] 20.2 Write property test for AES-256-GCM encryption round-trip
    - **Property 16: AES-256-GCM encryption round-trip**
    - Generate random JSON-serializable dicts and random 32-byte AES keys
    - Encrypt via `encrypt_payload`, decrypt via `decrypt_response` with same shared key
    - Verify result equals original dict
    - **Validates: Requirements 3.2, 14.1, 15.3, 15.4, 15.5**

  - [x] 20.3 Write property test for HPKE key derivation symmetry
    - **Property 17: HPKE key derivation symmetry**
    - Generate random X25519 keypairs for client and server
    - Derive shared key on both sides using ECDH + HKDF-SHA256 with same parameters
    - Verify both sides produce identical 32-byte keys
    - **Validates: Requirements 13.1, 13.2**

  - [x] 20.4 Write property test for AES-256-GCM decryption rejects tampered ciphertext
    - **Property 20: AES-256-GCM decryption rejects tampered ciphertext**
    - Generate random dicts, encrypt via `encrypt_payload`
    - Modify a random byte in the base64-decoded wire format
    - Verify `decrypt_response` raises `CallerError`
    - **Validates: Requirements 15.6**

  - [x] 20.5 Write unit tests for ClientEncryption edge cases
    - Test invalid server public key (not 32 bytes) raises `CallerError` with phase "encryption" (Req 13.5)
    - Test `encrypt_payload` before `derive_shared_key` raises `CallerError` (Req 14.1)
    - Test decryption failure on tampered response raises `CallerError` with phase "encryption" (Req 15.6)
    - Test decrypted response that is not valid JSON raises `CallerError` (Req 15.7)
    - _Requirements: 13.5, 14.1, 15.6, 15.7_

- [x] 21. Checkpoint - Ensure ClientEncryption tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 22. Implement `generate_nonce` and `_verify_nonce` methods
  - [x] 22.1 Implement `generate_nonce` static method on `RemoteExecutorCaller`
    - Generate 32 random bytes and return as 64-char hex string
    - Each call must produce a unique value
    - _Requirements: 3.12, 5.13, 11.11, 11.12_

  - [x] 22.2 Implement `_verify_nonce` private method on `RemoteExecutorCaller`
    - Accept `payload_doc` dict, `expected_nonce` string, and `phase` string
    - Extract `nonce` field from payload, decode from bytes if necessary
    - Compare against `expected_nonce`; raise `CallerError` if missing or mismatched
    - _Requirements: 3.13, 5.14, 11.12_

  - [x] 22.3 Update `validate_attestation` to accept optional `expected_nonce` parameter
    - Add `expected_nonce: str | None = None` parameter
    - After PCR validation, if `expected_nonce` is provided, call `_verify_nonce`
    - _Requirements: 3.13, 11.12_

  - [x] 22.4 Update `validate_output_attestation` to accept optional `expected_nonce` parameter
    - Add `expected_nonce: str | None = None` parameter
    - After PCR validation, if `expected_nonce` is provided, call `_verify_nonce`
    - _Requirements: 5.14_

  - [x] 22.5 Write property test for nonce freshness verification
    - **Property 18: Nonce freshness verification**
    - Generate random nonce strings, build attestation documents with matching and non-matching nonces
    - Verify `validate_attestation` with `expected_nonce` accepts when nonces match
    - Verify raises `CallerError` when nonces differ or nonce field is missing
    - **Validates: Requirements 3.11, 3.12, 3.13, 5.13, 5.14, 11.3, 11.11, 11.12**

  - [x] 22.6 Write unit tests for nonce verification edge cases
    - Test matching nonce passes validation
    - Test mismatched nonce raises `CallerError`
    - Test missing nonce field raises `CallerError`
    - Test nonce as bytes is decoded correctly
    - _Requirements: 3.13, 5.14, 11.12_

- [x] 23. Implement `attest` method for server attestation and HPKE key exchange
  - [x] 23.1 Implement `attest` method on `RemoteExecutorCaller`
    - Generate a unique random nonce via `generate_nonce()`
    - Send HTTP GET to `{server_url}/attest?nonce={nonce}` with no auth headers and no request body
    - On HTTP 200, extract `attestation_document` from JSON response
    - Call `validate_attestation(attestation_b64, expected_nonce=nonce)` to validate COSE Sign1 + PKI + PCR + nonce
    - Extract `public_key` field from validated attestation payload; raise `CallerError(phase="attest")` if null or missing
    - Initialize `self._encryption = ClientEncryption()` and call `derive_shared_key(server_public_key_bytes)`
    - Store the nonce for later reference
    - On HTTP error or connection error, raise `CallerError(phase="attest")`
    - Set configurable timeout for the request
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 11.10, 11.11, 11.12, 12.1, 12.2, 12.3, 12.4, 12.5, 13.1, 13.2, 13.3_

  - [x] 23.2 Write unit tests for attest method
    - Test successful attest extracts server public key and initializes encryption
    - Test missing `public_key` in attestation raises `CallerError` with phase "attest" (Req 11.7)
    - Test connection error raises `CallerError` with phase "attest" (Req 11.9)
    - Test HTTP error raises `CallerError` with phase "attest" (Req 11.8)
    - Test attest does not include Authorization header or auth credentials (Req 11.2)
    - Test nonce is included as query parameter (Req 11.3)
    - _Requirements: 11.2, 11.3, 11.7, 11.8, 11.9_

- [x] 24. Checkpoint - Ensure attest and nonce tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 25. Update `execute` method for HPKE encryption
  - [x] 25.1 Rewrite `execute` method to use encrypted communication
    - Generate a unique random nonce via `generate_nonce()`
    - Build plaintext payload: `{repository_url, commit_hash, script_path, github_token, oidc_token, nonce}`
    - Encrypt payload via `self._encryption.encrypt_payload()`
    - Send HTTP POST to `{server_url}/execute` with JSON body `{encrypted_payload: "base64", client_public_key: "base64"}` — no Authorization header
    - On HTTP 200, extract `encrypted_response` from JSON response and decrypt via `self._encryption.decrypt_response()`
    - Extract `execution_id` and `attestation_document` from decrypted response
    - Call `validate_attestation(attestation_b64, expected_nonce=nonce)` to verify nonce in returned attestation
    - Remove the `Authorization` header from the request (OIDC token is now in encrypted payload only)
    - Handle HTTP 401/403 errors as before
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 10.1, 10.3, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 15.1_

  - [x] 25.2 Write property test for encrypted envelope structure
    - **Property 19: Encrypted envelope structure**
    - Generate random payloads, call `execute` (mocked HTTP)
    - Verify request body is JSON with `encrypted_payload` and `client_public_key` fields (both base64)
    - Call `poll_output` (mocked HTTP) and verify request body has `encrypted_payload` only (no `client_public_key`)
    - **Validates: Requirements 3.1, 14.6, 14.7**

  - [x] 25.3 Write unit tests for encrypted execute
    - Test execute sends encrypted envelope with `encrypted_payload` and `client_public_key` fields
    - Test execute does not include Authorization header (Req 10.3)
    - Test execute includes OIDC token in encrypted payload (Req 10.1)
    - Test execute includes nonce in encrypted payload (Req 3.11)
    - Test execute verifies nonce in returned attestation (Req 3.13)
    - _Requirements: 3.1, 3.11, 3.13, 10.1, 10.3, 14.6_

- [x] 26. Update `poll_output` method for HPKE encryption
  - [x] 26.1 Rewrite `poll_output` to use encrypted POST requests
    - Change from HTTP GET to HTTP POST for each poll request
    - For each poll iteration: generate unique nonce, build plaintext `{oidc_token, nonce}`, encrypt via `self._encryption.encrypt_payload()`
    - Send JSON body `{encrypted_payload: "base64"}` — no `client_public_key`, no Authorization header
    - On HTTP 200, extract `encrypted_response` and decrypt via `self._encryption.decrypt_response()`
    - On final response (`complete=true`), store the last nonce for output attestation nonce verification
    - Handle HTTP 401/403 errors as before (no retry)
    - Handle transient HTTP errors with retry logic as before
    - _Requirements: 5.1, 5.2, 5.3, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12, 5.13, 5.14, 10.2, 10.3, 14.7, 15.2_

  - [x] 26.2 Write unit tests for encrypted poll_output
    - Test poll_output sends POST (not GET) with encrypted payload
    - Test poll_output does not include Authorization header (Req 10.3)
    - Test poll_output includes OIDC token in encrypted payload (Req 10.2)
    - Test poll_output includes unique nonce in each request (Req 5.13)
    - Test poll_output request body has `encrypted_payload` only, no `client_public_key` (Req 14.7)
    - Test poll_output decrypts response correctly
    - _Requirements: 5.1, 5.13, 10.2, 10.3, 14.7_

- [x] 27. Checkpoint - Ensure encrypted execute and poll_output tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 28. Update `run` method and orchestration flow
  - [ ] 28.1 Update `run` method to include attest step and pass nonces
    - Insert `attest()` call between `request_oidc_token()` and `execute()`
    - Flow becomes: health_check → request_oidc_token → attest → execute (encrypted) → validate_attestation (with nonce) → poll_output (encrypted) → validate_output_attestation (with nonce)
    - Pass the last poll nonce to `validate_output_attestation` for nonce verification
    - Remove standalone `validate_attestation` call after execute (now done inside `execute`)
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6_

  - [ ] 28.2 Write unit tests for updated run flow
    - Test run calls methods in correct order: health_check → request_oidc_token → attest → execute → poll_output → validate_output_attestation
    - Test attest failure prevents execute from being called (Req 16.6)
    - Test no unencrypted payloads sent to /execute or /output (Req 16.3)
    - _Requirements: 16.1, 16.3, 16.6_

- [ ] 29. Update existing property tests for HPKE and nonce compatibility
  - [ ] 29.1 Update Property 14 test for OIDC token in encrypted payload
    - Change from verifying Authorization header to verifying OIDC token in encrypted payload's `oidc_token` field
    - Verify NO HTTP request to any endpoint includes an Authorization header
    - Mock `ClientEncryption` to inspect encrypted payloads
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [ ] 29.2 Update Property 6 (polling termination) for encrypted POST
    - Change mock from GET responses to POST responses with encrypted payloads
    - Mock `ClientEncryption` encrypt/decrypt for poll requests
    - Verify exactly N+1 POST requests made
    - _Requirements: 5.6, 5.7_

  - [ ] 29.3 Update Property 7 (polling retry) for encrypted POST
    - Change mock from GET to POST with encrypted payloads
    - _Requirements: 5.10_

  - [ ] 29.4 Update Property 5 (execute HTTP error propagation) for encrypted POST
    - Update mock to handle encrypted envelope format
    - _Requirements: 3.8_

  - [ ] 29.5 Update Property 8 (exit code propagation) for full encrypted flow
    - Mock the full flow including attest, HPKE key exchange, encrypted execute/poll
    - _Requirements: 7.6_

  - [ ] 29.6 Update Property 1 (attestation decode round-trip) for nonce field
    - Include `nonce` and `public_key` fields in generated attestation payloads
    - Test with `expected_nonce` parameter
    - _Requirements: 4A.1, 4A.2, 4A.3, 11.5_

- [ ] 30. Update existing unit tests for HPKE and nonce compatibility
  - [ ] 30.1 Update `tests/test_caller_unit.py` for encrypted communication
    - Update execute tests to use encrypted envelope format and mock `ClientEncryption`
    - Update poll_output tests to use POST with encrypted payloads
    - Set `_encryption` attribute on caller instances where execute/poll_output tests need it
    - Remove Authorization header assertions from execute and poll_output tests
    - Add assertions that no Authorization header is sent on any request
    - _Requirements: 10.3, 14.6, 14.7_

  - [ ] 30.2 Update `tests/test_caller_properties.py` for encrypted communication
    - Update all property tests that construct `RemoteExecutorCaller` to initialize `_encryption`
    - Update execute and poll_output property tests to mock encrypted request/response
    - _Requirements: 14.6, 14.7_

- [ ] 31. Update GitHub Actions workflow for encrypted flow
  - [ ] 31.1 Verify workflow YAML is compatible with encrypted flow
    - Ensure the caller script invocation does not pass `--github-token` via Authorization header
    - Verify `--audience` is still passed for OIDC token (now used in encrypted payload)
    - No workflow YAML changes should be needed since encryption is handled inside the Python script
    - _Requirements: 16.1, 10.3_

- [ ] 32. Final checkpoint - Ensure all HPKE and nonce tests pass
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
- Tasks 20-32 cover HPKE encrypted communication, mandatory nonces, and related updates (Requirements 11-16; Properties 16-20)
