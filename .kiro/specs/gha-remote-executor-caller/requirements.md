# Requirements Document

## Introduction

This document specifies the requirements for the GitHub Actions Remote Executor Caller — a GitHub Actions workflow and supporting scripts that act as the client side of the Remote Executor system. The caller workflow is triggered via `workflow_dispatch`, sends execution requests to an already-deployed Remote Executor server, validates the server's identity and response integrity through NitroTPM attestation documents, and reports results back in the GitHub Actions workflow output.

The caller includes:

1. **GitHub Actions Workflow**: A `workflow_dispatch`-triggered workflow that orchestrates the entire call-validate-poll-verify cycle against the Remote Executor server.
2. **Sample Build Script**: A sample script included in the repository that the Remote Executor server will fetch and execute.
3. **Attestation Validation Logic**: Client-side logic to decode, cryptographically verify, and validate COSE Sign1-encoded NitroTPM attestation documents returned by the server, including certificate chain (PKI) validation, COSE signature verification, PCR value validation, and output integrity verification.

## Glossary

- **Caller_Workflow**: GitHub Actions workflow (triggered by `workflow_dispatch`) that sends execution requests to the Remote Executor server and processes results
- **Remote_Executor_Server**: The already-deployed HTTP server (specified in the `github-actions-remote-executor` spec) that executes scripts and returns attestation documents
- **Sample_Build_Script**: A shell script included in the repository that serves as the payload for remote execution
- **Attestation_Document**: Base64-encoded COSE Sign1 structure returned by the Remote Executor server on POST /execute, signed by the NitroTPM, proving the server's execution environment identity. The outer CBOR decoding yields a 4-element array: [protected_header, unprotected_header, payload, signature]. The payload is itself CBOR-encoded and contains the attestation fields (module_id, pcrs, certificate, cabundle, user_data, nonce, public_key, etc.)
- **Output_Attestation_Document**: Base64-encoded COSE Sign1 structure returned by the Remote Executor server on GET /execution/{id}/output when execution is complete, containing a SHA-256 digest of the script output in the user_data field of the payload
- **COSE_Sign1**: CBOR Object Signing and Encryption Sign1 structure — a CBOR array of 4 elements [protected_header, unprotected_header, payload, signature] used to carry a signed attestation payload
- **Execution_ID**: UUID returned by the Remote Executor server that uniquely identifies a script execution request
- **CBOR**: Concise Binary Object Representation — the binary encoding format used for attestation documents and COSE structures
- **NitroTPM**: Trusted Platform Module on the Attestable EC2 instance that signs attestation documents
- **Server_URL**: The base URL of the Remote Executor server, provided as a `workflow_dispatch` input
- **Caller_Script**: Python script that implements the HTTP client logic, attestation validation, and polling loop
- **Output_Digest**: SHA-256 hash of the script output used to verify integrity against the Output_Attestation_Document's user_data field
- **Root_CA_Certificate**: The AWS Nitro Enclaves root certificate authority PEM, hardcoded in the Caller_Workflow definition, used to anchor the certificate chain validation
- **Expected_PCRs**: A JSON map of PCR index (integer) to expected hex-encoded PCR value for PCR4 and PCR7, hardcoded in the Caller_Workflow definition, used to validate the enclave's Platform Configuration Registers against known-good values
- **Certificate_Chain**: The ordered list of intermediate CA certificates (cabundle) included in the attestation document, linking the signing certificate to the Root_CA_Certificate
- **Signing_Certificate**: The DER-encoded X.509 certificate embedded in the attestation document payload, whose public key is used to verify the COSE Sign1 signature

## Requirements

### Requirement 1: Workflow Dispatch Trigger

**User Story:** As a developer, I want to trigger the caller workflow manually with configurable inputs, so that I can specify which Remote Executor server to target and what script to run.

#### Acceptance Criteria

1. THE Caller_Workflow SHALL be triggered by the `workflow_dispatch` event
2. THE Caller_Workflow SHALL accept a required input `server_url` specifying the base URL of the Remote_Executor_Server
3. THE Caller_Workflow SHALL accept an optional input `script_path` with a default value pointing to the Sample_Build_Script
4. THE Caller_Workflow SHALL accept an optional input `commit_hash` that defaults to the current workflow commit SHA
5. IF the `server_url` input is empty, THEN THE Caller_Workflow SHALL fail with a clear error message
6. THE Caller_Workflow SHALL hardcode the AWS Nitro Enclaves Root_CA_Certificate PEM inline in the workflow definition and pass it to the Caller_Script
7. THE Caller_Workflow SHALL hardcode the Expected_PCRs for PCR4 and PCR7 as a JSON-encoded map inline in the workflow definition and pass it to the Caller_Script

### Requirement 2: Sample Build Script

**User Story:** As a developer, I want a sample build script in the repository, so that I have a ready-to-use payload for testing remote execution.

