# Requirements Document

## Introduction

This document specifies the requirements for the GitHub Actions Remote Executor system, covering both its runtime behavior and build process.

The GitHub Actions Remote Executor is a secure service that receives script execution requests from GitHub Actions workflows, retrieves scripts from GitHub repositories, generates attestation documents, executes scripts in ephemeral Docker containers, and returns execution results. The system runs on an Attestable EC2 instance with NitroTPM to provide cryptographic attestation of the execution environment. Each script execution runs inside a newly created Docker container that is destroyed after the execution completes, ensuring complete isolation between executions.

This specification covers three major aspects:

1. **Runtime Requirements (Requirements 1-10)**: How the Remote Executor operates when deployed - receiving execution requests, authenticating requests, cloning repositories from GitHub, generating attestations, executing scripts asynchronously, and providing output polling endpoints.

2. **Build Requirements (Requirements 11-21)**: How the attestable AMI containing the Remote Executor is built - using a GitHub Actions workflow to build a KIWI image in a reproducible environment, attesting build artifacts, pushing them to GitHub Container Registry with PCR measurements, and converting the KIWI image into an AWS AMI using a separate EC2 instance that verifies signatures.

3. **Deployment Requirements (Requirements 22-27)**: How the built attestable AMI is deployed as a running target EC2 instance - provisioning an isolated VPC with network infrastructure, configuring security groups with port 8080 open to the world, launching the instance with NitroTPM and IMDSv2, and automating the deployment via a Python script that orchestrates Terraform and persists infrastructure state.

4. **Cleanup Requirements (Requirements 28-31)**: How all AWS resources created during the build and deployment process are removed - loading resource identifiers from the AMI build result file, destroying Terraform-managed infrastructure, deregistering the AMI and associated EBS snapshot, and verifying all resources have been cleaned up.

5. **Debug Requirements (Requirement 32)**: Optional debug features for the deployed instance - enabling SSH access using standard EC2 key pair provisioning for troubleshooting, controlled via opt-in flags in the deployment script and Terraform variables.

6. **Image Provisioning Requirements (Requirements 33-35)**: Packages and services that must be included in the KIWI image for runtime operation - Docker daemon for container execution, container image pulling at startup, and git for repository cloning.

7. **Streaming Output Requirements (Requirement 44)**: How the Script_Executor streams output from Execution_Containers incrementally during execution, so that clients can observe partial output while scripts are still running rather than waiting for the container to exit.

8. **Security Hardening Requirements (Requirements 45-50)**: Additional security requirements for areas not covered by existing requirements - anti-replay protection, debug image gating, artifact provenance workflow verification, Docker daemon hardening, systemd service hardening, and IAM permission scoping.

Note: Security hardening acceptance criteria from the security review are integrated throughout the existing requirements where they naturally belong. Only genuinely new requirement areas appear in the Security Hardening Requirements section.

Note: By default, the KIWI image excludes SSH-related packages (openssh-server, cloud-init, ec2-instance-connect) to remove operator access. The debug feature must be enabled at KIWI image build time to include these packages, and then at deployment time to open port 22 and attach a key pair.

The build process does NOT use the Remote Executor itself (since you can't use something that doesn't exist yet during initial builds). Instead, it uses standard GitHub Actions runners to build the KIWI image, and a temporary EC2 instance to convert it to an AMI.

## Glossary

### Runtime Components
- **GHA_Server**: HTTP server that receives and processes execution requests from GitHub Actions
- **Attestation_Generator**: Component that generates cryptographic attestation documents proving execution environment
- **Script_Executor**: Component that executes scripts inside ephemeral Docker containers
- **Execution_Container**: Ephemeral Docker container created for a single script execution and destroyed after completion
- **Container_Image**: Docker image used as the base for creating Execution_Containers
- **Output_Collector**: Component that captures and stores script execution output
- **Execution_ID**: Unique identifier for each script execution request
- **Repository_Client**: Component that clones repositories from GitHub at specified commits
- **Request_Validator**: Component that validates and authenticates incoming execution requests
- **Attestation_Document**: Cryptographic proof of the execution environment generated by the NitroTPM on the Attestable EC2 instance
- **OIDC_Token**: JSON Web Token (JWT) issued by GitHub's OIDC provider (`https://token.actions.githubusercontent.com`) to a GitHub Actions workflow, included in the encrypted request body as the `oidc_token` field to authenticate requests
- **JWKS**: JSON Web Key Set published by GitHub's OIDC provider, used to verify the cryptographic signature of OIDC_Tokens
- **Allowed_Repositories**: Configured list of GitHub repository full names (e.g., `owner/repo`) permitted to execute scripts on the Remote Executor
- **Expected_Audience**: Configured audience string that the `aud` claim in the OIDC_Token must match, used to ensure the token was issued for this specific Remote Executor instance
- **Execution_Request**: JSON payload containing script location and execution parameters
- **Script_Output**: Captured stdout, stderr, and exit code from script execution
- **Output_Attestation_Document**: Cryptographic attestation document generated on every /execution/{id}/output poll response, containing a SHA-256 digest of the current Script_Output (stdout + stderr + exit_code at that point in time) in the user_data field, enabling the client to verify output integrity regardless of execution status
- **Log_Streaming_Thread**: A daemon background thread started by the Script_Executor for each Execution_Container that uses the Docker SDK's streaming log API to incrementally capture stdout and stderr output during execution, feeding chunks to the Output_Collector in real time so that polling clients can observe partial output before the container exits

### Encryption Components
- **PQ_Hybrid_KEM** (also known as **X25519MLKEM768**): Post-Quantum/Traditional Hybrid Key Encapsulation Mechanism combining X25519 ECDH (classical) with ML-KEM-768 (post-quantum, FIPS 203) to derive a shared secret resistant to both classical and quantum attacks; the hybrid approach ensures security even if one component is broken. The name X25519MLKEM768 follows the convention used by IETF, OpenSSL, and other implementations for this specific hybrid combination
- **ML_KEM**: Module-Lattice-Based Key-Encapsulation Mechanism standardized in FIPS 203, providing post-quantum key encapsulation; the system uses the ML-KEM-768 parameter set (NIST security level 3)
- **Server_Keypair**: A composite key pair consisting of an X25519 key pair and an ML-KEM-768 key pair, generated by the GHA_Server at startup and held in memory for the server's entire lifetime, used for PQ_Hybrid_KEM key agreement
- **Server_Public_Key**: The composite public key portion of the Server_Keypair (containing both the X25519 public key and the ML-KEM-768 encapsulation key), serialized as a length-prefixed concatenation; because the composite key (1216+ bytes) exceeds the NitroTPM attestation document public_key field limit (1024 bytes), the full composite key is returned in the /attest JSON response body, while a SHA-256 fingerprint of the composite key is placed in the attestation document's public_key field for cryptographic binding
- **Client_Keypair**: An X25519 key pair generated by the client for a single key exchange session; the client also generates an ML-KEM-768 ciphertext by encapsulating against the server's ML-KEM-768 encapsulation key
- **Client_Public_Key**: The composite value sent unencrypted alongside encrypted requests, containing the client's X25519 public key and the ML-KEM-768 ciphertext, serialized as a length-prefixed concatenation so the server can derive the same Shared_Key
- **Shared_Key**: A symmetric encryption key derived by combining the X25519 ECDH shared secret and the ML-KEM-768 shared secret via HKDF-SHA256 with a domain-separation label, used to encrypt and decrypt request and response payloads for a specific execution
- **Encryption_Context**: The association between an Execution_ID and its corresponding Shared_Key, stored by the server for the lifetime of that execution
- **Attest_Endpoint**: An unauthenticated HTTP GET endpoint at /attest that returns an Attestation_Document containing a SHA-256 fingerprint of the Server_Public_Key in the public_key field, alongside the full Server_Public_Key in the JSON response body

### Deployment Components
- **Deploy_Terraform**: Terraform configuration in terraform/deploy/ that provisions the target EC2 instance and supporting network infrastructure
- **Deploy_Script**: Python script (scripts/deploy.py) that orchestrates the deployment by loading AMI build results, running Terraform, and saving infrastructure state
- **Target_Instance**: EC2 instance launched from the attestable AMI that runs the Remote Executor service
- **Infrastructure_State**: JSON file containing deployed resource identifiers and attestation API URL

### Cleanup Components
- **Cleanup_Script**: Python script (scripts/cleanup.py) that orchestrates the removal of all AWS resources created during the build and deployment process
- **AMI_Build_Result**: JSON file containing ami_id, snapshot_id, and region fields produced by the AMI build process
- **Keep_AMI_Flag**: Optional CLI flag (--keep-ami) that instructs the Cleanup_Script to skip AMI deregistration and snapshot deletion while still destroying Terraform infrastructure

### Build Components
- **Build_Workflow**: The GitHub Actions workflow that builds the KIWI image and attests artifacts
- **KIWI_Builder**: Docker container that builds the KIWI image using KIWI NG
- **Artifact_Publisher**: Component that pushes build artifacts to GitHub Container Registry using ORAS
- **Attestation_Service**: GitHub's attestation service that signs build artifacts with Sigstore
- **AMI_Converter**: Python script that provisions an EC2 instance and converts KIWI image to AMI
- **Signature_Verifier**: Component that verifies GitHub attestations before AMI creation
- **PCR_Measurements**: Platform Configuration Register measurements (PCR4 and PCR7) for TPM attestation
- **KIWI_Image**: Raw disk image (.raw file) produced by KIWI NG build process
- **Build_Artifact**: Bundle containing KIWI image and PCR measurements file
- **GHCR**: GitHub Container Registry where artifacts are stored

### Security Components
- **Nonce_Cache**: In-memory cache that tracks recently seen nonces from encrypted requests to prevent replay attacks, with entries expiring after a configurable TTL
- **Allowed_Branches**: Optional configured list of branch patterns that the OIDC_Token `ref` claim must match for authorization
- **REQUIRE_PROTECTED_REF**: Optional boolean configuration that, when true, requires the OIDC_Token `ref_protected` claim to be "true"
- **CONTAINER_IMAGE_DIGEST**: Optional configuration value containing a SHA-256 digest used to verify the pulled Container_Image matches the expected image
- **Coldsnap**: AWS tool for uploading raw disk images to EBS snapshots
- **Build_Instance**: Temporary EC2 instance used to convert KIWI image to AMI
- **Docker_Daemon**: The Docker Engine service (dockerd) that must be installed and enabled in the KIWI image to support creation and management of Execution_Containers at runtime
- **Git_Package**: The git version control system binary that must be installed in the KIWI image to support repository cloning operations by the Repository_Client at runtime

