# Requirements Document

## Introduction

This document specifies the requirements for the GitHub Actions Remote Executor Caller — a GitHub Actions workflow and supporting scripts that act as the client side of the Remote Executor system. The caller workflow is triggered via `workflow_dispatch`, sends execution requests to an already-deployed Remote Executor server, validates the server's identity and response integrity through NitroTPM attestation documents, and reports results back in the GitHub Actions workflow output.

The caller includes:

1. **GitHub Actions Workflow**: A `workflow_dispatch`-triggered workflow that orchestrates the entire call-validate-poll-verify cycle against the Remote Executor server.
2. **Sample Build Script**: A sample script included in the repository that the Remote Executor server will fetch and execute.
3. **Attestation Validation Logic**: Client-side logic to decode and validate CBOR-encoded NitroTPM attestation documents returned by the server, verifying both server identity and output integrity.

## Glossary

- **Caller_Workflow**: GitHub Actions workflow (triggered by `workflow_dispatch`) that sends execution requests to the Remote Executor server and processes results
- **Remote_Executor_Server**: The already-deployed HTTP server (specified in the `github-actions-remote-executor` spec) that executes scripts and returns attestation documents
- **Sample_Build_Script**: A shell script included in the repository that serves as the payload for remote execution
- **Attestation_Document**: Base64-encoded CBOR document returned by the Remote Executor server on POST /execute, signed by the NitroTPM, proving the server's execution environment identity
- **Output_Attestation_Document**: Base64-encoded CBOR document returned by the Remote Executor server on GET /execution/{id}/output when execution is complete, containing a SHA-256 digest of the script output in the user_data field
- **Execution_ID**: UUID returned by the Remote Executor server that uniquely identifies a script execution request
- **CBOR**: Concise Binary Object Representation — the binary encoding format used for attestation documents
- **NitroTPM**: Trusted Platform Module on the Attestable EC2 instance that signs attestation documents
- **Server_URL**: The base URL of the Remote Executor server, provided as a `workflow_dispatch` input
- **Caller_Script**: Python script that implements the HTTP client logic, attestation validation, and polling loop
- **Output_Digest**: SHA-256 hash of the script output used to verify integrity against the Output_Attestation_Document's user_data field

## Requirements

### Requirement 1: Workflow Dispatch Trigger

**User Story:** As a developer, I want to trigger the caller workflow manually with configurable inputs, so that I can specify which Remote Executor server to target and what script to run.

#### Acceptance Criteria

1. THE Caller_Workflow SHALL be triggered by the `workflow_dispatch` event
2. THE Caller_Workflow SHALL accept a required input `server_url` specifying the base URL of the Remote_Executor_Server
3. THE Caller_Workflow SHALL accept an optional input `script_path` with a default value pointing to the Sample_Build_Script
4. THE Caller_Workflow SHALL accept an optional input `commit_hash` that defaults to the current workflow commit SHA
5. IF the `server_url` input is empty, THEN THE Caller_Workflow SHALL fail with a clear error message

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

**User Story:** As a security engineer, I want the caller to validate the server's attestation document, so that I can verify the execution request was accepted by a genuine NitroTPM-attested environment.

#### Acceptance Criteria

1. WHEN the Caller_Script receives an Attestation_Document from POST /execute, THE Caller_Script SHALL decode the Attestation_Document from base64 to binary
2. THE Caller_Script SHALL parse the decoded binary as a CBOR-encoded attestation document
3. IF the base64 decoding fails, THEN THE Caller_Script SHALL fail the workflow step with a decoding error
4. IF the CBOR parsing fails, THEN THE Caller_Script SHALL fail the workflow step with a parsing error
5. THE Caller_Script SHALL log the attestation document fields for audit purposes
6. THE Caller_Script SHALL verify that the attestation document contains expected structural fields (such as module_id, digest, timestamp, pcrs, certificate, cabundle)

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

**User Story:** As a security engineer, I want the caller to validate the output attestation document, so that I can verify the execution output has not been tampered with.

#### Acceptance Criteria

1. WHEN the execution is complete and an Output_Attestation_Document is present, THE Caller_Script SHALL decode the Output_Attestation_Document from base64 to binary
2. THE Caller_Script SHALL parse the decoded binary as a CBOR-encoded attestation document
3. THE Caller_Script SHALL compute the SHA-256 digest of the returned script output (stdout and stderr concatenated or as defined by the server)
4. THE Caller_Script SHALL extract the user_data field from the parsed Output_Attestation_Document
5. THE Caller_Script SHALL compare the computed SHA-256 digest against the digest in the user_data field of the Output_Attestation_Document
6. IF the digests match, THEN THE Caller_Script SHALL log that output integrity verification succeeded
7. IF the digests do not match, THEN THE Caller_Script SHALL fail the workflow step with an integrity verification error
8. IF the Output_Attestation_Document is null or missing, THEN THE Caller_Script SHALL log a warning and continue without output integrity verification
9. IF the CBOR parsing of the Output_Attestation_Document fails, THEN THE Caller_Script SHALL fail the workflow step with a parsing error

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