#### Acceptance Criteria

1. THE Sample_Build_Script SHALL be a shell script located at a well-known path in the repository
2. THE Sample_Build_Script SHALL produce output on stdout demonstrating successful execution
3. THE Sample_Build_Script SHALL exit with code 0 on successful completion
4. THE Sample_Build_Script SHALL include basic system information in its output to verify the execution environment

### Requirement 3: Execution Request Submission

**User Story:** As a GitHub Actions workflow, I want to send an execution request to the Remote Executor server, so that the server fetches and runs my script.

#### Acceptance Criteria

1. THE Caller_Script SHALL send an HTTP POST request to `{Server_URL}/execute` with a JSON body containing `repository_url`, `commit_hash`, `script_path`, and `github_token`
2. THE Caller_Script SHALL use the repository URL of the current GitHub repository
3. THE Caller_Script SHALL use the `GITHUB_TOKEN` secret for the `github_token` field
4. WHEN the Remote_Executor_Server returns HTTP 200, THE Caller_Script SHALL extract the Execution_ID and Attestation_Document from the response
5. IF the Remote_Executor_Server returns an HTTP error status, THEN THE Caller_Script SHALL fail the workflow step with the error details
6. IF the Remote_Executor_Server is unreachable, THEN THE Caller_Script SHALL fail the workflow step with a connection error message
7. THE Caller_Script SHALL set a configurable timeout for the HTTP POST request

### Requirement 4: Server Identity Attestation Validation

**User Story:** As a security engineer, I want the caller to cryptographically validate the server's attestation document, so that I can verify the execution request was accepted by a genuine NitroTPM-attested environment with a trusted signing certificate and expected enclave measurements.

#### Acceptance Criteria

##### 4A: COSE Sign1 Parsing

1. WHEN the Caller_Script receives an Attestation_Document from POST /execute, THE Caller_Script SHALL decode the Attestation_Document from base64 to binary
2. THE Caller_Script SHALL parse the decoded binary as a CBOR-encoded COSE_Sign1 structure (a 4-element array: [protected_header, unprotected_header, payload, signature])
3. THE Caller_Script SHALL CBOR-decode the payload element (index 2) of the COSE_Sign1 array to extract the attestation document fields (module_id, digest, timestamp, pcrs, certificate, cabundle, user_data, nonce, public_key)
4. IF the base64 decoding fails, THEN THE Caller_Script SHALL fail the workflow step with a decoding error
5. IF the outer CBOR parsing fails or the result is not a 4-element array, THEN THE Caller_Script SHALL fail the workflow step with a COSE Sign1 structure error
6. IF the payload CBOR decoding fails, THEN THE Caller_Script SHALL fail the workflow step with a payload parsing error
7. THE Caller_Script SHALL verify that the decoded payload contains expected structural fields (module_id, digest, timestamp, pcrs, certificate, cabundle)

##### 4B: Certificate Chain (PKI) Validation

8. THE Caller_Script SHALL validate the Signing_Certificate against the Certificate_Chain and Root_CA_Certificate
9. THE Caller_Script SHALL construct an X509 certificate store containing the Root_CA_Certificate and all intermediate certificates from the cabundle (excluding the first entry, which is the root)
10. THE Caller_Script SHALL load the Signing_Certificate from the certificate field of the attestation payload (DER-encoded)
11. THE Caller_Script SHALL verify the Signing_Certificate against the constructed X509 store
12. IF the certificate chain validation fails, THEN THE Caller_Script SHALL fail the workflow step with a certificate validation error

##### 4C: COSE Signature Verification

13. THE Caller_Script SHALL extract the EC2 public key parameters (x, y coordinates on the P-384 curve) from the Signing_Certificate
14. THE Caller_Script SHALL reconstruct a COSE Sign1 message using the protected header (CBOR-decoded from index 0), unprotected header (index 1), payload (index 2), and signature (index 3)
15. THE Caller_Script SHALL verify the COSE Sign1 signature using the extracted EC2 public key with the ES384 algorithm
16. IF the COSE signature verification fails, THEN THE Caller_Script SHALL fail the workflow step with a signature verification error

##### 4D: PCR Validation

17. THE Caller_Script SHALL compare each expected PCR value (PCR4 and PCR7) against the corresponding PCR in the attestation document
18. IF a specified PCR index is not present in the attestation document, THEN THE Caller_Script SHALL fail the workflow step with a missing PCR error identifying the index
19. IF a PCR value in the attestation document does not match the expected hex value, THEN THE Caller_Script SHALL fail the workflow step with a PCR mismatch error identifying the index

##### 4E: Audit Logging

20. THE Caller_Script SHALL log the attestation document fields for audit purposes

### Requirement 5: Execution Output Polling

**User Story:** As a GitHub Actions workflow, I want to poll for execution results, so that I can retrieve the script output once execution completes.