## Requirements

## Runtime Requirements

### Requirement 1: HTTP Server Endpoint

**User Story:** As a GitHub Actions workflow, I want to send execution requests to an HTTP endpoint, so that I can trigger script execution in the Remote Executor

#### Acceptance Criteria

1. THE GHA_Server SHALL listen for HTTP POST requests on a configured port
2. THE GHA_Server SHALL accept Execution_Request payloads in JSON format
3. WHEN a valid Execution_Request is received, THE GHA_Server SHALL return an Execution_ID
4. THE GHA_Server SHALL return HTTP 200 OK for valid requests
5. THE GHA_Server SHALL include the Execution_ID in the response body
6. IF the request payload is malformed, THEN THE GHA_Server SHALL return HTTP 400 Bad Request
7. THE GHA_Server SHALL log all incoming requests with timestamps

### Requirement 2: Request Authentication and Validation

**User Story:** As a security engineer, I want execution requests authenticated using GitHub Actions OIDC tokens, so that only workflows from authorized repositories can execute scripts

#### Acceptance Criteria

1. THE Request_Validator SHALL require an oidc_token field in the decrypted request body of each request to the /execute endpoint
2. THE Request_Validator SHALL require an oidc_token field in the decrypted request body of each request to the /execution/{id}/output endpoint
3. IF the oidc_token field is missing from the decrypted request body, THEN THE Request_Validator SHALL reject the request with HTTP 401 Unauthorized and an error message indicating the token is required
4. THE Request_Validator SHALL fetch the JWKS from GitHub's OIDC provider at https://token.actions.githubusercontent.com/.well-known/jwks to verify the OIDC_Token signature
5. THE Request_Validator SHALL cache the fetched JWKS and refresh the cache when a token presents an unknown key ID
6. IF the OIDC_Token signature cannot be verified against the JWKS, THEN THE Request_Validator SHALL reject the request with HTTP 401 Unauthorized
7. THE Request_Validator SHALL validate that the `iss` claim of the OIDC_Token matches `https://token.actions.githubusercontent.com`
8. IF the `iss` claim does not match, THEN THE Request_Validator SHALL reject the request with HTTP 401 Unauthorized
9. THE Request_Validator SHALL validate that the `aud` claim of the OIDC_Token matches the Expected_Audience from configuration
10. IF the `aud` claim does not match the Expected_Audience, THEN THE Request_Validator SHALL reject the request with HTTP 401 Unauthorized
11. THE Request_Validator SHALL validate that the `repository` claim of the OIDC_Token matches an entry in the Allowed_Repositories from configuration
12. IF the `repository` claim does not match any entry in the Allowed_Repositories, THEN THE Request_Validator SHALL reject the request with HTTP 403 Forbidden
13. THE Request_Validator SHALL validate that the OIDC_Token has not expired by checking the `exp` claim against the current time
14. IF the OIDC_Token has expired, THEN THE Request_Validator SHALL reject the request with HTTP 401 Unauthorized
15. THE Request_Validator SHALL validate that the Execution_Request contains required fields
16. THE Request_Validator SHALL validate that the repository URL is a valid GitHub repository
17. THE Request_Validator SHALL validate that the script path is specified
18. IF required fields are missing, THEN THE Request_Validator SHALL return HTTP 400 Bad Request with error details
19. THE Request_Validator SHALL sanitize all input parameters to prevent injection attacks; specifically, `validate_script_path` SHALL reject paths containing null bytes (`\x00`) and SHALL reject absolute paths (paths starting with `/` or `\`) in addition to path traversal sequences, because `os.path.join` silently discards the clone prefix when given an absolute path
20. THE Request_Validator SHALL NOT require authentication for the /health endpoint
21. THE Request_Validator SHALL NOT require authentication for the /attest endpoint
22. WHEN the GHA_Server processes a valid /execute request, THE Request_Validator SHALL verify that the `repository` claim from the validated OIDC_Token matches the `repository_url` field in the Execution_Request
23. IF the `repository` claim does not match the `repository_url`, THEN THE GHA_Server SHALL reject the request with HTTP 403 Forbidden and an error message indicating repository mismatch
24. THE comparison SHALL occur after OIDC_Token validation succeeds and before repository cloning begins
25. THE GHA_Server SHALL support an optional Allowed_Branches configuration value containing a list of allowed branch patterns
26. WHEN Allowed_Branches is configured, THE Request_Validator SHALL validate that the `ref` claim in the OIDC_Token matches one of the allowed branch patterns
27. IF the `ref` claim does not match any allowed branch pattern, THEN THE Request_Validator SHALL reject the request with HTTP 403 Forbidden
28. THE GHA_Server SHALL support an optional REQUIRE_PROTECTED_REF configuration value of type boolean
29. WHEN REQUIRE_PROTECTED_REF is true, THE Request_Validator SHALL validate that the `ref_protected` claim in the OIDC_Token is "true"
30. IF REQUIRE_PROTECTED_REF is true and `ref_protected` is not "true", THEN THE Request_Validator SHALL reject the request with HTTP 403 Forbidden
31. WHEN Allowed_Branches is not configured, THE Request_Validator SHALL skip branch validation
32. WHEN REQUIRE_PROTECTED_REF is not configured or is false, THE Request_Validator SHALL skip protected ref validation

### Requirement 3: Repository Cloning

**User Story:** As a GitHub Actions workflow, I want the Remote Executor to clone my repository at the specified commit, so that scripts can reference other files in the repository during execution

#### Acceptance Criteria

1. WHEN an Execution_Request is validated, THE Repository_Client SHALL clone the specified repository at the specified commit into a temporary directory
2. THE Repository_Client SHALL use `git clone --depth 1` to perform a shallow clone of the repository
3. THE Repository_Client SHALL authenticate to GitHub using the provided token embedded in the clone URL
4. THE Repository_Client SHALL checkout the exact commit specified in the Execution_Request after cloning
5. THE Repository_Client SHALL validate that the script file specified in the Execution_Request exists within the cloned repository
6. IF the repository does not exist or cannot be cloned, THEN THE Repository_Client SHALL record an error for the Execution_ID
7. IF GitHub authentication fails during cloning, THEN THE Repository_Client SHALL record an authentication error
8. THE Repository_Client SHALL support cloning private repositories using the provided token
9. THE Repository_Client SHALL validate that the cloned repository is not empty
10. WHEN the Repository_Client completes a successful clone, THE Repository_Client SHALL strip the GitHub token from the cloned repository's .git/config by running `git remote set-url origin https://github.com/{owner}/{repo}.git` (without the token)
11. THE Repository_Client SHALL perform the token stripping before the cloned repository is mounted into the Execution_Container
12. AFTER token stripping, THE Repository_Client SHALL remove the .git directory entirely from the cloned repository before the repository is mounted into the Execution_Container

### Requirement 4: Attestation Document Generation and Execution Initiation

**User Story:** As a security engineer, I want attestation documents generated before script execution, so that execution environment can be cryptographically verified

#### Acceptance Criteria

1. WHEN a script is successfully retrieved, THE Attestation_Generator SHALL generate an Attestation_Document
2. THE Attestation_Document SHALL include PCR measurements from the NitroTPM on the Attestable EC2 instance
3. THE Attestation_Document SHALL include a timestamp of generation
4. THE Attestation_Document SHALL be cryptographically signed by the NitroTPM on the Attestable EC2 instance
5. THE Attestation_Generator SHALL store the Attestation_Document associated with the Execution_ID
6. WHEN the Attestation_Document is generated, THE Script_Executor SHALL initiate script execution
7. IF attestation generation fails, THEN THE GHA_Server SHALL record an attestation error for the Execution_ID

### Requirement 5: Asynchronous Script Execution in Ephemeral Docker Containers

**User Story:** As a GitHub Actions workflow, I want scripts executed asynchronously inside ephemeral Docker containers, so that I can poll for results without blocking and each execution is fully isolated

#### Acceptance Criteria

1. THE Script_Executor SHALL create a new Execution_Container from the configured Container_Image for each script execution
2. THE Script_Executor SHALL execute each script inside the Execution_Container with a unique Execution_ID
3. THE Script_Executor SHALL NOT reuse an Execution_Container for more than one script execution
4. WHEN script execution completes, THE Script_Executor SHALL remove the Execution_Container and its associated resources
5. IF script execution fails, THEN THE Script_Executor SHALL remove the Execution_Container and its associated resources
6. THE Output_Collector SHALL capture stdout from the Execution_Container
7. THE Output_Collector SHALL capture stderr from the Execution_Container
8. THE Output_Collector SHALL capture the exit code from the Execution_Container
9. THE Script_Executor SHALL enforce a maximum execution timeout of 30 minutes
10. IF the script exceeds the timeout, THEN THE Script_Executor SHALL stop and remove the Execution_Container and record a timeout error
11. THE Script_Executor SHALL execute multiple scripts concurrently in separate Execution_Containers without interference
12. THE Output_Collector SHALL store Script_Output associated with the Execution_ID
13. THE Script_Executor SHALL assign a unique container name derived from the Execution_ID to each Execution_Container
14. THE Script_Executor SHALL stream output from the Execution_Container incrementally during execution rather than capturing output only after the container exits, so that clients polling the output endpoint can observe partial output while the script is still running
15. THE Output_Collector SHALL enforce a maximum output buffer size configurable via MAX_OUTPUT_SIZE_BYTES
16. WHEN the combined stdout and stderr output exceeds MAX_OUTPUT_SIZE_BYTES, THE Output_Collector SHALL truncate the output and mark the output record as truncated

### Requirement 6: Output Polling Endpoint

**User Story:** As a GitHub Actions workflow, I want to poll for execution results, so that I can retrieve script output and attestation documents

#### Acceptance Criteria

1. THE GHA_Server SHALL provide an HTTP GET endpoint for retrieving execution results
2. THE GHA_Server SHALL accept the Execution_ID as a URL parameter
3. THE GHA_Server SHALL require a valid oidc_token in the decrypted request body before returning execution results
4. WHEN the output endpoint is polled, THE GHA_Server SHALL return HTTP 200 OK with the current execution status, Script_Output, Attestation_Document, and Output_Attestation_Document regardless of whether execution is running, completed, failed, or timed_out
5. THE response SHALL include stdout, stderr, and exit code
6. THE response SHALL include the Attestation_Document in base64 encoding
7. WHEN the output endpoint is polled, THE Attestation_Generator SHALL generate an Output_Attestation_Document containing a SHA-256 digest of the current Script_Output (stdout, stderr, and exit_code at that point in time) in the user_data field
8. THE response SHALL include the Output_Attestation_Document in base64 encoding on every poll response
9. THE Output_Attestation_Document SHALL enable the client to verify output integrity by comparing the digest in user_data against the SHA-256 digest of the returned Script_Output at the time of the poll
10. IF the Execution_ID does not exist, THEN THE GHA_Server SHALL return HTTP 404 Not Found
11. IF Output_Attestation_Document generation fails, THEN THE GHA_Server SHALL return the Script_Output and Attestation_Document with an error field indicating attestation failure
12. THE GHA_Server SHALL retain execution results for at least 1 hour after completion
13. WHEN the GHA_Server creates an execution record via /execute, THE Execution_Manager SHALL store the `repository` claim from the validated OIDC_Token in the execution record
14. WHEN the GHA_Server receives a /execution/{id}/output request, THE GHA_Server SHALL compare the `repository` claim from the validated OIDC_Token against the repository stored in the execution record
15. IF the `repository` claim does not match the execution record's repository, THEN THE GHA_Server SHALL reject the request with HTTP 403 Forbidden

### Requirement 7: Error Handling and Logging

**User Story:** As a DevOps engineer, I want comprehensive error handling and logging, so that I can troubleshoot execution failures

#### Acceptance Criteria

1. THE GHA_Server SHALL log all errors with severity levels
2. THE GHA_Server SHALL log request validation failures with details
3. THE Repository_Client SHALL log repository clone errors with details
4. THE Script_Executor SHALL log script execution failures with error messages
5. THE Attestation_Generator SHALL log attestation generation failures
6. WHEN an error occurs, THE GHA_Server SHALL include error details in the polling response
7. THE GHA_Server SHALL log all state transitions for each Execution_ID
8. THE GHA_Server SHALL write logs to a persistent storage location
9. THE GHA_Server SHALL use a contextvars.ContextVar-based approach for storing per-request log context instead of a process-global mutable dictionary
10. THE log context for one request or task SHALL NOT be visible to or modifiable by any other concurrent request or task
11. THE Execution_Manager SHALL store execution durations in a bounded data structure (e.g., a deque with a fixed maximum length) so that the duration history does not grow without bound in long-running deployments

### Requirement 8: Security and Resource Management

**User Story:** As a security engineer, I want resource limits and security controls enforced via Docker container isolation, so that script execution cannot compromise the host system

#### Acceptance Criteria

1. THE Script_Executor SHALL enforce a maximum memory limit on each Execution_Container using Docker memory constraints
2. THE Script_Executor SHALL enforce a maximum CPU time limit on each Execution_Container using Docker CPU constraints
3. THE Script_Executor SHALL execute scripts inside the Execution_Container with a non-root user
4. THE Script_Executor SHALL create each Execution_Container with network access disabled
5. THE Script_Executor SHALL create each Execution_Container with a read-only root filesystem except for a designated execution directory
6. THE Script_Executor SHALL prevent the Execution_Container from gaining additional privileges by disabling privilege escalation
7. THE GHA_Server SHALL limit the number of concurrent Execution_Containers
8. THE GHA_Server SHALL reject new requests when at maximum capacity with HTTP 503 Service Unavailable
9. WHEN an Execution_Container is removed, THE Script_Executor SHALL verify the container no longer exists on the Docker host
10. THE Script_Executor SHALL remove any dangling Execution_Containers on startup that match the container naming convention
11. BEFORE creating a new execution record, THE GHA_Server SHALL check the count of active executions (queued and running) against MAX_CONCURRENT_EXECUTIONS
12. THE concurrency check SHALL be performed atomically to prevent race conditions
13. WHEN the Repository_Client retrieves a script file, THE GHA_Server SHALL check the file size against MAX_SCRIPT_SIZE_BYTES before execution
14. IF the script file size exceeds MAX_SCRIPT_SIZE_BYTES, THEN THE GHA_Server SHALL reject the request with HTTP 413 Payload Too Large
15. THE GHA_Server SHALL schedule periodic invocation of cleanup_expired on the Execution_Manager
16. WHEN cleanup_expired executes, THE Execution_Manager SHALL call remove_output on the Output_Collector and remove_encryption_context on the Encryption_Manager for each expired execution record
17. THE Script_Executor SHALL create each Execution_Container with cap_drop set to ALL to remove all Linux capabilities
18. THE Script_Executor SHALL NOT add back any capabilities unless documented as required with justification
19. THE GHA_Server rate limiter SHALL periodically evict source IP entries whose most recent request timestamp is outside the current rate-limit window, so that the per-IP tracking dictionary does not grow without bound under distributed or spoofed-source traffic

### Requirement 9: Configuration Management

**User Story:** As a DevOps engineer, I want the Remote Executor configured via environment variables or config files, so that I can deploy it in different environments

#### Acceptance Criteria

1. THE GHA_Server SHALL read the listening port from configuration
2. THE GHA_Server SHALL read the Allowed_Repositories list from configuration
3. THE GHA_Server SHALL read the Expected_Audience from configuration
4. THE Repository_Client SHALL read the GitHub access token from configuration
5. THE Script_Executor SHALL read execution timeout limits from configuration
6. THE Script_Executor SHALL read resource limits from configuration
7. THE Script_Executor SHALL read the Container_Image name from configuration
8. THE GHA_Server SHALL read the maximum concurrent executions from configuration
9. THE GHA_Server SHALL validate all configuration values at startup
10. IF required configuration is missing, THEN THE GHA_Server SHALL fail to start with a descriptive error
11. THE Script_Executor SHALL verify that the Docker daemon is accessible at startup
12. IF the Docker daemon is not accessible, THEN THE GHA_Server SHALL fail to start with a descriptive error

### Requirement 10: Health and Monitoring

**User Story:** As a DevOps engineer, I want a health check endpoint, so that I can monitor the Remote Executor's status

#### Acceptance Criteria

1. THE GHA_Server SHALL provide an HTTP GET endpoint for health checks
2. THE health check endpoint SHALL return HTTP 200 OK when the service is healthy
3. THE health check endpoint SHALL return HTTP 503 Service Unavailable when the service is unhealthy
4. THE GHA_Server SHALL apply rate limiting to the /health endpoint
5. THE /health endpoint SHALL return only a simple healthy/unhealthy status without Docker availability, disk space, or active execution count details

## Build Requirements

### Requirement 11: Reproducible KIWI Image Build

**User Story:** As a security engineer, I want the KIWI image to be built in a reproducible containerized environment, so that the build process is consistent and auditable

#### Acceptance Criteria

1. THE Build_Workflow SHALL build the KIWI image inside a Docker container
2. THE KIWI_Builder SHALL use a Dockerfile that specifies exact versions of all build dependencies
3. THE Build_Workflow SHALL checkout the repository with submodules before building
4. THE KIWI_Builder SHALL execute the KIWI NG build script to produce a raw disk image
5. THE Build_Workflow SHALL configure loop devices on the host for KIWI image building
6. THE Build_Workflow SHALL store build outputs in a dedicated build-output directory
7. THE KIWI_Builder SHALL generate PCR measurements file (pcr_measurements.json) containing PCR4 and PCR7 values
8. IF the KIWI build fails, THEN THE Build_Workflow SHALL fail with a descriptive error message
9. THE Build_Workflow SHALL pin the GitHub Actions runner to a specific Ubuntu version (e.g., ubuntu-24.04) instead of ubuntu-latest
10. THE Terraform data source for the Build_Instance AMI SHALL use a specific AMI ID or name filter with a specific version instead of most_recent = true
11. THE Dockerfile for the KIWI builder SHALL include comments documenting that DNF packages are installed without explicit version pinning
12. THE documentation SHALL suggest using --releasever lock or documenting expected package versions for audit purposes
13. THE KIWI image description (appliance.kiwi) SHALL pin the AL2023 package repository URL to a specific release version instead of using the floating `latest` mirrorlist path, and SHALL include a comment explaining the pinning rationale and how to update it

### Requirement 12: Separate Python Dependency Configurations

**User Story:** As a DevOps engineer, I want Python dependencies separated into scripts configuration and remote executor configuration, so that the KIWI image only contains libraries needed for the remote executor service

#### Acceptance Criteria

1. THE Build_Workflow SHALL maintain a scripts configuration file at scripts/pyproject.toml for script dependencies
2. THE scripts configuration SHALL include boto3 for AWS SDK operations
3. THE scripts configuration SHALL include paramiko for SSH connectivity
4. THE Build_Workflow SHALL maintain a remote executor configuration file at pyproject.toml for service dependencies
5. THE remote executor configuration SHALL include fastapi for the HTTP server framework
6. THE remote executor configuration SHALL include uvicorn for the ASGI server
7. THE remote executor configuration SHALL include requests for HTTP client operations
8. THE remote executor configuration SHALL include docker for Docker container management
9. THE remote executor configuration SHALL include wolfcrypt-py for ML-KEM-768 post-quantum key encapsulation operations (FIPS 203) via the wolfcrypt.ciphers module (MlKemType, MlKemPrivate, MlKemPublic classes)
10. WHERE development or testing is performed, THE remote executor configuration SHALL include hypothesis for property-based testing
11. WHERE development or testing is performed, THE remote executor configuration SHALL include pytest for test execution
12. WHERE development or testing is performed, THE remote executor configuration SHALL include pytest-asyncio for async test support
13. WHERE development or testing is performed, THE remote executor configuration SHALL include httpx for async HTTP client testing
14. WHEN building the KIWI image, THE KIWI_Builder SHALL install only the remote executor dependencies from pyproject.toml
15. THE KIWI_Builder SHALL NOT install script dependencies from scripts/pyproject.toml into the KIWI image
16. WHEN executing build scripts, THE Build_Workflow SHALL use dependencies from scripts/pyproject.toml
17. THE scripts configuration and remote executor configuration SHALL be managed independently using uv
18. THE KIWI_Builder SHALL copy pyproject.toml and uv.lock files into the KIWI image build context
19. THE Build_Workflow SHALL extract the dependency list from pyproject.toml and pre-download Python dependency wheels using pip3 download in the build script (which has network access)
20. THE Build_Workflow SHALL copy the pre-downloaded wheels into the KIWI image build context at /tmp/kiwi-build/wheels/
21. THE KIWI_Builder SHALL install Python dependencies from pre-downloaded wheels using pip3 install --no-index --find-links (fully offline, no network required)
22. THE KIWI_Builder SHALL install dependencies to the system Python environment in the KIWI image
23. THE installation process SHALL occur during the KIWI image build phase before the image is finalized

### Requirement 13: Artifact Publishing with PCR Annotations

**User Story:** As a DevOps engineer, I want build artifacts published to GHCR with PCR measurements as annotations, so that consumers can verify expected attestation values

#### Acceptance Criteria

1. WHEN the KIWI image build completes, THE Artifact_Publisher SHALL extract PCR4 and PCR7 from pcr_measurements.json
2. THE Artifact_Publisher SHALL authenticate to GHCR using GitHub token
3. THE Artifact_Publisher SHALL generate an artifact tag using branch name and timestamp
4. THE Artifact_Publisher SHALL push the raw disk image and PCR measurements file using ORAS
5. THE Artifact_Publisher SHALL annotate the artifact with pcr4 and pcr7 values
6. THE Artifact_Publisher SHALL calculate and output the artifact digest
7. IF PCR measurements are missing or invalid, THEN THE Artifact_Publisher SHALL fail with an error
8. IF ORAS push fails, THEN THE Artifact_Publisher SHALL fail with registry connectivity error
9. THE Build_Workflow SHALL verify the SHA-256 checksum of the downloaded ORAS binary against a known expected value before installation; IF the checksum does not match, THEN the workflow SHALL fail with an integrity verification error; THE ORAS version used in the Build_Workflow SHALL match the version used in the AMI_Converter

### Requirement 14: Build Provenance Attestation

**User Story:** As a security engineer, I want build artifacts attested using GitHub's attestation service, so that artifact provenance can be cryptographically verified

#### Acceptance Criteria

1. WHEN artifacts are pushed to GHCR, THE Attestation_Service SHALL generate a build provenance attestation
2. THE Attestation_Service SHALL sign the attestation using Sigstore
3. THE Attestation_Service SHALL include the artifact digest in the attestation
4. THE Attestation_Service SHALL include the repository identity in the attestation
5. THE Attestation_Service SHALL push the attestation to the registry
6. THE Build_Workflow SHALL output the attestation ID and URL
7. THE Build_Workflow SHALL provide verification instructions in the workflow summary

### Requirement 15: AMI Build Instance Provisioning

**User Story:** As a DevOps engineer, I want the AMI conversion to use a temporary EC2 instance provisioned with Terraform, so that the conversion process is isolated and reproducible

#### Acceptance Criteria

1. THE AMI_Converter SHALL provision a Build_Instance using Terraform in the specified AWS region
2. THE AMI_Converter SHALL detect the user's public IP address using checkip.amazonaws.com
3. THE AMI_Converter SHALL create a VPC with CIDR block 10.2.0.0/16 for the Build_Instance
4. THE AMI_Converter SHALL create a public subnet with CIDR block 10.2.1.0/24 in the first availability zone
5. THE AMI_Converter SHALL create an Internet Gateway and route table for internet access
6. THE AMI_Converter SHALL configure security groups to allow SSH only from the user's IP address as /32 CIDR
7. THE AMI_Converter SHALL generate a 4096-bit RSA SSH key pair using Terraform tls_private_key resource
8. THE AMI_Converter SHALL provision the Build_Instance using Amazon Linux 2023 AMI
9. THE AMI_Converter SHALL configure the Build_Instance with IMDSv2 required for metadata access
10. THE AMI_Converter SHALL attach an IAM instance profile with permissions for EC2 and EBS snapshot operations
11. THE AMI_Converter SHALL wait for the instance to be running using EC2 waiter
12. THE AMI_Converter SHALL wait for instance status checks to pass using EC2 waiter
13. THE AMI_Converter SHALL save the SSH private key to a temporary file with 600 permissions
14. IF instance provisioning fails, THEN THE AMI_Converter SHALL fail with a descriptive error
15. THE AMI_Converter SHALL validate the artifact_ref argument against a strict allowlist pattern matching `^ghcr\.io/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+(?:/[a-zA-Z0-9._-]+)*:[a-zA-Z0-9._-]+$` (supporting ghcr.io/owner/repo/package:tag format with optional additional path segments)
16. IF artifact_ref contains characters outside the allowlist, THEN reject and terminate before executing any remote commands

### Requirement 16: SSH Connectivity Verification

**User Story:** As a DevOps engineer, I want SSH connectivity verified before proceeding with tool installation, so that connection issues are detected early

#### Acceptance Criteria

1. THE AMI_Converter SHALL verify SSH connectivity to the Build_Instance using paramiko
2. THE AMI_Converter SHALL connect as ec2-user with the generated SSH private key
3. THE AMI_Converter SHALL retry SSH connection up to 10 times with 30 second delays
4. THE AMI_Converter SHALL configure SSH keepalive with 30 second intervals to prevent connection timeouts
5. THE AMI_Converter SHALL set SSH connection timeout to 10 seconds
6. THE AMI_Converter SHALL set SSH banner timeout to 10 seconds
7. IF SSH connection fails after all retries, THEN THE AMI_Converter SHALL fail with connection error

### Requirement 17: Build Tool Installation

**User Story:** As a DevOps engineer, I want required tools installed on the build instance, so that artifact verification and AMI creation can proceed

#### Acceptance Criteria

1. THE AMI_Converter SHALL install git and gcc on the Build_Instance using dnf package manager
2. THE AMI_Converter SHALL install Rust toolchain by downloading the official standalone Rust installer tarball and its detached GPG signature, verifying the GPG signature before extracting and installing (detailed in acceptance criterion 17)
3. THE AMI_Converter SHALL install ORAS CLI version 1.3.0 from GitHub releases for linux_amd64
4. THE AMI_Converter SHALL extract ORAS binary to /usr/local/bin
5. THE AMI_Converter SHALL install GitHub CLI by adding gh-cli.repo and installing gh package via dnf
6. THE AMI_Converter SHALL clone coldsnap from https://github.com/awslabs/coldsnap.git
7. THE AMI_Converter SHALL build and install coldsnap using cargo install --locked
8. THE AMI_Converter SHALL verify ORAS installation by executing oras version command
9. THE AMI_Converter SHALL verify GitHub CLI installation by executing gh version command
10. THE AMI_Converter SHALL verify coldsnap installation by executing coldsnap --help command
11. THE AMI_Converter SHALL stream installation output to logs during each installation step
12. IF any tool installation fails, THEN THE AMI_Converter SHALL fail with installation error
13. THE AMI_Converter SHALL verify the downloaded ORAS archive against a known SHA-256 checksum before installation
14. IF the ORAS checksum does not match, THEN fail with an integrity verification error
15. THE AMI_Converter SHALL clone coldsnap at a specific pinned git tag or commit hash rather than HEAD
16. THE AMI_Converter SHALL document trust assumptions for the Rust signing key and GitHub CLI in code comments
17. THE AMI_Converter SHALL install Rust using the official standalone installer tarball: download `rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz` from `https://static.rust-lang.org/dist/` and its detached GPG signature (`rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz.asc`) from the same base URL, import the official Rust signing key (85AB96E6FA1BE5FE), verify the GPG signature before extracting, run the included `install.sh` script, and remove the tarball and signature file after installation; IF the GPG signature verification fails, THEN fail with an integrity verification error without extracting or executing the installer

### Requirement 18: Artifact Signature Verification

**User Story:** As a security engineer, I want artifact signatures verified using GitHub attestation before AMI creation, so that only trusted artifacts are converted to AMIs

#### Acceptance Criteria

1. WHEN tools are installed, THE Signature_Verifier SHALL extract repository owner and name from artifact reference
2. THE Signature_Verifier SHALL fetch the artifact manifest digest using oras manifest fetch command
3. THE Signature_Verifier SHALL calculate the SHA256 digest of the manifest
4. THE Signature_Verifier SHALL download the GitHub attestation bundle from api.github.com/repos/{owner}/{repo}/attestations/sha256:{digest}
5. THE Signature_Verifier SHALL extract the first attestation bundle using jq and save to bundle.json
6. THE Signature_Verifier SHALL verify the attestation using gh attestation verify oci:// command with -R flag for repository identity
7. THE Signature_Verifier SHALL verify the attestation in offline mode using -b bundle.json flag
8. THE Signature_Verifier SHALL set GH_FORCE_TTY=1 to force gh to output verification results
9. IF signature verification succeeds with exit code 0, THEN THE AMI_Converter SHALL proceed with artifact download
10. IF signature verification fails with non-zero exit code, THEN THE AMI_Converter SHALL terminate without creating AMI
11. THE AMI_Converter SHALL log detailed verification results including stdout and stderr
12. THE AMI_Converter SHALL not proceed with untrusted artifacts under any circumstances

### Requirement 19: Artifact Download and Validation

**User Story:** As a DevOps engineer, I want artifacts downloaded and validated on the build instance, so that AMI creation uses correct files

#### Acceptance Criteria

1. WHEN signature verification succeeds, THE AMI_Converter SHALL create ~/artifacts directory on the Build_Instance
2. THE AMI_Converter SHALL pull the artifact bundle from GHCR using oras pull command
3. THE AMI_Converter SHALL execute oras pull in the ~/artifacts directory
4. THE AMI_Converter SHALL verify the raw disk image file exists in ~/artifacts/build-output directory using ls *.raw
5. THE AMI_Converter SHALL verify pcr_measurements.json exists in ~/artifacts/build-output directory using test -f
6. THE AMI_Converter SHALL read pcr_measurements.json content using cat command
7. THE AMI_Converter SHALL parse pcr_measurements.json as JSON to extract PCR4 and PCR7 values
8. THE AMI_Converter SHALL log all downloaded artifacts and their sizes using ls -lh
9. THE AMI_Converter SHALL stream ORAS pull output to logs during download
10. IF the raw disk image file is not found, THEN THE AMI_Converter SHALL fail with file not found error
11. IF pcr_measurements.json is not found, THEN THE AMI_Converter SHALL fail with file not found error
12. IF pcr_measurements.json parsing fails, THEN THE AMI_Converter SHALL fail with parsing error

### Requirement 20: Snapshot Upload and AMI Registration

**User Story:** As a DevOps engineer, I want the raw disk image converted to an EBS snapshot and registered as an AMI, so that the image can be launched as EC2 instances

#### Acceptance Criteria

1. WHEN artifacts are validated, THE AMI_Converter SHALL find the raw disk image filename in ~/artifacts/build-output directory
2. THE AMI_Converter SHALL upload the raw disk image using /home/ec2-user/.cargo/bin/coldsnap upload command
3. THE AMI_Converter SHALL stream coldsnap output to logs during upload
4. THE AMI_Converter SHALL parse the snapshot ID starting with snap- from coldsnap stdout
5. THE AMI_Converter SHALL wait for the snapshot to complete using EC2 snapshot_completed waiter
6. THE AMI_Converter SHALL configure the waiter with 15 second delay and 40 max attempts
7. THE AMI_Converter SHALL register the AMI with VirtualizationType set to hvm
8. THE AMI_Converter SHALL register the AMI with BootMode set to uefi
9. THE AMI_Converter SHALL register the AMI with Architecture set to x86_64
10. THE AMI_Converter SHALL register the AMI with TpmSupport set to v2.0
11. THE AMI_Converter SHALL register the AMI with EnaSupport set to True
12. THE AMI_Converter SHALL register the AMI with RootDeviceName set to /dev/xvda
13. THE AMI_Converter SHALL configure BlockDeviceMappings with the snapshot ID for /dev/xvda
14. THE AMI_Converter SHALL generate an AMI name with format attestable-ami-imported-{architecture}-{timestamp} where timestamp uses strftime('%Y-%m-%dT%H-%M-%S') to ensure only AWS-allowed characters (letters, numbers, '(', ')', '.', '-', '/', '_')
15. IF snapshot upload fails, THEN THE AMI_Converter SHALL fail with upload error
16. IF snapshot waiter times out, THEN THE AMI_Converter SHALL fail with waiter error
17. IF AMI registration fails, THEN THE AMI_Converter SHALL fail with ClientError

### Requirement 21: Build Result Output and Infrastructure Cleanup

**User Story:** As a DevOps engineer, I want build results saved to a file and infrastructure cleaned up, so that I can reference the AMI and no resources are left running

#### Acceptance Criteria

1. WHEN AMI registration succeeds, THE AMI_Converter SHALL create a build result dictionary with ami_id, snapshot_id, region, build_timestamp, and pcr_measurements
2. THE build result SHALL include PCR4 value from pcr_measurements.json Measurements.PCR4 field
3. THE build result SHALL include PCR7 value from pcr_measurements.json Measurements.PCR7 field
4. THE build result SHALL include build_timestamp in ISO 8601 format using datetime.now(timezone.utc).isoformat()
5. THE AMI_Converter SHALL write the build result to the output file specified by --output-file argument
6. THE AMI_Converter SHALL format the JSON output with 2-space indentation
7. THE AMI_Converter SHALL close SSH connections before destroying infrastructure
8. THE AMI_Converter SHALL destroy the Build_Instance using terraform destroy -auto-approve command
9. THE AMI_Converter SHALL pass the same Terraform variables used during apply to the destroy command
10. THE AMI_Converter SHALL execute Terraform destroy in the terraform/build-ami working directory
11. THE AMI_Converter SHALL delete the temporary SSH key file using os.unlink
12. THE AMI_Converter SHALL perform cleanup in a finally block to ensure execution even if AMI creation fails
13. IF cleanup fails, THEN THE AMI_Converter SHALL log cleanup errors but not fail the overall process
14. THE AMI_Converter SHALL log all cleanup operations including infrastructure destruction and key deletion
15. THE AMI_Converter SHALL overwrite the temporary SSH key file with random bytes before unlinking
16. THE AMI_Converter SHALL document that Terraform state contains sensitive SSH key material

### Requirement 33: Docker Daemon Provisioning in KIWI Image

**User Story:** As a DevOps engineer, I want the KIWI image to include Docker with the daemon enabled, so that the Remote Executor can create and manage Execution_Containers at runtime without additional provisioning

#### Acceptance Criteria

1. THE appliance.kiwi package definition SHALL include the docker package in the image packages list
2. THE config.sh configuration script SHALL enable the docker service using systemctl enable during image creation
3. WHEN the KIWI image boots, THE Docker daemon SHALL be running and accessible to the Script_Executor
4. IF the docker package is not present in appliance.kiwi, THEN THE KIWI_Builder SHALL fail to produce an image capable of running Execution_Containers

### Requirement 34: Pull Container Image at Server Startup

**User Story:** As a DevOps engineer, I want the Container_Image pulled when the Remote Executor server starts, so that Execution_Containers can be created without baking the image into the KIWI image at build time

#### Acceptance Criteria

1. WHEN the GHA_Server starts, THE GHA_Server SHALL pull the configured Container_Image from the container registry using the Docker daemon
2. THE GHA_Server SHALL pull the Container_Image before accepting any execution requests
3. THE GHA_Server SHALL verify that the Container_Image is available in the local Docker image store after pulling
4. IF the Container_Image pull fails, THEN THE GHA_Server SHALL fail to start with a descriptive error message indicating the image name and failure reason
5. IF the Container_Image is already present in the local Docker image store, THE GHA_Server SHALL skip pulling and use the existing image
6. THE GHA_Server SHALL log the Container_Image pull operation including image name, pull duration, and image size
7. THE GHA_Server SHALL support an optional CONTAINER_IMAGE_DIGEST configuration value containing a SHA-256 digest
8. WHEN CONTAINER_IMAGE_DIGEST is configured, THE GHA_Server SHALL verify the pulled image matches the expected digest
9. IF the digest does not match, THEN fail to start with a descriptive error
10. THE GHA_Server SHALL support digest-pinned container image references (e.g., python:3.11-slim@sha256:...)
11. THE default env file and .env.example SHALL include a CONTAINER_IMAGE_DIGEST entry (empty by default) with a comment instructing operators to set a digest in production to prevent tag drift or registry compromise

### Requirement 35: Git Package Provisioning in KIWI Image

**User Story:** As a DevOps engineer, I want the KIWI image to include the git package, so that the Repository_Client can clone GitHub repositories at runtime using git commands

#### Acceptance Criteria

1. THE appliance.kiwi package definition SHALL include the git package in the image packages list
2. WHEN the KIWI image boots, THE git binary SHALL be available in the system PATH for the Repository_Client to invoke via subprocess
3. IF the git package is not present in appliance.kiwi, THEN THE Repository_Client SHALL fail to clone repositories because the git binary is unavailable

### Requirement 44: Streaming Output Capture During Container Execution

**User Story:** As a GitHub Actions workflow, I want to see partial script output while the script is still running, so that I can monitor execution progress in real time rather than waiting for the entire script to finish before any output appears

#### Acceptance Criteria

1. THE Script_Executor SHALL start a background log-streaming thread for each Execution_Container immediately after the container is started and before calling container.wait()
2. THE log-streaming thread SHALL use the Docker SDK's `container.logs(stream=True, follow=True, stdout=True, stderr=False)` API to receive stdout output incrementally as the container produces it
3. THE log-streaming thread SHALL use a separate `container.logs(stream=True, follow=True, stdout=False, stderr=True)` call or a combined stream to receive stderr output incrementally
4. THE log-streaming thread SHALL feed each received chunk of output to the Output_Collector via `capture_output(execution_id, stream_name, chunk)` so that the output buffer is populated incrementally during execution
5. THE log-streaming thread SHALL terminate gracefully when the container exits (the Docker SDK log stream ends when the container stops)
6. THE log-streaming thread SHALL handle Docker API errors gracefully by logging a warning and continuing to attempt capture, so that transient errors do not cause output loss
7. WHEN the container exits, THE Script_Executor SHALL NOT call `_capture_container_logs()` to re-capture the full output in a single batch, because the streaming thread has already captured all output incrementally
8. THE streaming approach SHALL ensure that clients polling the /execution/{id}/output endpoint observe partial output within one poll interval of the output being produced by the script, rather than seeing empty output until the container exits
9. THE log-streaming thread SHALL NOT block the container.wait() call; both SHALL run concurrently so that the Script_Executor can detect container completion while output is being streamed
10. IF the container is stopped due to a timeout, THE log-streaming thread SHALL capture any output produced up to the point of termination before the thread exits
11. THE log-streaming thread SHALL be implemented as a daemon thread so that it does not prevent the server process from shutting down

### Requirement 36: Server Keypair Generation and Lifecycle

**User Story:** As a security engineer, I want the server to generate a composite post-quantum hybrid keypair at startup and retain it in memory for its entire lifetime, so that clients can establish encrypted channels resistant to both classical and quantum attacks

#### Acceptance Criteria

1. WHEN the GHA_Server starts, THE GHA_Server SHALL generate a Server_Keypair consisting of an X25519 key pair and an ML-KEM-768 key pair
2. THE GHA_Server SHALL store the Server_Keypair in memory only and SHALL NOT persist the Server_Keypair to disk
3. THE Server_Keypair SHALL remain the same for the entire lifetime of the GHA_Server process
4. THE GHA_Server SHALL use the cryptography library for X25519 key generation and the wolfcrypt-py library (wolfcrypt.ciphers module: MlKemPrivate with MlKemType.ML_KEM_768) for ML-KEM-768 key generation
5. THE GHA_Server SHALL log the generation of the Server_Keypair at startup at INFO level without logging any private key material or decapsulation key material
6. THE Server_Public_Key SHALL be serialized as a length-prefixed concatenation of the 32-byte X25519 public key and the 1184-byte ML-KEM-768 encapsulation key, with each component preceded by a 4-byte big-endian length prefix

### Requirement 37: Attestation Endpoint

**User Story:** As a client, I want an unauthenticated endpoint to request an attestation document and the server's composite public key, so that I can verify the server environment and obtain the Server_Public_Key for post-quantum hybrid key exchange

#### Acceptance Criteria

1. THE GHA_Server SHALL provide an HTTP GET endpoint at /attest for requesting Attestation_Documents
2. THE /attest endpoint SHALL NOT require authentication
3. THE /attest endpoint SHALL accept an optional query parameter named nonce
4. WHEN a request to /attest is received, THE Attestation_Generator SHALL compute a SHA-256 fingerprint of the serialized Server_Public_Key and include the fingerprint in the public_key field of the Attestation_Document
5. WHEN a nonce query parameter is provided, THE Attestation_Generator SHALL include the nonce value in the generated Attestation_Document
6. THE /attest endpoint SHALL return a JSON response body containing both the base64-encoded Attestation_Document and the base64-encoded serialized Server_Public_Key as separate fields
7. THE Attestation_Document returned by /attest SHALL NOT be encrypted
8. IF attestation generation fails, THEN THE GHA_Server SHALL return HTTP 500 Internal Server Error with an error message indicating attestation failure
9. THE /attest endpoint SHALL be the only endpoint whose Attestation_Document includes the Server_Public_Key fingerprint in the public_key field
10. THE Attestation_Document returned by /attest SHALL NOT include user_data; only the public_key field (containing the SHA-256 fingerprint) and optionally the nonce SHALL be present
11. THE client SHALL verify the Server_Public_Key by computing the SHA-256 fingerprint of the received composite key and comparing it against the fingerprint in the Attestation_Document public_key field
12. THE GHA_Server SHALL apply per-IP rate limiting to the /attest endpoint
13. IF a client exceeds the rate limit on /attest, THEN return HTTP 429 Too Many Requests

### Requirement 38: Nonce Support in Attestation Responses

**User Story:** As a client, I want to include a random nonce in any request that responds with an attestation document, so that I can verify the freshness of the attestation document and prevent replay attacks

#### Acceptance Criteria

1. THE /attest endpoint SHALL accept an optional nonce query parameter
2. THE /execute endpoint SHALL accept an optional nonce field in the (encrypted) request body
3. THE /execution/{execution_id}/output endpoint SHALL accept an optional nonce query parameter
4. WHEN a nonce is provided in any request that generates an Attestation_Document, THE Attestation_Generator SHALL pass the nonce to the nitro-tpm-attest tool for inclusion in the Attestation_Document
5. WHEN no nonce is provided, THE Attestation_Generator SHALL generate the Attestation_Document without a nonce
6. THE Attestation_Document SHALL include the client-provided nonce so the client can verify the attestation was generated in response to its specific request

### Requirement 39: Server Public Key in Attestation Documents

**User Story:** As a client, I want the server's composite public key cryptographically bound to the /attest attestation document, so that I can verify the public key belongs to the attested server environment before establishing a post-quantum hybrid encrypted channel

#### Acceptance Criteria

1. WHEN generating an Attestation_Document for the /attest endpoint, THE Attestation_Generator SHALL compute a SHA-256 fingerprint of the serialized Server_Public_Key and include the fingerprint in the public_key field of the Attestation_Document, because the composite key exceeds the 1024-byte public_key field limit
2. WHEN generating an Attestation_Document for any endpoint other than /attest (e.g., /execute, /execution/{id}/output), THE Attestation_Generator SHALL NOT include the Server_Public_Key fingerprint in the Attestation_Document
3. THE Server_Public_Key SHALL be serialized as a deterministic length-prefixed concatenation of the X25519 public key and the ML-KEM-768 encapsulation key, and the SHA-256 fingerprint SHALL be computed over this serialized form
4. THE client SHALL extract the Server_Public_Key from the /attest JSON response body, compute its SHA-256 fingerprint, and verify it matches the fingerprint in the Attestation_Document public_key field before using the key for PQ_Hybrid_KEM key exchange

### Requirement 40: Post-Quantum Hybrid Encrypted Execute Requests

**User Story:** As a security engineer, I want the /execute request payload encrypted using a post-quantum hybrid key exchange combining X25519 and ML-KEM-768, so that sensitive data including the GitHub OIDC token is protected against both classical and quantum attacks

#### Acceptance Criteria

1. WHEN calling /execute, THE client SHALL derive a Shared_Key by performing both an X25519 ECDH exchange (using a fresh Client_Keypair and the server's X25519 public key from the Server_Public_Key) and an ML-KEM-768 encapsulation (against the server's ML-KEM-768 encapsulation key from the Server_Public_Key), then combining both shared secrets via HKDF-SHA256 with a domain-separation label
2. THE client SHALL send the Client_Public_Key (containing the client's X25519 public key and the ML-KEM-768 ciphertext, serialized as a length-prefixed concatenation) unencrypted alongside the encrypted request payload so the server can derive the same Shared_Key
3. WHEN the GHA_Server receives an /execute request, THE GHA_Server SHALL parse the Client_Public_Key to extract the client's X25519 public key and the ML-KEM-768 ciphertext, perform X25519 ECDH and ML-KEM-768 decapsulation using the Server_Keypair, and combine both shared secrets via HKDF-SHA256 with the same domain-separation label to derive the same Shared_Key
4. THE GHA_Server SHALL decrypt the request payload using the derived Shared_Key with AES-256-GCM
5. IF decryption of the request payload fails, THEN THE GHA_Server SHALL return HTTP 400 Bad Request with an error message indicating decryption failure
6. IF the Client_Public_Key cannot be parsed or contains invalid X25519 or ML-KEM-768 components, THEN THE GHA_Server SHALL return HTTP 400 Bad Request with an error message indicating an invalid client public key
7. THE encrypted request payload SHALL include the OIDC_Token in a field named oidc_token within the request body so the token is protected by encryption rather than sent in the Authorization header
8. THE encrypted request payload SHALL include all fields of the Execution_Request (repository_url, commit_hash, script_path, github_token, oidc_token)
9. THE GHA_Server SHALL process the decrypted request payload using the same validation and execution logic as an unencrypted request
10. THE GHA_Server SHALL extract the OIDC_Token from the decrypted request body oidc_token field for authentication instead of from the Authorization header
11. THE HKDF-SHA256 derivation SHALL use a domain-separation info label of b"pq-hybrid-shared-key" to distinguish the hybrid key derivation from other uses of HKDF in the system

### Requirement 41: Execution-Bound Shared Key Storage

**User Story:** As a security engineer, I want the shared encryption key bound to a specific execution, so that all subsequent request and response data for that execution is encrypted with the same key

#### Acceptance Criteria

1. WHEN the GHA_Server successfully decrypts an /execute request, THE GHA_Server SHALL store the Shared_Key in an Encryption_Context associated with the Execution_ID
2. THE Encryption_Context SHALL persist for the lifetime of the execution
3. THE GHA_Server SHALL use the Shared_Key from the Encryption_Context to encrypt the /execute response payload
4. THE GHA_Server SHALL use the Shared_Key from the Encryption_Context to decrypt the /execution/{execution_id}/output request payload
5. THE GHA_Server SHALL use the Shared_Key from the Encryption_Context to encrypt the /execution/{execution_id}/output response payload
6. WHEN the execution record is cleaned up, THE GHA_Server SHALL remove the associated Encryption_Context from memory
7. THE Encryption_Context SHALL be stored in memory only and SHALL NOT be persisted to disk

### Requirement 42: Encrypted Request and Response Payloads

**User Story:** As a security engineer, I want request and response payloads encrypted using the execution-bound shared key derived from the post-quantum hybrid key exchange, so that script output, OIDC tokens, and sensitive data are protected against both classical and quantum attacks

#### Acceptance Criteria

1. WHEN responding to /execute, THE GHA_Server SHALL encrypt the response payload (containing execution_id, attestation_document, and status) using the Shared_Key from the Encryption_Context
2. WHEN the client sends a request to /execution/{execution_id}/output, THE client SHALL encrypt the request payload (containing the oidc_token and optional nonce) using the Shared_Key
3. WHEN the GHA_Server receives a /execution/{execution_id}/output request, THE GHA_Server SHALL decrypt the request payload using the Shared_Key from the Encryption_Context for that execution_id
4. WHEN responding to /execution/{execution_id}/output, THE GHA_Server SHALL encrypt the response payload (containing stdout, stderr, exit_code, and status) using the Shared_Key from the Encryption_Context
5. THE Attestation_Documents included in encrypted responses SHALL NOT be separately encrypted beyond the payload-level encryption
6. IF no Encryption_Context exists for the requested execution_id, THEN THE GHA_Server SHALL return HTTP 400 Bad Request with an error message indicating no encryption context is available
7. IF decryption of the /execution/{execution_id}/output request payload fails, THEN THE GHA_Server SHALL return HTTP 400 Bad Request with an error message indicating decryption failure
8. THE client SHALL decrypt response payloads using the same Shared_Key derived during the PQ_Hybrid_KEM key exchange

### Requirement 43: Attestation Document Encryption Exemption

**User Story:** As a client, I want attestation documents from the /attest endpoint returned unencrypted, so that I can obtain the server's public key before establishing an encrypted channel

#### Acceptance Criteria

1. THE /attest endpoint response SHALL NOT be encrypted
2. THE /health endpoint response SHALL NOT be encrypted
3. THE GHA_Server SHALL apply encryption only to endpoints that operate within an Encryption_Context (/execute and /execution/{execution_id}/output)

## Deployment Requirements

### Requirement 22: Deployment Network Infrastructure

**User Story:** As a DevOps engineer, I want the deployment to provision an isolated VPC with internet access, so that the target EC2 instance can serve HTTP requests while remaining network-isolated from other resources

#### Acceptance Criteria

1. THE Deploy_Terraform SHALL create a VPC with CIDR block 10.0.0.0/16 with DNS hostnames and DNS support enabled
2. THE Deploy_Terraform SHALL create a public subnet with CIDR block 10.0.1.0/24 in the first available availability zone
3. THE Deploy_Terraform SHALL configure the public subnet to map public IP addresses on launch
4. THE Deploy_Terraform SHALL create an Internet Gateway attached to the VPC
5. THE Deploy_Terraform SHALL create a route table with a default route (0.0.0.0/0) through the Internet Gateway
6. THE Deploy_Terraform SHALL associate the route table with the public subnet
7. THE Deploy_Terraform SHALL tag all network resources with descriptive Name tags prefixed with "github-runner-ec2-attestation"

### Requirement 23: Deployment Security Group Configuration

**User Story:** As a security engineer, I want the target instance's network access configured with HTTP on port 8080 open to the world, so that any authorized GitHub Actions workflow can reach the attestation API

#### Acceptance Criteria

1. THE Deploy_Terraform SHALL create a security group in the deployment VPC
2. THE security group SHALL allow inbound TCP traffic on port 8080 from 0.0.0.0/0
3. THE security group SHALL allow all outbound traffic
4. THE security group SHALL NOT allow inbound SSH access on port 22 by default
5. THE security group SHALL NOT allow inbound traffic on any port other than 8080 by default

### Requirement 24: Target EC2 Instance Provisioning

**User Story:** As a DevOps engineer, I want the target EC2 instance launched from the attestable AMI with NitroTPM and IMDSv2 enabled, so that the instance supports hardware-based attestation and secure metadata access

#### Acceptance Criteria

1. THE Deploy_Terraform SHALL launch an EC2 instance using the attestable_ami_id variable
2. THE Deploy_Terraform SHALL require the attestable_ami_id variable as a mandatory input with no default value
3. THE Deploy_Terraform SHALL use instance type from the instance_type variable with default value c5.9xlarge
4. THE Deploy_Terraform SHALL place the instance in the public subnet with an associated public IP address
5. THE Deploy_Terraform SHALL attach the deployment security group to the instance
6. THE Deploy_Terraform SHALL enable detailed monitoring on the instance
7. THE Deploy_Terraform SHALL configure IMDSv2 as required by setting http_tokens to "required"
8. THE Deploy_Terraform SHALL set the IMDSv2 http_put_response_hop_limit to 1
9. THE Deploy_Terraform SHALL use the aws_region variable with default value us-east-1
10. THE Deploy_Terraform SHALL use the AWS provider version ~> 5.0

### Requirement 25: Deployment Outputs

**User Story:** As a DevOps engineer, I want Terraform to output all key resource identifiers and the attestation API URL, so that downstream processes can reference the deployed infrastructure

#### Acceptance Criteria

1. THE Deploy_Terraform SHALL output the vpc_id of the created VPC
2. THE Deploy_Terraform SHALL output the subnet_id of the created public subnet
3. THE Deploy_Terraform SHALL output the security_group_id of the created security group
4. THE Deploy_Terraform SHALL output the instance_id of the launched EC2 instance
5. THE Deploy_Terraform SHALL output the instance_public_ip of the launched EC2 instance
6. THE Deploy_Terraform SHALL output the attestation_api_url constructed as http://{instance_public_ip}:8080

### Requirement 26: Deployment Script AMI Loading

**User Story:** As a DevOps engineer, I want the deployment script to load AMI build results from a JSON file, so that deployment is automated

#### Acceptance Criteria

1. THE Deploy_Script SHALL accept a --ami-build-result argument with default value ami_build_result.json
2. THE Deploy_Script SHALL accept an --instance-type argument with default value c5.9xlarge
3. THE Deploy_Script SHALL accept an --output-file argument with default value infrastructure_state.json
4. IF the AMI build result file does not exist, THEN THE Deploy_Script SHALL fail with a FileNotFoundError
5. THE Deploy_Script SHALL parse the AMI build result file as JSON and extract ami_id, snapshot_id, and region fields
6. IF the AMI build result file cannot be parsed, THEN THE Deploy_Script SHALL fail with a RuntimeError

### Requirement 27: Deployment Script Terraform Orchestration and State Persistence

**User Story:** As a DevOps engineer, I want the deployment script to run Terraform init and apply, then save the infrastructure state to a JSON file, so that the deployment is fully automated and results are persisted

#### Acceptance Criteria

1. THE Deploy_Script SHALL run terraform init in the terraform/deploy directory
2. IF the terraform/deploy directory does not exist, THEN THE Deploy_Script SHALL fail with a FileNotFoundError
3. IF terraform init fails with a non-zero exit code, THEN THE Deploy_Script SHALL fail with a RuntimeError
4. THE Deploy_Script SHALL run terraform apply -auto-approve with variables attestable_ami_id, instance_type, and aws_region passed via -var flags
5. IF terraform apply fails with a non-zero exit code, THEN THE Deploy_Script SHALL fail with a RuntimeError
6. WHEN terraform apply succeeds, THE Deploy_Script SHALL retrieve outputs by running terraform output -json
7. THE Deploy_Script SHALL extract the value field from each raw Terraform output entry
8. THE Deploy_Script SHALL write the extracted infrastructure state to the output file as JSON with 2-space indentation
9. IF saving the infrastructure state file fails, THEN THE Deploy_Script SHALL fail with a RuntimeError
10. THE Deploy_Script SHALL log all operations including Terraform variable values, command outputs, and final infrastructure state summary
11. IF any deployment step fails, THEN THE Deploy_Script SHALL log a message advising the user to run terraform destroy to clean up partial resources


## Cleanup Requirements

### Requirement 28: Cleanup Script Configuration and Input Loading

**User Story:** As a DevOps engineer, I want the cleanup script to load resource identifiers from the AMI build result file, so that the correct resources are targeted for deletion

#### Acceptance Criteria

1. THE Cleanup_Script SHALL accept a --ami-build-result argument with default value ami_build_result.json
2. THE Cleanup_Script SHALL accept a --terraform-dir argument with default value terraform/deploy
3. THE Cleanup_Script SHALL accept a --keep-ami flag that defaults to disabled
4. IF the AMI build result file does not exist, THEN THE Cleanup_Script SHALL fail with a FileNotFoundError
5. THE Cleanup_Script SHALL parse the AMI build result file as JSON and extract ami_id, snapshot_id, and region fields
6. IF the AMI build result file cannot be parsed, THEN THE Cleanup_Script SHALL fail with a RuntimeError
7. THE Cleanup_Script SHALL log the loaded ami_id, snapshot_id, and region values at INFO level
8. WHEN --keep-ami is provided, THE Cleanup_Script SHALL log at INFO level that AMI and snapshot will be preserved
9. THE Cleanup_Script SHALL configure logging to output to both stdout and a cleanup.log file
10. THE Cleanup_Script SHALL prompt the user for confirmation before proceeding with resource destruction
11. IF the user does not confirm with "yes" or "y", THEN THE Cleanup_Script SHALL exit with return code 0 without deleting resources

### Requirement 29: Terraform Infrastructure Destruction

**User Story:** As a DevOps engineer, I want the cleanup script to destroy all Terraform-managed deployment infrastructure, so that VPC, security groups, and EC2 instances are removed

#### Acceptance Criteria

1. THE Cleanup_Script SHALL run terraform init in the terraform-dir directory before executing destroy
2. IF the terraform-dir directory does not exist, THEN THE Cleanup_Script SHALL log a warning and skip Terraform destruction
3. IF no terraform.tfstate file exists in the terraform-dir, THEN THE Cleanup_Script SHALL log a warning and skip Terraform destruction
4. IF terraform init fails with a non-zero exit code, THEN THE Cleanup_Script SHALL raise a RuntimeError
5. THE Cleanup_Script SHALL run terraform destroy -auto-approve with dummy variable values for attestable_ami_id and allowed_http_cidr
6. IF terraform destroy fails with a non-zero exit code, THEN THE Cleanup_Script SHALL raise a RuntimeError
7. WHEN terraform destroy succeeds, THE Cleanup_Script SHALL verify the Terraform state file shows no remaining resources
8. IF the Terraform state still contains resources after destroy, THEN THE Cleanup_Script SHALL log a warning indicating resources may not have been destroyed properly

### Requirement 30: AMI Deregistration and Snapshot Deletion

**User Story:** As a DevOps engineer, I want the cleanup script to deregister the attestable AMI and delete the associated EBS snapshot, so that no unused images remain in the AWS account

#### Acceptance Criteria

1. THE Cleanup_Script SHALL create an EC2 client using the region from the AMI build result
2. WHEN --keep-ami is not provided, THE Cleanup_Script SHALL check if the AMI exists before attempting deregistration using describe_images
3. IF the AMI is not found (InvalidAMIID.NotFound), THEN THE Cleanup_Script SHALL log a warning and skip AMI deregistration
4. WHEN --keep-ami is not provided, THE Cleanup_Script SHALL deregister the AMI using the EC2 DeregisterImage API with DeleteAssociatedSnapshots set to True
5. WHEN the AMI is deregistered, THE Cleanup_Script SHALL wait 2 seconds and verify the deregistration propagated using describe_images
6. WHEN the AMI is deregistered, THE Cleanup_Script SHALL verify the snapshot deletion propagated using describe_snapshots
7. IF the EC2 DeregisterImage API call fails, THEN THE Cleanup_Script SHALL log the error and raise the ClientError
8. WHEN --keep-ami is provided, THE Cleanup_Script SHALL skip AMI deregistration and snapshot deletion entirely
9. WHEN --keep-ami is provided, THE Cleanup_Script SHALL log at INFO level that AMI deregistration and snapshot deletion were skipped

### Requirement 31: Cleanup Verification and Reporting

**User Story:** As a DevOps engineer, I want the cleanup script to verify all resources have been removed and report any remaining resources, so that I can manually clean up anything that was missed

#### Acceptance Criteria

1. WHEN AMI deregistration completes, THE Cleanup_Script SHALL check for remaining EC2 instances tagged with Purpose "AMI Build" or "Attestation Demo" in pending, running, stopping, or stopped states
2. WHEN --keep-ami is not provided, THE Cleanup_Script SHALL check for the specific AMI by ami_id from the build result
3. WHEN --keep-ami is not provided, THE Cleanup_Script SHALL check for the specific EBS snapshot by snapshot_id from the build result
4. WHEN --keep-ami is provided, THE Cleanup_Script SHALL exclude the AMI and EBS snapshot from the remaining-resource check
5. IF remaining resources are found, THEN THE Cleanup_Script SHALL log a warning listing each resource type, ID, and status
6. IF remaining resources are found, THEN THE Cleanup_Script SHALL advise the user to manually delete the listed resources
7. IF no remaining resources are found, THEN THE Cleanup_Script SHALL log that cleanup verification is complete and all resources are removed
8. WHEN --keep-ami is provided and no remaining resources are found (excluding the preserved AMI and snapshot), THE Cleanup_Script SHALL log that cleanup verification is complete and the AMI and snapshot were intentionally preserved
9. IF any step in the cleanup process fails, THEN THE Cleanup_Script SHALL return exit code 1 and log that some resources may still exist
10. WHEN all cleanup steps succeed, THE Cleanup_Script SHALL return exit code 0

## Debug Requirements

### Requirement 32: Debug SSH Access for KIWI Image Build

**User Story:** As a DevOps engineer, I want to optionally build a KIWI image with SSH access enabled, so that instances launched from the AMI can be accessed via SSH for debugging using standard EC2 key pair provisioning

#### Acceptance Criteria

##### GitHub Actions Workflow

1. THE Build_Workflow SHALL default to building the KIWI image without SSH access on all triggers (push, pull_request, schedule)
2. THE Build_Workflow SHALL define a workflow_dispatch input named enable_ssh of type boolean with default value false and a description indicating it enables SSH debug access in the built image
3. WHEN triggered via workflow_dispatch with enable_ssh set to true, THE Build_Workflow SHALL pass the --enable-ssh flag to the build-kiwi-image.sh script
4. WHEN triggered via push or any non-workflow_dispatch event, THE Build_Workflow SHALL NOT pass the --enable-ssh flag to the build script
5. WHEN the image is built with SSH enabled, THE Build_Workflow SHALL append a prominent warning to the GitHub Actions job summary (GITHUB_STEP_SUMMARY) indicating that the image was built with SSH debug access enabled and is NOT intended for production use
6. THE warning in the job summary SHALL be visually distinct (e.g., using a blockquote with a warning emoji or similar markdown formatting) so it cannot be overlooked

##### Build-Time (KIWI Image)

7. THE build-kiwi-image.sh script SHALL accept an optional --enable-ssh flag
8. WHEN --enable-ssh is passed, THE build script SHALL modify the KIWI image description to remove the ignore directives for openssh-server, cloud-init, cloud-init-cfg-ec2, and ec2-instance-connect before building
9. WHEN --enable-ssh is NOT passed, THE KIWI image SHALL continue to exclude openssh-server, cloud-init, cloud-init-cfg-ec2, and ec2-instance-connect via ignore directives (default secure behavior)
10. WHEN --enable-ssh is passed, THE config.sh script SHALL enable the sshd service via systemctl enable sshd
11. WHEN --enable-ssh is NOT passed, THE config.sh script SHALL NOT enable the sshd service
12. THE build script SHALL pass an ENABLE_SSH environment variable to the KIWI builder Docker container
13. THE config.sh script SHALL read the ENABLE_SSH environment variable to conditionally enable sshd
14. WHEN SSH is enabled, THE KIWI image SHALL rely on cloud-init and ec2-instance-connect for SSH key provisioning using standard EC2 key pair mechanisms (no baked-in keys)

##### Deployment-Time (Terraform and Deploy Script)

15. THE Deploy_Script SHALL accept an optional --enable-ssh flag that defaults to disabled
16. THE Deploy_Script SHALL accept an optional --key-pair-name argument specifying an existing EC2 key pair name
17. IF --enable-ssh is provided without --key-pair-name, THEN THE Deploy_Script SHALL fail with an error indicating that --key-pair-name is required when SSH is enabled
18. IF --enable-ssh is not provided, THEN THE Deploy_Terraform SHALL NOT attach any key pair to the Target_Instance
19. IF --enable-ssh is not provided, THEN THE Deploy_Terraform SHALL NOT allow inbound SSH traffic in the security group
20. WHEN --enable-ssh is provided, THE Deploy_Terraform SHALL accept an enable_ssh variable with default value false
21. WHEN --enable-ssh is provided, THE Deploy_Terraform SHALL accept a key_pair_name variable with default value ""
22. WHEN enable_ssh is true, THE Deploy_Terraform SHALL add an inbound security group rule allowing TCP port 22 from the allowed_ssh_cidr variable
23. WHEN enable_ssh is true, THE Deploy_Terraform SHALL attach the EC2 key pair specified by key_pair_name to the Target_Instance
24. WHEN enable_ssh is false, THE security group SHALL NOT contain any inbound rule for port 22
25. WHEN enable_ssh is false, THE Target_Instance SHALL NOT have a key_name attribute set
26. THE Deploy_Script SHALL pass enable_ssh and key_pair_name as Terraform variables via -var flags when --enable-ssh is provided
27. WHEN --enable-ssh is provided, THE Deploy_Script SHALL detect the user's public IP address by querying https://checkip.amazonaws.com with a 5 second timeout and pass allowed_ssh_cidr as {detected_ip}/32 via -var flag
28. WHEN --enable-ssh is provided, THE Deploy_Script SHALL log a warning that SSH debug access is enabled and the instance is accessible on port 22
29. THE Deploy_Script SHALL include the ssh_enabled status in the Infrastructure_State JSON output

## Security Hardening Requirements

### Requirement 45: Encrypted Request Anti-Replay Protection

**User Story:** As a security engineer, I want encrypted requests protected against replay attacks, so that captured valid requests cannot be replayed to cause duplicate executions

**Security Finding:** #9 (High)

#### Acceptance Criteria

1. THE GHA_Server SHALL maintain a nonce cache that tracks recently seen nonces from encrypted requests
2. WHEN the GHA_Server receives an encrypted /execute request, THE GHA_Server SHALL extract the nonce from the decrypted payload and check it against the nonce cache
3. IF the nonce has been previously seen in the cache, THEN THE GHA_Server SHALL reject the request with HTTP 400 Bad Request and an error message indicating a duplicate nonce
4. THE nonce cache entries SHALL expire after a configurable TTL that matches the OIDC_Token lifetime
5. THE GHA_Server SHALL apply the same nonce validation to /execution/{execution_id}/output requests

### Requirement 46: Debug Image Annotation and Production Gate

**User Story:** As a security engineer, I want SSH-enabled debug images annotated with a machine-readable marker and production AMI builds gated against debug artifacts, so that debug images cannot be accidentally deployed to production

**Security Finding:** #14 (Medium)

#### Acceptance Criteria

1. WHEN the Build_Workflow builds with --enable-ssh, THE Artifact_Publisher SHALL add a machine-readable annotation `debug=true` to the ORAS artifact push
2. WHEN the Build_Workflow builds without --enable-ssh, THE Artifact_Publisher SHALL add a machine-readable annotation `debug=false` to the ORAS artifact push
3. WHEN the AMI_Converter downloads an artifact, THE AMI_Converter SHALL check for the `debug` annotation on the artifact
4. IF the artifact has `debug=true` annotation, THEN THE AMI_Converter SHALL refuse to build the AMI and terminate with an error unless an explicit `--allow-debug` CLI flag is provided
5. IF --allow-debug is provided and the artifact has `debug=true`, THEN THE AMI_Converter SHALL log a prominent warning that a debug image is being converted to an AMI

### Requirement 47: Artifact Provenance Workflow Verification

**User Story:** As a security engineer, I want the AMI converter to optionally verify the producing workflow identity in the attestation, so that only artifacts from a specific trusted workflow are accepted

**Security Finding:** #17 (Medium)

#### Acceptance Criteria

1. THE AMI_Converter SHALL accept an optional --expected-workflow CLI argument specifying the expected workflow file path
2. WHEN --expected-workflow is provided, THE Signature_Verifier SHALL run `gh attestation verify` with `--format json` to produce machine-readable output, then extract the workflow identity from the certificate's SubjectAlternativeName (SAN) field in the JSON result
3. THE Signature_Verifier SHALL extract the SAN using `jq -r '.[0].verificationResult.signature.certificate.subjectAlternativeName' attestation_result.json`; the SAN is populated directly from GitHub's OIDC token and cannot be forged by the workflow that produced the attestation
4. IF the extracted SAN contains the expected workflow path as a substring, THE Signature_Verifier SHALL consider the workflow identity verified
5. IF the SAN does not contain the expected workflow path, THEN THE AMI_Converter SHALL terminate with an error indicating workflow mismatch
6. THE Signature_Verifier SHALL NOT set GH_FORCE_TTY when running `gh attestation verify --format json`, because ANSI escape codes injected by GH_FORCE_TTY break jq parsing of the JSON output
7. THE Signature_Verifier SHALL separately run `gh attestation verify` with GH_FORCE_TTY=1 (without --format json) to produce human-readable output for logging purposes
8. WHEN --expected-workflow is not provided, THE Signature_Verifier SHALL skip workflow identity verification

### Requirement 48: Docker Daemon Security Configuration

**User Story:** As a security engineer, I want the Docker daemon configured with explicit security hardening in the KIWI image, so that container isolation does not rely on ambient defaults

**Security Finding:** #19 (High)

#### Acceptance Criteria

1. THE KIWI image SHALL include a daemon.json configuration file for the Docker daemon at /etc/docker/daemon.json
2. THE daemon.json SHALL set `no-new-privileges` to true to prevent privilege escalation via setuid/setgid
3. THE daemon.json SHALL set `live-restore` to false to ensure containers stop when the daemon restarts
4. THE KIWI image build SHALL document the expected Docker daemon security configuration in code comments

### Requirement 49: Systemd Service Hardening

**User Story:** As a security engineer, I want the host executor systemd service hardened with security directives, so that a container breakout has reduced impact on the host

**Security Finding:** #22 (Medium)

#### Acceptance Criteria

1. THE systemd service unit for the github-actions-remote-executor SHALL set NoNewPrivileges to true
2. THE systemd service unit SHALL NOT set PrivateTmp to true, because the service creates temporary directories under TEMP_STORAGE_PATH and bind-mounts them into Docker containers via the Docker daemon; PrivateTmp would place those directories in a private namespace invisible to the Docker daemon, causing all container bind mounts to fail
3. THE systemd service unit SHALL set ProtectSystem to strict
4. THE systemd service unit SHALL set ProtectHome to true
5. THE systemd service unit SHALL set RestrictAddressFamilies to AF_INET AF_INET6 AF_UNIX AF_NETLINK
6. THE systemd service unit SHALL set StateDirectory to gha-executor so systemd creates and manages /var/lib/gha-executor
7. THE systemd service unit SHALL set LogsDirectory to github-actions-executor so systemd creates and manages /var/log/github-actions-executor
8. THE systemd service unit SHALL set ReadWritePaths to include /var/lib/gha-executor, /var/log/github-actions-executor, /var/run/docker.sock, and /tmp
9. THE TEMP_STORAGE_PATH configuration value SHALL be set to a path outside of /tmp (e.g. /var/lib/gha-executor) to avoid conflicts with PrivateTmp on other services and to ensure Docker bind mounts resolve correctly
10. THE env configuration file SHALL set TEMP_STORAGE_PATH to /var/lib/gha-executor

### Requirement 51: Host Login Access Hardening

**User Story:** As a security engineer, I want the root account locked and the serial console login prompt disabled in the KIWI image, so that no interactive login is possible even if a console or out-of-band access path were somehow reachable

#### Acceptance Criteria

1. THE config.sh script SHALL lock the root account unconditionally (regardless of ENABLE_SSH) by running `passwd -l root` during image creation, preventing password-based login via any console path
2. THE config.sh script SHALL mask the serial-getty@ttyS0.service unit unconditionally by running `systemctl mask serial-getty@ttyS0.service` during image creation, preventing a login prompt from being spawned on the serial console
3. THE serial console (console=ttyS0 in the kernel cmdline) SHALL remain active for read-only log output; masking the getty unit only prevents interactive login, not log streaming
4. WHEN SSH debug access is enabled (ENABLE_SSH=true), the root account lock and serial getty mask SHALL remain in effect; debug access is provided exclusively via the ec2-user account over SSH, which is unaffected by these controls

### Requirement 50: AMI Build IAM Permission Scoping

**User Story:** As a security engineer, I want the AMI build instance IAM permissions scoped to the specific region and account, so that compromise of the build instance does not grant account-wide image manipulation capability

**Security Finding:** #24 (High)

#### Acceptance Criteria

1. THE Terraform IAM policy for the Build_Instance SHALL scope EC2 and EBS permissions to the specific AWS region using resource ARN patterns
2. THE IAM policy SHALL use explicit resource ARN patterns for snapshots (`arn:aws:ec2:{region}::snapshot/*`), images (`arn:aws:ec2:{region}::image/*`), and volumes (`arn:aws:ec2:{region}:{account}:volume/*`) instead of a wildcard resource
3. THE IAM policy SHALL use `aws:RequestedRegion` condition key to restrict operations to the build region
4. THE IAM policy SHALL NOT use Resource = "*" for EC2 snapshot and image operations