#### Acceptance Criteria

1. THE Caller_Script SHALL send HTTP GET requests to `{Server_URL}/execution/{Execution_ID}/output` to poll for results
2. THE Caller_Script SHALL poll at a configurable interval with a default of 5 seconds
3. WHILE the response field `complete` is false, THE Caller_Script SHALL continue polling
4. WHEN the response field `complete` is true, THE Caller_Script SHALL extract `stdout`, `stderr`, `exit_code`, and `output_attestation_document` from the response
5. THE Caller_Script SHALL enforce a configurable maximum polling duration with a default of 10 minutes
6. IF the maximum polling duration is exceeded, THEN THE Caller_Script SHALL fail the workflow step with a timeout error
7. IF a polling request fails with an HTTP error, THEN THE Caller_Script SHALL retry up to a configurable number of times before failing
8. THE Caller_Script SHALL log incremental output during polling to provide real-time feedback in the workflow log

### Requirement 6: Output Attestation Validation

**User Story:** As a security engineer, I want the caller to cryptographically validate the output attestation document, so that I can verify the execution output has not been tampered with and was produced by a genuine attested environment.

#### Acceptance Criteria

##### 6A: COSE Sign1 Parsing and Cryptographic Verification

1. WHEN the execution is complete and an Output_Attestation_Document is present, THE Caller_Script SHALL decode the Output_Attestation_Document from base64 to binary
2. THE Caller_Script SHALL parse the decoded binary as a CBOR-encoded COSE_Sign1 structure (a 4-element array)
3. THE Caller_Script SHALL CBOR-decode the payload element of the COSE_Sign1 array to extract the attestation document fields
4. THE Caller_Script SHALL validate the Signing_Certificate from the output attestation against the Certificate_Chain and Root_CA_Certificate using the same PKI validation as Requirement 4B
5. THE Caller_Script SHALL verify the COSE Sign1 signature of the output attestation using the Signing_Certificate's EC2 public key with the ES384 algorithm
6. IF the COSE signature verification of the output attestation fails, THEN THE Caller_Script SHALL fail the workflow step with a signature verification error
7. THE Caller_Script SHALL validate the PCR values in the output attestation using the same PCR validation as Requirement 4D

##### 6B: Output Integrity Verification

8. THE Caller_Script SHALL compute the SHA-256 digest of the returned script output (stdout and stderr concatenated or as defined by the server)
9. THE Caller_Script SHALL extract the user_data field from the CBOR-decoded payload of the Output_Attestation_Document
10. THE Caller_Script SHALL compare the computed SHA-256 digest against the digest in the user_data field of the Output_Attestation_Document
11. IF the digests match, THEN THE Caller_Script SHALL log that output integrity verification succeeded
12. IF the digests do not match, THEN THE Caller_Script SHALL fail the workflow step with an integrity verification error

##### 6C: Error Handling

13. IF the Output_Attestation_Document is null or missing, THEN THE Caller_Script SHALL log a warning and continue without output integrity verification
14. IF the CBOR parsing of the Output_Attestation_Document fails, THEN THE Caller_Script SHALL fail the workflow step with a parsing error

### Requirement 7: Workflow Result Reporting

**User Story:** As a developer, I want execution results reported in the GitHub Actions workflow, so that I can see the script output and verification status directly in the workflow run.

#### Acceptance Criteria

1. THE Caller_Workflow SHALL display the script stdout in the workflow log
2. THE Caller_Workflow SHALL display the script stderr in the workflow log
3. THE Caller_Workflow SHALL display the script exit code in the workflow log
4. THE Caller_Workflow SHALL display the attestation validation result (pass or fail) in the workflow log
5. THE Caller_Workflow SHALL display the output integrity verification result (pass, fail, or skipped) in the workflow log
6. WHEN the script exit code is non-zero, THE Caller_Workflow SHALL mark the workflow step as failed
7. THE Caller_Workflow SHALL produce a summary using GitHub Actions job summary (`$GITHUB_STEP_SUMMARY`) containing execution results and verification status

### Requirement 8: Health Check

**User Story:** As a developer, I want the caller to verify the Remote Executor server is healthy before sending an execution request, so that I get early feedback if the server is unavailable.

#### Acceptance Criteria

1. THE Caller_Script SHALL send an HTTP GET request to `{Server_URL}/health` before submitting the execution request
2. WHEN the health endpoint returns HTTP 200 with `status` equal to `healthy`, THE Caller_Script SHALL proceed with the execution request
3. IF the health endpoint returns a non-200 status or `status` is not `healthy`, THEN THE Caller_Script SHALL fail the workflow step with a server health error
4. IF the health endpoint is unreachable, THEN THE Caller_Script SHALL fail the workflow step with a connection error message
5. THE Caller_Script SHALL set a configurable timeout for the health check request
