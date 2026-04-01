# Implementation Plan: GitHub Actions Remote Executor

## Overview

This implementation plan breaks down the GitHub Actions Remote Executor into discrete coding tasks. The system is an HTTP server running on an Attestable EC2 instance with NitroTPM that executes scripts from GitHub repositories with cryptographic attestation. The implementation follows an asynchronous execution model with polling-based output retrieval.

## Tasks

- [x] 1. Set up project structure and core configuration
  - Create Python project structure with src/ directory
  - Set up pyproject.toml with uv for remote executor dependencies (fastapi, uvicorn, requests, hypothesis, pytest, pytest-asyncio, httpx)
  - Set up scripts/pyproject.toml with uv for build/deployment script dependencies (boto3, paramiko)
  - Create configuration module for loading environment variables
  - Define ServerConfig dataclass with all configuration parameters
  - Implement configuration validation on startup
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 12.1, 12.2_

- [x] 1.1 Write property tests for configuration management
  - **Property 50: Configuration Loading**
  - **Property 51: Port Configuration**
  - **Property 52: Timeout Configuration**
  - **Property 53: Size Limit Configuration**
  - **Property 54: Rate Limit Configuration**
  - **Property 55: Storage Path Configuration**
  - **Property 56: Retention Period Configuration**
  - **Property 57: Missing Configuration Failure**
  - **Validates: Requirements 9.1-9.8**

- [x] 2. Implement data models and validation
  - [x] 2.1 Create core data model classes
    - Implement ExecutionRequest dataclass
    - Implement ExecutionRecord dataclass
    - Implement ExecutionStatus enum
    - Implement AttestationDocument dataclass
    - Implement OutputData dataclass
    - _Requirements: 2.1, 2.2, 2.3, 2.6_

  - [x] 2.2 Implement RequestValidator class
    - Write validate_execution_request method
    - Write validate_repository_url method (GitHub URL format)
    - Write validate_commit_hash method (40-char hex SHA)
    - Write validate_script_path method (non-empty, no path traversal)
    - Return descriptive validation errors
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6_

  - [x] 2.3 Write property tests for request validation
    - **Property 1: Valid Request Acceptance**
    - **Property 2: Malformed Request Rejection**
    - **Property 4: Required Field Validation**
    - **Property 5: Repository URL Format Validation**
    - **Property 6: Commit Hash Format Validation**
    - **Property 7: Validation Error Response**
    - **Validates: Requirements 1.3, 1.4, 2.1, 2.2, 2.3, 2.5, 2.6**

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement GitHub repository client
  - [x] 4.1 Create RepositoryClient class
    - Implement authenticate method using GitHub token
    - Implement fetch_file method using GitHub API
    - Handle GitHub API errors (401, 404, rate limits)
    - Map GitHub errors to appropriate HTTP status codes
    - Store fetched files in temporary secure location
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [x] 4.2 Write property tests for repository client
    - **Property 8: GitHub Authentication**
    - **Property 9: Exact Commit File Retrieval**
    - **Property 10: Authentication Failure Response**
    - **Property 11: Repository Not Found Response**
    - **Property 12: Commit Not Found Response**
    - **Property 13: File Not Found Response**
    - **Property 14: Temporary File Storage**
    - **Validates: Requirements 3.1-3.7**

  - [x] 4.3 Write unit tests for repository client
    - Test with mocked GitHub API responses
    - Test error handling for various GitHub API errors
    - Test file size validation
    - _Requirements: 3.1-3.7, 8.4_

- [x] 5. Implement AWS Nitro attestation generator
  - [x] 5.1 Create AttestationGenerator class
    - Implement verify_tpm_available method to check NitroTPM device at `/usr/bin/nitro-tpm-attest`
    - Implement generate_attestation method that:
      1. Accepts optional user_data and nonce parameters for inclusion in attestation
      2. Writes user_data and nonce to temporary files if provided (using tempfile.mkstemp)
      3. Invokes `/usr/bin/nitro-tpm-attest` with optional `--user-data` and `--nonce` flags
      4. Captures binary CBOR-encoded attestation document from stdout using subprocess.run
      5. Implements 30-second timeout for attestation generation
      6. Returns attestation document as bytes or detailed error information
      7. Cleans up temporary files in finally block
      8. Handles subprocess failures, timeouts, and OS errors
      9. Returns error responses with command, exit code, stdout, stderr, and context for debugging
    - Include repository URL, commit hash, script path, timestamp in user_data
    - Encode attestation in CBOR format (handled by nitro-tpm-attest)
    - Handle attestation generation failures with detailed error context
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.10_

  - [x] 5.2 Write property tests for attestation generator
    - **Property 15: Attestation Document Generation**
    - **Property 16: Attestation Document Completeness**
    - **Property 17: Attestation Document Signing**
    - **Property 20: Attestation Failure Response**
    - **Validates: Requirements 4.1-4.6, 4.10**

  - [x] 5.3 Write unit tests for attestation generator
    - Test with mocked NitroTPM device
    - Test attestation document structure
    - Test signature verification
    - _Requirements: 4.1-4.6_

- [x] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement execution management
  - [x] 7.1 Create ExecutionManager class
    - Implement create_execution method with UUID generation
    - Implement get_execution method for retrieval by ID
    - Implement update_status method for status transitions
    - Implement cleanup_expired method for retention policy
    - Use thread-safe data structure for execution storage
    - Track execution lifecycle: queued → running → (completed|failed|timed_out)
    - _Requirements: 4.7, 5.9, 6.10_

  - [x] 7.2 Write property tests for execution manager
    - **Property 18: Execution ID Uniqueness**
    - **Property 29: Execution Status Tracking**
    - **Property 36: Output Retention Period**
    - **Validates: Requirements 4.7, 5.9, 6.10**

  - [x] 7.3 Write unit tests for execution manager
    - Test concurrent access to execution store
    - Test status transition validation
    - Test cleanup of expired executions
    - _Requirements: 4.7, 5.9, 6.10_

- [x] 8. Implement output collection
  - [x] 8.1 Create OutputCollector class
    - Implement capture_output method for streaming output
    - Implement get_output method with offset support
    - Store stdout and stderr separately
    - Support incremental output retrieval
    - Implement thread-safe buffered writes
    - _Requirements: 5.3, 5.4, 6.3, 6.4, 6.5, 6.6_

  - [x] 8.2 Write property tests for output collector
    - **Property 23: Output Stream Capture**
    - **Property 24: Output Storage Round-Trip**
    - **Property 31: Output Structure Separation**
    - **Property 32: Offset-Based Output Retrieval**
    - **Validates: Requirements 5.3, 5.4, 6.3, 6.4, 6.5, 6.6**

  - [x] 8.3 Write unit tests for output collector
    - Test large output handling
    - Test concurrent output capture
    - Test offset edge cases (0, beyond end, negative)
    - _Requirements: 5.3, 5.4, 6.3, 6.4, 6.5, 6.6_

- [x] 9. Implement script executor
  - [x] 9.1 Create ScriptExecutor class
    - Implement execute_async method for background execution as root
    - Capture stdout and stderr streams
    - Implement execution timeout with process termination
    - Capture exit codes
    - Integrate with OutputCollector for stream capture
    - Update ExecutionManager status throughout lifecycle
    - Clean up temporary files after execution
    - _Requirements: 5.1, 5.2, 5.3, 5.5, 5.6, 5.7, 8.4_

  - [x] 9.2 Write property tests for script executor
    - **Property 21: Asynchronous Script Execution**
    - **Property 22: Process Isolation**
    - **Property 25: Execution Timeout Configuration**
    - **Property 26: Timeout Termination**
    - **Property 27: Exit Code Capture**
    - **Property 28: Temporary File Cleanup**
    - **Validates: Requirements 5.1, 5.2, 5.5, 5.6, 5.7, 8.4**

  - [x] 9.3 Write unit tests for script executor
    - Test script execution with known output
    - Test timeout scenarios
    - Test cleanup on success and failure
    - _Requirements: 5.1-5.7, 8.4_

- [x] 10. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement HTTP server and request handlers
  - [x] 11.1 Create HTTP server with Flask or FastAPI
    - Set up HTTP server listening on configured port
    - Implement request routing
    - Implement rate limiting middleware per source IP
    - Implement request logging middleware (exclude tokens)
    - Implement error handling middleware
    - _Requirements: 1.1, 1.2, 1.5, 7.2, 8.7_

  - [x] 11.2 Implement POST /execute endpoint
    - Parse request body into ExecutionRequest
    - Validate request using RequestValidator
    - Authenticate and fetch file using RepositoryClient
    - Validate script file size
    - Generate attestation using AttestationGenerator
    - Create execution record using ExecutionManager
    - Initiate async execution using ScriptExecutor
    - Return immediate response with execution_id and attestation_document
    - Handle all error cases with appropriate HTTP status codes
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 4.8, 4.9, 8.2, 8.3_

  - [x] 11.3 Implement GET /execution/{execution_id}/output endpoint
    - Parse execution_id from URL path
    - Parse optional offset query parameter
    - Retrieve execution record using ExecutionManager
    - Retrieve output using OutputCollector with offset
    - Return status, stdout, stderr, offsets, complete flag, exit_code
    - Handle non-existent execution IDs with 404
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9_

  - [x] 11.4 Write property tests for HTTP endpoints
    - **Property 3: Concurrent Request Handling**
    - **Property 19: Immediate Response with Attestation**
    - **Property 30: Output Endpoint Status Return**
    - **Property 33: Completion Exit Code Inclusion**
    - **Property 34: Completion Flag Accuracy**
    - **Property 35: Invalid Execution ID Response**
    - **Property 47: Script Size Validation**
    - **Property 48: Oversized Script Rejection**
    - **Property 49: Rate Limiting per IP**
    - **Validates: Requirements 1.3, 1.4, 1.5, 4.8, 4.9, 6.2, 6.7, 6.8, 6.9, 8.2, 8.3, 8.5**

  - [x] 11.5 Write unit tests for HTTP endpoints
    - Test complete request/response flow
    - Test error responses for each error case
    - Test rate limiting behavior
    - Test concurrent request handling
    - _Requirements: 1.1-1.5, 4.8-4.10, 6.1-6.9, 8.2, 8.3, 8.5_

- [x] 12. Implement health and metrics endpoints
  - [x] 12.1 Create GET /health endpoint
    - Return HTTP 200 when operational
    - Include attestation capability status
    - Include disk space availability
    - Include active executions count
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [x] 12.2 Create GET /metrics endpoint
    - Track total executions count
    - Track successful executions count
    - Track failed executions count
    - Track average execution duration
    - Include active executions count
    - _Requirements: 10.5, 10.6_

  - [x] 12.3 Write property tests for health and metrics
    - **Property 58: Health Check Attestation Status**
    - **Property 59: Health Check Disk Space**
    - **Property 60: Execution Metrics Tracking**
    - **Validates: Requirements 10.3, 10.4, 10.6**

  - [x] 12.4 Write unit tests for health and metrics
    - Test health endpoint response structure
    - Test metrics accuracy
    - Test metrics under concurrent executions
    - _Requirements: 10.1-10.6_

- [x] 13. Implement logging and error handling
  - [x] 13.1 Create logging infrastructure
    - Set up structured logging with timestamp and context
    - Implement log levels (ERROR, WARN, INFO, DEBUG)
    - Log all errors with request context
    - Log request details excluding tokens
    - Log execution lifecycle events
    - Log attestation generation events
    - Log request phase durations
    - Implement log rotation and retention
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.7_

  - [x] 13.2 Implement error response handling
    - Return HTTP 500 for unexpected errors
    - Ensure error messages don't expose internal details
    - Use consistent error response format
    - _Requirements: 7.5, 7.6_

  - [x] 13.3 Write property tests for logging and error handling
    - **Property 37: Error Logging with Context**
    - **Property 38: Request Logging without Token**
    - **Property 39: Execution Event Logging**
    - **Property 40: Attestation Event Logging**
    - **Property 41: Unexpected Error Response**
    - **Property 42: Error Response Security**
    - **Property 43: Request Phase Duration Logging**
    - **Validates: Requirements 7.1-7.7**

  - [x] 13.4 Write unit tests for logging
    - Test log output for various scenarios
    - Test token exclusion from logs
    - Test error message sanitization
    - _Requirements: 7.1-7.7_

- [x] 14. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Integration and wiring
  - [x] 15.1 Create main application entry point
    - Load configuration on startup
    - Verify NitroTPM device availability
    - Initialize all components
    - Start HTTP server
    - Handle graceful shutdown
    - _Requirements: 9.1, 9.8_

  - [x] 15.2 Wire all components together
    - Connect HTTP handlers to service layer
    - Connect service layer to execution layer
    - Connect execution layer to storage layer
    - Ensure proper dependency injection
    - _Requirements: All requirements_

  - [x] 15.3 Write integration tests
    - Test complete end-to-end execution flow
    - Test error scenarios (auth failure, timeout, not found)
    - Test concurrent execution handling
    - Test rate limiting enforcement
    - Test cleanup and retention policies
    - _Requirements: All requirements_

- [x] 16. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 17. Set up KIWI image build infrastructure
  - [x] 17.1 Create Dockerfile for KIWI builder
    - Specify exact versions of all build dependencies
    - Install KIWI NG and required tools
    - Configure build environment
    - _Requirements: 11.1, 11.2_

  - [x] 17.2 Create KIWI image description files
    - Define disk image configuration
    - Configure boot loader and partitions
    - Specify packages and system configuration
    - Include pyproject.toml and uv.lock in image description directory
    - _Requirements: 11.4_
  - [x] 17.2.1 Create KIWI configuration script (config.sh) for Python dependencies
    - Create config.sh script that executes during KIWI image preparation phase
    - Install Python dependencies from pre-downloaded wheels using pip3 install --no-index --find-links (fully offline, no network required)
    - Verify pre-downloaded wheels exist at /tmp/kiwi-build/wheels/
    - Verify critical packages are importable (fastapi, uvicorn, requests)
    - Log installation success for build audit trail
    - Fail KIWI build if dependency installation or verification fails
    - _Requirements: 12.17, 12.18, 12.19, 12.20, 12.21_

  - [x] 17.3 Create build script (.github/scripts/build-kiwi-image.sh)
    - Configure loop device setup on host
    - Ensure pyproject.toml and uv.lock are in KIWI image description directory
    - Extract dependency list dynamically from pyproject.toml using tomllib (no hardcoded package names)
    - Pre-download Python dependency wheels using pip3 download (network available in this phase)
    - Copy pre-downloaded wheels into KIWI image overlay at /tmp/kiwi-build/wheels/
    - Execute KIWI NG build in Docker container
    - Generate PCR measurements file (pcr_measurements.json)
    - Store outputs in build-output directory
    - Handle build failures with descriptive errors
    - _Requirements: 11.4, 11.5, 11.6, 11.7, 11.8, 12.16, 12.17, 12.18_

  - [x] 17.4 Write property tests for KIWI build
    - **Property 61: KIWI Build Reproducibility**
    - **Property 62: PCR Measurements Presence**
    - **Validates: Requirements 11.1, 11.2, 11.6, 11.7**

- [x] 18. Create GitHub Actions workflow for image build
  - [x] 18.1 Create .github/workflows/build-attestable-image.yml
    - Configure workflow triggers (push, workflow_dispatch)
    - Set up permissions for attestations and packages
    - Checkout repository with submodules
    - _Requirements: 11.3, 12.2, 13.1_

  - [x] 18.2 Implement KIWI build step in workflow
    - Execute build-kiwi-image.sh script
    - Upload build artifacts (raw image, PCR measurements)
    - Handle build failures
    - _Requirements: 11.1, 11.4, 11.6_

  - [x] 18.3 Implement artifact publishing step
    - Extract PCR4 and PCR7 from pcr_measurements.json
    - Generate artifact tag using branch name and timestamp
    - Authenticate to GHCR using GitHub token
    - Push raw disk image and PCR measurements using ORAS
    - Annotate artifact with pcr4 and pcr7 values
    - Output artifact digest
    - Handle missing/invalid PCR measurements
    - Handle ORAS push failures
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8_

  - [x] 18.4 Implement GitHub attestation step
    - Generate build provenance attestation
    - Sign attestation using Sigstore
    - Include artifact digest and repository identity
    - Push attestation to registry
    - Output attestation ID and URL
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

  - [x] 18.5 Generate workflow summary
    - Include artifact reference and digest
    - Include attestation verification instructions
    - Include PCR measurement values
    - _Requirements: 13.7_

  - [x] 18.6 Write property tests for artifact publishing
    - **Property 63: PCR Extraction Validation**
    - **Property 64: Artifact Annotation Completeness**
    - **Property 65: Artifact Tag Uniqueness**
    - **Property 66: Attestation Bundle Completeness**
    - **Validates: Requirements 12.1, 12.3, 12.5, 13.3, 13.4**

- [x] 19. Checkpoint - Ensure build workflow tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 20. Create AMI converter script structure
  - [x] 20.1 Create scripts/build-ami.py entry point
    - Implement command-line argument parsing (artifact-ref, output-file, region, instance-type)
    - Set up structured logging configuration
    - Implement main execution flow with error handling and finally block for cleanup
    - _Requirements: 14.1, 19.7, 19.8, 20.5_

  - [x] 20.2 Implement configuration and validation
    - Validate artifact reference format (ghcr.io/owner/repo:tag format)
    - Validate AWS region
    - Validate output file path
    - Detect user's public IP address using checkip.amazonaws.com
    - _Requirements: 14.2_

- [x] 21. Create Terraform infrastructure module
  - [x] 21.1 Create terraform/build-ami/ module structure
    - Define variables (region, allowed_ssh_cidr, instance_type with default c5.9xlarge)
    - Configure AWS provider with region variable
    - Create data sources for availability zones and Amazon Linux 2023 AMI
    - _Requirements: 14.1_

  - [x] 21.2 Define VPC and networking resources
    - Create VPC with CIDR 10.2.0.0/16, DNS hostnames and support enabled
    - Create public subnet with CIDR 10.2.1.0/24 in first availability zone
    - Create Internet Gateway attached to VPC
    - Create route table with 0.0.0.0/0 route to IGW
    - Create route table association with public subnet
    - _Requirements: 14.3, 14.4, 14.5_

  - [x] 21.3 Define security group with SSH access
    - Allow SSH (port 22) ingress only from allowed_ssh_cidr variable
    - Allow all outbound traffic (0.0.0.0/0)
    - Attach to VPC
    - _Requirements: 14.6_

  - [x] 21.4 Define SSH key pair generation
    - Generate 4096-bit RSA key pair using tls_private_key resource
    - Create aws_key_pair with public key
    - _Requirements: 14.7_

  - [x] 21.5 Define IAM role and instance profile
    - Create IAM role with EC2 assume role policy
    - Create IAM policy with EC2 snapshot/image and EBS direct API permissions
    - Create IAM instance profile linking role to instance
    - _Requirements: 14.10_

  - [x] 21.6 Define EC2 instance resource
    - Use Amazon Linux 2023 AMI (latest, x86_64, hvm)
    - Configure instance type from variable
    - Attach to public subnet with auto-assign public IP
    - Attach security group and IAM instance profile
    - Attach SSH key pair
    - Configure metadata options with IMDSv2 required (http_tokens = "required")
    - Configure root volume (30GB gp3, encrypted)
    - _Requirements: 14.1, 14.8, 14.9_

  - [x] 21.7 Define outputs
    - Output instance_id
    - Output instance_public_ip
    - Output ssh_private_key (sensitive)
    - Output vpc_id
    - Output security_group_id
    - _Requirements: 14.1_

  - [x] 21.8 Write property tests for infrastructure provisioning
    - **Property 69: SSH Access Configuration**
    - **Property 78: Terraform State Isolation**
    - **Validates: Requirements 14.3, 14.6**

- [x] 22. Implement build instance provisioning
  - [x] 22.1 Create provision_instance function
    - Initialize Terraform in terraform/build-ami working directory
    - Apply Terraform configuration with variables (region, instance_type, allowed_ssh_cidr)
    - Parse Terraform outputs JSON to extract instance_id, instance_public_ip, ssh_private_key
    - Save SSH private key to temporary file with 0600 permissions
    - Wait for instance to be running using EC2 waiter
    - Wait for status checks to pass using EC2 waiter
    - Handle provisioning failures with descriptive errors
    - _Requirements: 14.1, 14.11, 14.12, 14.13_

  - [x] 22.2 Create verify_ssh_connectivity function
    - Connect to instance using paramiko with ec2-user and SSH private key
    - Configure SSH keepalive with 30-second intervals
    - Set connection timeout to 10 seconds and banner timeout to 10 seconds
    - Retry SSH connection up to 10 times with 30-second delays
    - Return connected SSH client
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7_

  - [x] 22.3 Write property tests for instance provisioning
    - **Property 79: SSH Keepalive Maintenance**
    - **Validates: Requirements 15.4**

- [x] 23. Implement SSH command execution utility
  - [x] 23.1 Create execute_remote_command function
    - Accept ssh_client, command, and stream_output parameters
    - Set channel to non-blocking mode to prevent deadlock
    - Read stdout and stderr concurrently in 4096-byte chunks
    - Stream output to logger in real-time if stream_output=True
    - Read remaining output after command completes
    - Capture and return exit code, stdout, and stderr
    - Handle UTF-8 decoding errors with 'replace' mode
    - _Requirements: 16.1, 16.2, 16.11_

- [x] 24. Implement tool installation functions
  - [x] 24.1 Create install_system_dependencies function
    - Install git and gcc via dnf package manager
    - Stream installation output to logger
    - Verify installation exit code (must be 0)
    - Raise RuntimeError on installation failure
    - _Requirements: 16.1_

  - [x] 24.2 Create install_rust function
    - Download and execute rustup installer from sh.rustup.rs with -y flag
    - Installation path: /home/ec2-user/.cargo/bin/
    - Stream installation output to logger
    - Verify installation exit code (must be 0)
    - Raise RuntimeError on installation failure
    - _Requirements: 16.2_

  - [x] 24.3 Create install_oras function
    - Download ORAS CLI version 1.3.0 from GitHub releases (linux_amd64.tar.gz)
    - Extract to /tmp and move binary to /usr/local/bin/oras
    - Remove temporary tar.gz file
    - Verify installation by executing oras version command
    - Log version information
    - Raise RuntimeError on installation or verification failure
    - _Requirements: 16.3, 16.4, 16.8_

  - [x] 24.4 Create install_github_cli function
    - Add gh-cli.repo repository configuration via dnf config-manager
    - Install gh package via dnf
    - Verify installation by executing gh version command
    - Log version information
    - Raise RuntimeError on installation or verification failure
    - _Requirements: 16.5, 16.9_

  - [x] 24.5 Create install_coldsnap function
    - Clone coldsnap from https://github.com/awslabs/coldsnap.git
    - Build and install using cargo install --locked coldsnap
    - Installation path: /home/ec2-user/.cargo/bin/coldsnap
    - Verify installation by executing coldsnap --help command
    - Log help output confirmation
    - Raise RuntimeError on installation or verification failure
    - _Requirements: 16.6, 16.7, 16.10_

  - [x] 24.6 Create install_all_tools orchestration function
    - Execute installation functions in sequence: system deps, Rust, ORAS, GitHub CLI, coldsnap
    - Handle installation failures with descriptive errors
    - Log installation progress at INFO level
    - Terminate build immediately if any tool installation fails
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 16.9, 16.10, 16.11, 16.12_

  - [x] 24.7 Write property tests for tool installation
    - **Property 70: Tool Installation Verification**
    - **Validates: Requirements 16.8, 16.9, 16.10**

- [x] 25. Checkpoint - Ensure infrastructure and tool tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 26. Implement signature verification
  - [x] 26.1 Create verify_artifact_signature function
    - Extract repository owner and name from artifact reference (ghcr.io/owner/repo format)
    - Fetch artifact manifest using oras manifest fetch command
    - Calculate manifest digest using sha256sum
    - Download attestation bundle from GitHub API: https://api.github.com/repos/{owner}/{repo}/attestations/sha256:{digest}
    - Extract first attestation bundle using jq: .attestations[0].bundle > bundle.json
    - Verify attestation using gh attestation verify with -R flag for repository identity and -b bundle.json for offline mode
    - Set GH_FORCE_TTY=1 environment variable to force gh output
    - Log detailed verification results including stdout and stderr
    - Return verification status (True if exit code 0, False otherwise)
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8, 17.11_

  - [x] 26.2 Implement verification failure handling
    - Log detailed error message with security implications
    - Explain possible causes: not attested, signature mismatch, tampering
    - Raise RuntimeError with "SIGNATURE VERIFICATION FAILED" message
    - Terminate build immediately without creating AMI
    - Cleanup process will still execute via finally block
    - _Requirements: 17.9, 17.10, 17.12_

  - [x] 26.3 Implement verification success path
    - Log successful verification with checkmark
    - Proceed to artifact download
    - _Requirements: 17.9_

  - [x] 26.4 Write property tests for signature verification
    - **Property 67: Signature Verification Requirement**
    - **Property 68: Untrusted Artifact Rejection**
    - **Validates: Requirements 17.9, 17.10, 17.12**

- [x] 27. Implement artifact download and validation
  - [x] 27.1 Create pull_artifact_from_ghcr function
    - Create ~/artifacts directory on build instance using mkdir -p
    - Execute oras pull command in ~/artifacts directory with artifact reference
    - Stream ORAS pull output to logger
    - Verify exit code is 0
    - List downloaded files in ~/artifacts/build-output using ls -lh
    - Log all downloaded artifacts and their sizes
    - _Requirements: 18.1, 18.2, 18.3, 18.8, 18.9_

  - [x] 27.2 Create validate_artifact_files function
    - Verify raw disk image exists using ls ~/artifacts/build-output/\*.raw
    - Verify pcr_measurements.json exists using test -f command
    - Raise RuntimeError with descriptive error if files missing
    - _Requirements: 18.4, 18.5, 18.10, 18.11_

  - [x] 27.3 Create validate_pcr_measurements function
    - Read pcr_measurements.json content using cat command
    - Parse JSON in Python script
    - Extract PCR4 from Measurements.PCR4 field
    - Extract PCR7 from Measurements.PCR7 field
    - Validate PCR values are non-empty hex strings
    - Return PCRMeasurements dataclass with pcr4 and pcr7
    - Raise RuntimeError if parsing fails or values invalid
    - _Requirements: 18.6, 18.7, 18.12_

  - [x] 27.4 Write property tests for artifact download
    - **Property 71: Artifact Download Completeness**
    - **Property 72: PCR Measurements Round-Trip**
    - **Validates: Requirements 18.4, 18.5, 18.7**

- [x] 28. Implement snapshot upload and AMI registration
  - [x] 28.1 Create upload_snapshot function
    - Find raw disk image filename in ~/artifacts/build-output using ls \*.raw
    - Execute /home/ec2-user/.cargo/bin/coldsnap upload with full path to raw image
    - Stream coldsnap output to logger in real-time during upload
    - Parse snapshot ID from coldsnap stdout (search for "snap-" prefix in all lines)
    - Fallback: check last line if snapshot ID not found in output
    - Validate snapshot ID format (must start with "snap-")
    - Raise RuntimeError if snapshot ID cannot be parsed or upload fails
    - _Requirements: 19.1, 19.2, 19.3, 19.4_

  - [x] 28.2 Create wait_for_snapshot function
    - Create EC2 waiter for snapshot_completed
    - Configure waiter with 15-second delay and 40 max attempts (up to 10 minutes)
    - Wait for snapshot to complete
    - Log progress during wait
    - Raise WaiterError if timeout exceeded or snapshot enters error state
    - _Requirements: 19.5, 19.6_

  - [x] 28.3 Create register_ami function
    - Generate AMI name with format: attestable-ami-imported-{architecture}-{timestamp}
    - Use strftime('%Y-%m-%dT%H-%M-%S') with UTC timezone to produce AWS-valid AMI names (no colons or '+' characters)
    - Register AMI with boto3 ec2_client.register_image with parameters:
      - VirtualizationType: hvm
      - BootMode: uefi
      - Architecture: x86_64
      - RootDeviceName: /dev/xvda
      - BlockDeviceMappings: single device /dev/xvda with snapshot ID
      - TpmSupport: v2.0
      - EnaSupport: True
    - Extract AMI ID from response ImageId field
    - Log AMI registration success with AMI ID
    - Raise ClientError on registration failure
    - _Requirements: 19.7, 19.8, 19.9, 19.10, 19.11, 19.12, 19.13, 19.14, 19.15, 19.16, 19.17_

  - [x] 28.4 Write property tests for snapshot and AMI
    - **Property 73: Snapshot Upload Success**
    - **Property 74: AMI Registration Configuration**
    - **Property 80: Coldsnap Output Streaming**
    - **Validates: Requirements 19.4, 19.7, 19.8, 19.9, 19.10, 19.11**

- [x] 29. Implement build result output and cleanup
  - [x] 29.1 Create generate_build_result function
    - Create build result dictionary with keys: ami_id, snapshot_id, region, build_timestamp, pcr_measurements
    - Extract PCR4 from pcr_measurements.json Measurements.PCR4 field
    - Extract PCR7 from pcr_measurements.json Measurements.PCR7 field
    - Format build_timestamp using datetime.now(timezone.utc).isoformat()
    - Write JSON to output file specified by --output-file argument
    - Format JSON with 2-space indentation using json.dump(indent=2)
    - Log complete build result at INFO level
    - _Requirements: 20.1, 20.2, 20.3, 20.4, 20.5, 20.6_

  - [x] 29.2 Create cleanup_infrastructure function
    - Close SSH client connection if open (ssh_client.close())
    - Execute terraform destroy -auto-approve in terraform/build-ami directory
    - Pass same variables as terraform apply: region, instance_type, allowed_ssh_cidr
    - Capture stdout and stderr from terraform destroy
    - Log "Infrastructure destroyed successfully" on success
    - Log terraform destroy errors but do not raise exceptions
    - Delete temporary SSH key file using os.unlink if it exists
    - Ignore SSH key deletion errors (silent failure)
    - _Requirements: 20.7, 20.8, 20.9, 20.10, 20.11, 20.13, 20.14_

  - [x] 29.3 Implement cleanup guarantee in main flow
    - Wrap entire build process in try/except/finally block
    - Catch all exceptions in except block, log error, set exit code to 1
    - Execute cleanup_infrastructure in finally block
    - Ensure cleanup runs even if build fails at any stage
    - Log "Cleaning up infrastructure..." at WARNING level before cleanup
    - _Requirements: 20.12_

  - [x] 29.4 Write property tests for build result and cleanup
    - **Property 75: Build Result Completeness**
    - **Property 76: Infrastructure Cleanup Guarantee**
    - **Property 77: Build Failure Cleanup**
    - **Validates: Requirements 20.1, 20.2, 20.3, 20.4, 20.5, 20.12**

  - [x] 29.5 Write integration tests for complete AMI build flow
    - Test complete build flow with mocked external services
    - Test signature verification failure handling
    - Test tool installation failures
    - Test concurrent build isolation
    - Test cleanup on various failure scenarios
    - _Requirements: 11-20_

- [x] 30. Add README section documenting how to build the AMI
  - [x] 30.1 Add "Building the AMI" section to README.md after the "AWS Nitro EC2 Deployment" section
    - Document the two-phase build process: (1) build the KIWI image via GitHub Actions, (2) convert to AMI via build-ami.py
    - Document prerequisites: AWS credentials configured, Terraform installed, Python with boto3 and paramiko (scripts/pyproject.toml), ORAS CLI
    - Document Step 1: Triggering the GitHub Actions workflow (push to main/develop or manual workflow_dispatch) to build and publish the KIWI image to GHCR
    - Document how to find the artifact reference from the workflow run output (GHCR artifact tag and digest)
    - Document Step 2: Running `uv run --project scripts python scripts/build-ami.py` with CLI arguments:
      - `--artifact-ref` (required): GHCR artifact reference (e.g., ghcr.io/owner/repo:tag)
      - `--region` (optional, default: us-east-1): AWS region for AMI creation
      - `--instance-type` (optional, default: c5.9xlarge): EC2 instance type for the build instance
      - `--output-file` (optional, default: ami_build_result.json): Path for the JSON build result output
    - Document what the script does: provisions temporary EC2 via Terraform, verifies artifact signature, downloads artifact, uploads snapshot via coldsnap, registers AMI, cleans up infrastructure
    - Document the output: ami_build_result.json containing ami_id, snapshot_id, region, build_timestamp, and pcr_measurements
    - _Requirements: 11.1, 11.4, 14.1, 15.1, 17.1, 19.1, 20.1, 20.5_

- [x] 31. Final checkpoint - Ensure all build tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 32. Create deployment Terraform module
  - [x] 32.1 Create terraform/deploy/main.tf with deployment infrastructure
    - Configure AWS provider with region variable and required_providers (hashicorp/aws ~> 5.0)
    - Create VPC with CIDR 10.0.0.0/16, DNS hostnames and DNS support enabled
    - Create public subnet with CIDR 10.0.1.0/24 in first availability zone, map public IP on launch
    - Create Internet Gateway attached to VPC
    - Create route table with default route (0.0.0.0/0) through IGW
    - Associate route table with public subnet
    - Create security group allowing inbound TCP on port 8080 only from allowed_http_cidr (no SSH, no other ports)
    - Allow all outbound traffic
    - Launch EC2 instance from attestable_ami_id with instance_type variable
    - Place instance in public subnet with public IP, attach security group
    - Enable detailed monitoring
    - Configure IMDSv2 required (http_tokens = "required", http_put_response_hop_limit = 1)
    - Tag all resources with "github-runner-ec2-attestation" prefix
    - _Requirements: 22.1, 22.2, 22.3, 22.4, 22.5, 22.6, 22.7, 23.1, 23.2, 23.3, 23.4, 23.5, 23.6, 24.1, 24.2, 24.3, 24.4, 24.5, 24.6, 24.7, 24.8, 24.9, 24.10_

  - [x] 32.2 Create terraform/deploy/variables.tf with deployment variables
    - Define attestable_ami_id (string, required, no default)
    - Define instance_type (string, default "c5.9xlarge")
    - Define allowed_http_cidr (string, required, no default)
    - Define aws_region (string, default "us-east-1")
    - _Requirements: 23.6, 24.1, 24.2, 24.3, 24.9, 24.10_

  - [x] 32.3 Create terraform/deploy/outputs.tf with deployment outputs
    - Output vpc_id of the created VPC
    - Output subnet_id of the created public subnet
    - Output security_group_id of the created security group
    - Output instance_id of the launched EC2 instance
    - Output instance_public_ip of the launched EC2 instance
    - Output attestation_api_url constructed as http://{instance_public_ip}:8080
    - _Requirements: 25.1, 25.2, 25.3, 25.4, 25.5, 25.6_

  - [x] 32.4 Write property tests for deployment infrastructure
    - **Property 81: Deployment VPC Isolation**
    - **Property 82: Security Group HTTP-Only Access**
    - **Property 83: IMDSv2 Enforcement**
    - **Validates: Requirements 22.1, 23.2, 23.4, 23.5, 24.7, 24.8**

- [x] 33. Create deployment script (scripts/deploy.py)
  - [x] 33.1 Implement CLI argument parsing with parse_arguments()
    - Accept --ami-build-result (default: ami_build_result.json)
    - Accept --instance-type (default: c5.9xlarge)
    - Accept --output-file (default: infrastructure_state.json)
    - _Requirements: 26.1, 26.2, 26.3_

  - [x] 33.2 Implement get_user_public_ip() function
    - Query https://checkip.amazonaws.com with 5-second timeout
    - Return stripped IP address string
    - _Requirements: 26.7_

  - [x] 33.3 Implement terraform_init() function
    - Run terraform init in terraform/deploy directory
    - Raise FileNotFoundError if directory missing
    - Raise RuntimeError on non-zero exit code
    - Log stdout/stderr output
    - _Requirements: 27.1, 27.2, 27.3_

  - [x] 33.4 Implement terraform_apply() function
    - Run terraform apply -auto-approve with -var flags for attestable_ami_id, instance_type, allowed_http_cidr, aws_region
    - Raise RuntimeError on non-zero exit code
    - Retrieve outputs via terraform output -json
    - Parse and return raw JSON outputs
    - Log Terraform variable values and command output
    - _Requirements: 27.4, 27.5, 27.6, 27.10_

  - [x] 33.5 Implement load_terraform_output() function
    - Extract value field from each raw Terraform output entry
    - Return dict with extracted values
    - _Requirements: 27.7_

  - [x] 33.6 Implement main() function orchestrating the full deployment flow
    - Load AMI build result JSON (fail with FileNotFoundError if missing, RuntimeError if unparseable)
    - Detect public IP and construct {ip}/32 CIDR
    - Run terraform_init and terraform_apply
    - Extract values via load_terraform_output
    - Write infrastructure state to output file as JSON with 2-space indentation
    - Log all operations and final infrastructure state summary
    - On failure, log advice to run terraform destroy for cleanup
    - _Requirements: 26.4, 26.5, 26.6, 26.8, 27.8, 27.9, 27.10, 27.11_

  - [x] 33.7 Write property tests for deployment script
    - **Property 84: Infrastructure State Persistence**
    - **Property 85: Deployment IP Auto-Detection**
    - **Property 86: AMI Build Result Loading**
    - **Validates: Requirements 25.1-25.6, 26.5, 26.7, 26.8, 27.7, 27.8**

- [x] 34. Checkpoint - Ensure all deployment tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 35. Add README section documenting how to deploy
  - Document running deploy.py with CLI arguments (--ami-build-result, --instance-type, --output-file)
  - Document prerequisites: AMI build result file (ami_build_result.json), Terraform installed, AWS credentials configured, Python with boto3 (scripts/pyproject.toml)
  - Document the deployment flow: load AMI result → detect IP → terraform init → apply → save state
  - Document the output: infrastructure_state.json containing vpc_id, subnet_id, security_group_id, instance_id, instance_public_ip, attestation_api_url
  - _Requirements: 22, 23, 24, 25, 26, 27_

- [x] 36. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 37. Test cleanup script (scripts/cleanup.py)
  - [x] 37.1 Write property tests for cleanup CLI argument parsing
    - **Property 87: Cleanup CLI Argument Parsing**
    - Test that invoking parse_arguments() with no args returns defaults (ami_build_result.json, terraform/deploy)
    - Test that providing custom --ami-build-result and --terraform-dir values are correctly parsed
    - **Validates: Requirements 28.1, 28.2**

  - [x] 37.2 Write property tests for cleanup build result loading
    - **Property 88: Cleanup Build Result Loading**
    - Test that any valid JSON file containing ami_id, snapshot_id, and region fields is correctly parsed
    - Test that missing file raises FileNotFoundError, invalid JSON raises RuntimeError
    - **Validates: Requirements 28.4**

  - [x] 37.3 Write property tests for cleanup user cancellation
    - **Property 89: Cleanup User Cancellation**
    - Test that any user input not equal to "yes" or "y" (case-insensitive) results in exit code 0 without resource deletion
    - **Validates: Requirements 28.8, 28.9**

  - [x] 37.4 Write property tests for Terraform subprocess error propagation
    - **Property 90: Terraform Subprocess Error Propagation**
    - Test that any non-zero exit code from terraform init or terraform destroy causes destroy_infrastructure to raise RuntimeError
    - **Validates: Requirements 29.4, 29.6**

  - [x] 37.5 Write property tests for post-destroy state verification
    - **Property 91: Post-Destroy State Verification**
    - Test that empty resources array in Terraform state logs success; non-empty resources array logs warning
    - **Validates: Requirements 29.7, 29.8**

  - [x] 37.6 Write property tests for AMI deregistration verification
    - **Property 92: AMI Deregistration Verification**
    - Test that after deregister_image with DeleteAssociatedSnapshots=True, both AMI and snapshot deletion are verified via describe_images and describe_snapshots
    - Test that AMI not found (InvalidAMIID.NotFound) is handled gracefully with a warning
    - **Validates: Requirements 30.2, 30.4, 30.5, 30.6**

  - [x] 37.7 Write property tests for cleanup resource verification and reporting
    - **Property 93: Cleanup Resource Verification and Reporting**
    - Test that verify_cleanup reports each remaining resource's type, ID, and status
    - Test that when no resources remain, it logs that all resources are removed
    - **Validates: Requirements 31.1, 31.2, 31.3, 31.4, 31.5, 31.6**

  - [x] 37.8 Write property tests for cleanup exit code correctness
    - **Property 94: Cleanup Exit Code Correctness**
    - Test that main() returns 0 when all steps succeed
    - Test that main() returns 1 when any step raises an exception
    - **Validates: Requirements 31.7, 31.8**

  - [x] 37.9 Write unit tests for cleanup script functions
    - Test parse_arguments with no args (defaults), custom args, both custom
    - Test build result loading: missing file, empty file, invalid JSON, missing fields
    - Test user confirmation: "yes", "y", "Yes", "Y", "no", "n", "", "maybe"
    - Test destroy_infrastructure: missing directory, missing state, init failure, destroy failure, successful destroy
    - Test deregister_ami: AMI exists and deregisters, AMI not found, API error
    - Test verify_cleanup: no remaining resources, EC2 instances found, AMI found, snapshot found, mixed resources
    - Test main exit codes: full success (0), exception during Terraform (1), user cancellation (0)
    - _Requirements: 28.1-28.9, 29.1-29.8, 30.1-30.7, 31.1-31.8_

- [x] 38. Final checkpoint - Ensure all cleanup tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 39. Implement build-time SSH debug support in build-kiwi-image.sh
  - [x] 39.1 Add --enable-ssh flag parsing to build-kiwi-image.sh
    - Add `ENABLE_SSH="false"` default and a `while` loop to parse `--enable-ssh` flag (exit on unknown args)
    - Place argument parsing at the top of the script before any other logic
    - _Requirements: 32.7_

  - [x] 39.2 Add sed-based removal of SSH ignore directives when --enable-ssh is passed
    - When `ENABLE_SSH="true"`, use `sed -i` to remove `<ignore name="openssh-server"/>`, `<ignore name="cloud-init"/>`, `<ignore name="cloud-init-cfg-ec2"/>`, and `<ignore name="ec2-instance-connect"/>` from the copied `appliance.kiwi` in `${TEMP_IMAGE_DIR}`
    - Place this block after the image description files are copied to `TEMP_IMAGE_DIR` but before the Docker build step
    - Do NOT remove `amazon-ssm-agent` or `update-motd` ignore directives
    - _Requirements: 32.8, 32.9, 32.14_

  - [x] 39.3 Pass ENABLE_SSH environment variable to the Docker container
    - Add `-e "ENABLE_SSH=${ENABLE_SSH}"` to the existing `docker run` command that runs the KIWI build
    - _Requirements: 32.12_

- [x] 40. Implement conditional sshd enablement in config.sh
  - [x] 40.1 Add conditional sshd enablement block to config.sh
    - Add a block that reads the `ENABLE_SSH` environment variable
    - When `ENABLE_SSH` equals `"true"`, run `systemctl enable sshd` and log success
    - When `ENABLE_SSH` is unset, empty, or any other value, log that SSH is disabled (default secure behavior)
    - Place this block after the existing `systemctl enable github-actions-remote-executor.service` line and before the Python dependency installation section
    - _Requirements: 32.10, 32.11, 32.12, 32.13_

- [x] 41. Update GitHub Actions workflow for SSH debug support
  - [x] 41.1 Add workflow_dispatch enable_ssh input to build-attestable-image.yml
    - Add `enable_ssh` input under the existing `workflow_dispatch` trigger with type `boolean`, default `false`, and description indicating it enables SSH debug access (NOT for production)
    - _Requirements: 32.2_

  - [x] 41.2 Conditionally pass --enable-ssh flag in the Build KIWI image step
    - Modify the "Build KIWI image" step to construct an `SSH_FLAG` variable: set to `--enable-ssh` only when `github.event_name == 'workflow_dispatch'` AND `inputs.enable_ssh == 'true'`; otherwise empty
    - Pass `$SSH_FLAG` as an argument to `build-kiwi-image.sh`
    - _Requirements: 32.1, 32.3, 32.4_

  - [x] 41.3 Add SSH debug warning step to workflow
    - Add a new step "SSH debug warning" that runs only when `github.event_name == 'workflow_dispatch' && inputs.enable_ssh == true`
    - Append a prominent blockquote warning to `$GITHUB_STEP_SUMMARY` indicating the image was built with SSH debug access and is NOT for production use
    - _Requirements: 32.5, 32.6_

- [x] 42. Checkpoint - Ensure build-time SSH changes are consistent
  - Ensure all tests pass, ask the user if questions arise.

- [x] 43. Add Terraform variables and conditional SSH infrastructure
  - [x] 43.1 Add enable_ssh and key_pair_name variables to terraform/deploy/variables.tf
    - Add `enable_ssh` variable of type `bool` with default `false` and description "Enable SSH debug access (NOT for production)"
    - Add `key_pair_name` variable of type `string` with default `""` and description "EC2 key pair name for SSH access"
    - _Requirements: 32.20, 32.21_

  - [x] 43.2 Add conditional SSH ingress rule to security group in terraform/deploy/main.tf
    - Add a `dynamic "ingress"` block inside `aws_security_group.attestation_api` that creates a TCP port 22 rule from `var.allowed_http_cidr` only when `var.enable_ssh` is `true`
    - When `enable_ssh` is `false`, no port 22 rule should exist
    - _Requirements: 32.22, 32.24_

  - [x] 43.3 Conditionally attach key pair to EC2 instance in terraform/deploy/main.tf
    - Set `key_name = var.enable_ssh ? var.key_pair_name : null` on the `aws_instance.target` resource
    - When `enable_ssh` is `false`, `key_name` is `null` (no key pair attached)
    - _Requirements: 32.23, 32.25_

- [x] 44. Update deploy.py for SSH debug support
  - [x] 44.1 Add --enable-ssh and --key-pair-name CLI arguments to deploy.py
    - Add `--enable-ssh` as a `store_true` flag defaulting to `False` with help text "Enable SSH debug access (requires --key-pair-name)"
    - Add `--key-pair-name` as a `str` argument defaulting to `''` with help text "EC2 key pair name for SSH access (required when --enable-ssh is set)"
    - _Requirements: 32.15, 32.16_

  - [x] 44.2 Add validation that --key-pair-name is required when --enable-ssh is set
    - In `main()`, after parsing arguments, check if `args.enable_ssh` is `True` and `args.key_pair_name` is empty — if so, log an error and return exit code 1
    - _Requirements: 32.17_

  - [x] 44.3 Pass enable_ssh and key_pair_name as Terraform variables
    - When `args.enable_ssh` is `True`, add `enable_ssh: 'true'` and `key_pair_name: args.key_pair_name` to the `tf_vars` dict before calling `terraform_apply`
    - When `args.enable_ssh` is `False`, do not add these variables (Terraform defaults apply)
    - _Requirements: 32.18, 32.19, 32.26_

  - [x] 44.4 Log SSH warning and include ssh_enabled in infrastructure state
    - When `args.enable_ssh` is `True`, log a warning: "⚠️  SSH debug access is enabled. The instance will be accessible on port 22."
    - After loading Terraform output, set `terraform_output['ssh_enabled'] = args.enable_ssh` before writing the infrastructure state JSON
    - _Requirements: 32.27, 32.28_

- [x] 45. Checkpoint - Ensure deploy-time SSH changes are consistent
  - Ensure all tests pass, ask the user if questions arise.

- [x] 46. Write property and unit tests for debug SSH feature
  - [x] 46.1 Write property test for build flag propagation
    - **Property 95: Build Flag Propagation**
    - Test that --enable-ssh is passed to build script if and only if event is workflow_dispatch with enable_ssh=true; never passed for push, pull_request, or schedule triggers
    - **Validates: Requirements 32.1, 32.3, 32.4**

  - [x] 46.2 Write property test for KIWI XML SSH directive modification
    - **Property 96: KIWI XML SSH Directive Modification**
    - Test that when --enable-ssh is passed, the four SSH ignore directives (openssh-server, cloud-init, cloud-init-cfg-ec2, ec2-instance-connect) are removed from appliance.kiwi; when not passed, all four remain
    - **Validates: Requirements 32.8, 32.9**

  - [x] 46.3 Write property test for conditional sshd enablement
    - **Property 97: Conditional sshd Enablement**
    - Test that sshd is enabled if and only if ENABLE_SSH equals "true"; for all other values (empty, unset, "false", arbitrary strings) sshd is not enabled
    - **Validates: Requirements 32.10, 32.11, 32.12, 32.13**

  - [x] 46.4 Write property test for GHA summary SSH warning
    - **Property 98: GHA Summary SSH Warning**
    - Test that the job summary contains an SSH warning if and only if the trigger is workflow_dispatch with enable_ssh=true
    - **Validates: Requirements 32.5**

  - [x] 46.5 Write property test for deploy script SSH argument validation
    - **Property 99: Deploy Script SSH Argument Validation**
    - Test that --enable-ssh without --key-pair-name fails with error; --enable-ssh with --key-pair-name proceeds; no --enable-ssh proceeds regardless of --key-pair-name
    - **Validates: Requirements 32.15, 32.16, 32.17**

  - [x] 46.6 Write property test for Terraform SSH configuration consistency
    - **Property 100: Terraform SSH Configuration Consistency**
    - Test that port 22 ingress rule exists if and only if enable_ssh=true; key_name is set if and only if enable_ssh=true
    - **Validates: Requirements 32.18, 32.19, 32.22, 32.23, 32.24, 32.25**

  - [x] 46.7 Write property test for deploy script SSH Terraform variable passing
    - **Property 101: Deploy Script SSH Terraform Variable Passing**
    - Test that when --enable-ssh and --key-pair-name are provided, tf_vars includes enable_ssh=true and key_pair_name={name}; when --enable-ssh is not provided, these variables are absent
    - **Validates: Requirements 32.26**

  - [x] 46.8 Write property test for infrastructure state SSH status
    - **Property 102: Infrastructure State SSH Status**
    - Test that infrastructure state JSON includes ssh_enabled=true when --enable-ssh is provided and ssh_enabled=false otherwise
    - **Validates: Requirements 32.28**

  - [x] 46.9 Write property test for deploy script SSH warning
    - **Property 103: Deploy Script SSH Warning**
    - Test that a warning about SSH debug access is logged when --enable-ssh is provided; no such warning when --enable-ssh is not provided
    - **Validates: Requirements 32.27**

  - [x] 46.10 Write unit tests for debug SSH feature
    - Test build script --enable-ssh flag parsing: no args (default), --enable-ssh, unknown arg (error)
    - Test sed removal of specific ignore directives from sample appliance.kiwi XML, verify other directives preserved
    - Test config.sh conditional sshd enablement: ENABLE_SSH=true (enable), ENABLE_SSH=false (skip), unset (skip)
    - Test deploy.py --enable-ssh and --key-pair-name argument parsing: no SSH args (defaults), both provided, --enable-ssh without key pair (error)
    - Test Terraform variable construction: without SSH (4 vars), with SSH (6 vars)
    - Test infrastructure state output: ssh_enabled=true, ssh_enabled=false
    - _Requirements: 32.1-32.28_

- [x] 47. Final checkpoint - Ensure all debug SSH tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 48. Rename NSM references to NitroTPM in source code (src/)
  - [x] 48.1 Update src/attestation.py
    - Change module docstring from "AWS Nitro attestation document generation" to "NitroTPM attestation document generation"
    - Change class docstring from "Generates attestation documents using AWS Nitro Security Module" to "Generates attestation documents using NitroTPM on the Attestable EC2 instance"
    - Rename `__init__` parameter `nsm_device_path` to `tpm_device_path`
    - Rename `self.nsm_device_path` to `self.tpm_device_path`
    - Rename method `verify_nsm_available()` to `verify_tpm_available()`
    - Update method docstrings: "NSM device" → "NitroTPM device"
    - Update `generate_attestation` docstring: "AWS Nitro attestation" → "NitroTPM attestation"
    - Note: log messages referencing "nitro-tpm-attest" binary name should remain unchanged
    - _Requirements: 4.1, 4.2, 4.6_

  - [x] 48.2 Update src/config.py
    - Change comment "# AWS Nitro Configuration" to "# NitroTPM Configuration"
    - Rename field `nsm_device_path` to `tpm_device_path`
    - Change environment variable lookup from `NSM_DEVICE_PATH` to `TPM_DEVICE_PATH`
    - Update validation message from "nsm_device_path cannot be empty" to "tpm_device_path cannot be empty"
    - _Requirements: 9.1_

  - [x] 48.3 Update src/models.py
    - Change comment `# CBOR-encoded NSM attestation` to `# CBOR-encoded NitroTPM attestation`
    - _Requirements: 4.2_

  - [x] 48.4 Update src/main.py
    - Change log message "NSM device path" to "NitroTPM device path"
    - Change log message "Verifying NSM device availability" to "Verifying NitroTPM device availability"
    - Change `verify_nsm_available()` calls to `verify_tpm_available()`
    - Change `config.nsm_device_path` to `config.tpm_device_path`
    - Change log "NSM device not available" to "NitroTPM device not available"
    - Change log "NSM device verified and available" to "NitroTPM device verified and available"
    - _Requirements: 9.1, 10.3_

  - [x] 48.5 Update src/server.py
    - Change `config.nsm_device_path` to `config.tpm_device_path`
    - Change `verify_nsm_available()` calls to `verify_tpm_available()`
    - _Requirements: 10.3_

- [x] 49. Update configuration and environment files
  - [x] 49.1 Update .env.example
    - Change comment "# AWS Nitro Configuration" to "# NitroTPM Configuration"
    - Change `NSM_DEVICE_PATH=/dev/nsm` to `TPM_DEVICE_PATH=/dev/nsm`
    - _Requirements: 9.1_

  - [x] 49.2 Update kiwi-descriptions/root/etc/github-actions-remote-executor/env
    - Change comment "# AWS Nitro Configuration" to "# NitroTPM Configuration"
    - Change `NSM_DEVICE_PATH=/dev/nsm` to `TPM_DEVICE_PATH=/dev/nsm`
    - _Requirements: 9.1_

- [x] 50. Update tests to match renamed APIs
  - [x] 50.1 Update tests/test_attestation.py
    - Change fixture docstring "mocked NSM device path" to "mocked NitroTPM device path"
    - Change `nsm_device_path=` to `tpm_device_path=` in all AttestationGenerator constructor calls
    - Rename fixture `mock_nsm_device` to `mock_tpm_device`
    - Rename class `TestNSMAvailability` to `TestTPMAvailability`
    - Update all docstrings referencing "NSM" to "NitroTPM"
    - Change `verify_nsm_available()` calls to `verify_tpm_available()`
    - _Requirements: 4.1, 4.6_

  - [x] 50.2 Update tests/test_attestation_properties.py
    - Change comments "CBOR-encoded attestation from NSM" to "from NitroTPM"
    - Rename function `test_nsm_device_availability_check` to `test_tpm_device_availability_check`
    - Update docstrings: "verify_nsm_available" to "verify_tpm_available"
    - Change `verify_nsm_available()` calls to `verify_tpm_available()`
    - _Requirements: 4.1, 4.6_

  - [x] 50.3 Update tests/test_health_metrics_unit.py
    - Change `nsm_device_path=` to `tpm_device_path=` in all ServerConfig constructor calls
    - Change `verify_nsm_available` mock references to `verify_tpm_available`
    - _Requirements: 10.3_

  - [x] 50.4 Update tests/test_health_metrics_properties.py
    - Change `nsm_device_path=` to `tpm_device_path=` in all ServerConfig constructor calls
    - Change `verify_nsm_available` mock references to `verify_tpm_available`
    - _Requirements: 10.3_

  - [x] 50.5 Update tests/test_server_unit.py
    - Change `nsm_device_path=` to `tpm_device_path=` in all ServerConfig constructor calls
    - Update error message string "NSM device not available" to "NitroTPM device not available" if present in test assertions
    - _Requirements: 4.10, 10.3_

  - [x] 50.6 Update tests/test_integration.py
    - Change docstring "NSM device" to "NitroTPM device"
    - Change `nsm_device_path=` to `tpm_device_path=` in all ServerConfig constructor calls
    - _Requirements: 4.1_

  - [x] 50.7 Update tests/test_logging_error_handling_properties.py
    - Change `nsm_device_path=` to `tpm_device_path=` in all ServerConfig constructor calls
    - _Requirements: 7.1_

- [x] 51. Update README and documentation
  - [x] 51.1 Update README.md
    - Change "AWS Nitro-based EC2 instances" to "Attestable EC2 instance with NitroTPM" in overview
    - Change "AWS Nitro-based EC2 instance (for attestation capabilities)" to "Attestable EC2 instance with NitroTPM" in requirements
    - Change `NSM_DEVICE_PATH` to `TPM_DEVICE_PATH` in configuration section
    - Change "AWS Nitro Security Module device path" to "NitroTPM device path"
    - Update "AWS Nitro EC2 Deployment" section title to "Attestable EC2 Deployment"
    - Change "AWS Nitro-based EC2 instance for attestation" to "Attestable EC2 instance with NitroTPM"
    - Change "Nitro-based instances" to "NitroTPM-compatible instances"
    - _Requirements: 4.6, 9.1_

- [x] 52. Update tasks.md overview and notes sections
  - [x] 52.1 Update tasks.md self-references
    - Change overview "AWS Nitro-based EC2 instances" to "an Attestable EC2 instance with NitroTPM"
    - Update task 5 references: `verify_nsm_available` → `verify_tpm_available`, `nsm_device_path` → `tpm_device_path`, "NSM device" → "NitroTPM device"
    - Update task 15.1: "Verify NSM device availability" → "Verify NitroTPM device availability"
    - Update Notes section: "AWS Nitro attestation requires running on a Nitro-based EC2 instance" → "NitroTPM attestation requires running on an Attestable EC2 instance with NitroTPM"
    - _Requirements: 4.1, 4.6_

- [x] 53. Fix TPM config variable name and default value
  - [x] 53.1 Update src/config.py
    - Rename field `tpm_device_path` to `tpm_attest_path` in ServerConfig dataclass
    - Change environment variable lookup from `TPM_DEVICE_PATH` to `TPM_ATTEST_PATH`
    - Update validation message from "tpm_device_path cannot be empty" to "tpm_attest_path cannot be empty"
    - Update `from_env()` local variable and return field accordingly
    - _Requirements: 9.1, 9.7_

  - [x] 53.2 Update src/attestation.py
    - Rename `__init__` parameter `tpm_device_path` to `tpm_attest_path`
    - Rename `self.tpm_device_path` to `self.tpm_attest_path`
    - Update docstring from "Path to the nitro-tpm-attest command-line tool" to match new parameter name
    - Default value remains `/usr/bin/nitro-tpm-attest` (already correct)
    - _Requirements: 4.1, 4.6_

  - [x] 53.3 Update src/main.py
    - Change `config.tpm_device_path` to `config.tpm_attest_path` in AttestationGenerator constructor call
    - _Requirements: 9.1_

  - [x] 53.4 Update src/server.py
    - Change `config.tpm_device_path` to `config.tpm_attest_path` in AttestationGenerator constructor call
    - _Requirements: 10.3_

  - [x] 53.5 Update .env.example
    - Change `TPM_DEVICE_PATH=/dev/nsm` to `TPM_ATTEST_PATH=/usr/bin/nitro-tpm-attest`
    - _Requirements: 9.1_

  - [x] 53.6 Update kiwi-descriptions/root/etc/github-actions-remote-executor/env
    - Change `TPM_DEVICE_PATH=/dev/nsm` to `TPM_ATTEST_PATH=/usr/bin/nitro-tpm-attest`
    - _Requirements: 9.1_

  - [x] 53.7 Update README.md
    - Change `TPM_DEVICE_PATH` to `TPM_ATTEST_PATH` in configuration section
    - Change "NitroTPM device path (default: /dev/nsm)" to "NitroTPM attestation tool path (default: /usr/bin/nitro-tpm-attest)"
    - _Requirements: 9.1_

- [x] 54. Update tests for TPM config variable rename
  - [x] 54.1 Update tests/test_attestation.py
    - Change `tpm_device_path=` to `tpm_attest_path=` in all AttestationGenerator constructor calls
    - _Requirements: 4.1, 4.6_

  - [x] 54.2 Update tests/test_config_properties.py
    - Change `TPM_DEVICE_PATH` to `TPM_ATTEST_PATH` in all environment variable references
    - Change `tpm_device_path` to `tpm_attest_path` in all ServerConfig field assertions
    - Update default value assertions from `/dev/nsm` to `/usr/bin/nitro-tpm-attest` if present
    - _Requirements: 9.1_

  - [x] 54.3 Update tests/test_health_metrics_unit.py
    - Change `tpm_device_path=` to `tpm_attest_path=` in all ServerConfig constructor calls
    - _Requirements: 10.3_

  - [x] 54.4 Update tests/test_health_metrics_properties.py
    - Change `tpm_device_path=` to `tpm_attest_path=` in all ServerConfig constructor calls
    - _Requirements: 10.3_

  - [x] 54.5 Update tests/test_server_unit.py
    - Change `tpm_device_path=` to `tpm_attest_path=` in all ServerConfig constructor calls
    - _Requirements: 4.10, 10.3_

  - [x] 54.6 Update tests/test_integration.py
    - Change `tpm_device_path=` to `tpm_attest_path=` in all ServerConfig constructor calls
    - _Requirements: 4.1_

  - [x] 54.7 Update tests/test_logging_error_handling_properties.py
    - Change `tpm_device_path=` to `tpm_attest_path=` in all ServerConfig constructor calls
    - _Requirements: 7.1_

- [x] 55. Final checkpoint - Ensure all tests pass after TPM config fix
  - Ensure all tests pass, ask the user if questions arise.

- [x] 56. Implement Output_Attestation_Document generation
  - [x] 56.1 Add output attestation helper to AttestationGenerator
    - Create a `generate_output_attestation` method on `AttestationGenerator` that accepts the full Script_Output (stdout + stderr + exit code) as a string
    - Compute the SHA-256 digest of the Script_Output
    - Pass the hex-encoded digest as `user_data` to the existing `generate_attestation` flow (invoke `nitro-tpm-attest --user-data`)
    - Return the attestation document bytes on success, or `None` plus an error message on failure
    - _Requirements: 6.7, 6.9_

  - [x] 56.2 Update GET /execution/{id}/output endpoint to include Output_Attestation_Document
    - In `get_execution_output` in `src/server.py`, when `output_data.complete` is `True`:
      1. Concatenate stdout, stderr, and exit_code into a canonical Script_Output string
      2. Call `attestation_generator.generate_output_attestation(script_output)`
      3. If successful, base64-encode the attestation bytes and add `output_attestation_document` to the response
      4. If generation fails, set `output_attestation_document` to `null` and add `attestation_error` field with a failure message
    - When execution is not complete, omit `output_attestation_document` from the response
    - _Requirements: 6.7, 6.8, 6.9, 6.11_

  - [x] 56.3 Write property test for Output Attestation Digest Integrity
    - **Property 44: Output Attestation Digest Integrity**
    - Verify that for any Script_Output, the user_data passed to nitro-tpm-attest matches the SHA-256 hex digest of that Script_Output
    - **Validates: Requirements 6.7, 6.9**

  - [x] 56.4 Write property test for Output Attestation Base64 Encoding
    - **Property 45: Output Attestation Base64 Encoding**
    - Verify that when output attestation generation succeeds, the `output_attestation_document` field is a valid base64-encoded string
    - **Validates: Requirements 6.8**

  - [x] 56.5 Write property test for Output Attestation Failure Graceful Degradation
    - **Property 46: Output Attestation Failure Graceful Degradation**
    - Verify that when output attestation generation fails, the response still includes Script_Output and Attestation_Document, with `output_attestation_document` set to `null` and `attestation_error` present
    - **Validates: Requirements 6.11**

  - [x] 56.6 Write unit tests for output attestation
    - Test `generate_output_attestation` with mocked nitro-tpm-attest (success and failure paths)
    - Test GET /output endpoint returns `output_attestation_document` when execution is complete
    - Test GET /output endpoint returns `attestation_error` when output attestation fails
    - Test GET /output endpoint omits `output_attestation_document` when execution is still running
    - _Requirements: 6.7, 6.8, 6.9, 6.11_

- [x] 57. Checkpoint - Ensure all output attestation tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 58. Add PyJWT dependency and update ServerConfig for OIDC authentication
  - [x] 58.1 Add PyJWT[crypto] to pyproject.toml dependencies
    - Add `PyJWT[crypto]>=2.8.0` to the `dependencies` list in pyproject.toml
    - This provides JWT decoding and JWKS/RSA signature verification via the `cryptography` backend
    - _Requirements: 2.4, 2.5, 12.4_

  - [x] 58.2 Update ServerConfig with OIDC configuration fields
    - Add `allowed_repositories: list[str]` field to ServerConfig dataclass
    - Add `expected_audience: str` field to ServerConfig dataclass
    - Load `ALLOWED_REPOSITORIES` from environment (comma-separated string, split into list)
    - Load `EXPECTED_AUDIENCE` from environment
    - Add validation: `allowed_repositories` must be non-empty list, `expected_audience` must be non-empty string
    - Add both to `from_env()` and `validate()` methods
    - _Requirements: 9.2, 9.3, 9.8_

  - [x] 58.3 Update .env.example and KIWI env file with OIDC config variables
    - Add `ALLOWED_REPOSITORIES=owner/repo1,owner/repo2` to .env.example
    - Add `EXPECTED_AUDIENCE=https://your-remote-executor.example.com` to .env.example
    - Add same variables to kiwi-descriptions/root/etc/github-actions-remote-executor/env
    - _Requirements: 9.2, 9.3_

  - [x] 58.4 Update KIWI config.sh to verify PyJWT is importable
    - Add `python3.11 -c "import jwt"` verification check alongside existing fastapi/uvicorn/requests checks
    - _Requirements: 12.4_

- [x] 59. Implement OIDC token validation in RequestValidator
  - [x] 59.1 Add OIDCValidationResult and OIDCTokenClaims data models
    - Create `OIDCValidationResult` dataclass with fields: `valid` (bool), `status_code` (int), `error_message` (str | None), `claims` (dict | None)
    - Create `OIDCTokenClaims` dataclass with fields: `iss` (str), `aud` (str), `repository` (str), `exp` (int), `sub` (str)
    - Add to src/models.py or src/validation.py
    - _Requirements: 2.1, 2.7, 2.9, 2.11, 2.13_

  - [x] 59.2 Update RequestValidator constructor for OIDC configuration
    - Add `__init__` method accepting `allowed_repositories: list[str]` and `expected_audience: str`
    - Store as instance attributes for use in OIDC validation
    - Update all call sites (server.py `create_app`) to pass config values
    - _Requirements: 2.9, 2.11_

  - [x] 59.3 Implement `_fetch_jwks()` method
    - Fetch JWKS from `https://token.actions.githubusercontent.com/.well-known/jwks`
    - Cache the JWKS response in an instance variable
    - Accept `force_refresh: bool = False` parameter to refresh cache on unknown key ID
    - Use `requests.get()` with a reasonable timeout
    - Parse response as JSON and return the JWKS dict
    - Handle network errors gracefully
    - _Requirements: 2.4, 2.5_

  - [x] 59.4 Implement `validate_oidc_token()` method
    - Extract Bearer token from Authorization header string
    - Return 401 if header is missing or not in `Bearer <token>` format
    - Decode JWT header to get `kid` (key ID)
    - Fetch JWKS (from cache or fresh) and find matching key by `kid`
    - If `kid` not found in cached JWKS, force refresh and retry once
    - Verify JWT signature against the matching JWKS key using `jwt.decode()` with `algorithms=["RS256"]`
    - Validate `iss` claim equals `https://token.actions.githubusercontent.com` — return 401 if mismatch
    - Validate `aud` claim equals `self.expected_audience` — return 401 if mismatch
    - Validate `repository` claim is in `self.allowed_repositories` — return 403 if not found
    - Validate `exp` claim (PyJWT handles expiration automatically) — return 401 if expired
    - Return `OIDCValidationResult` with `valid=True`, `status_code=200`, and decoded claims on success
    - Return `OIDCValidationResult` with appropriate `status_code` (401 or 403) and `error_message` on failure
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14_

- [ ] 60. Add OIDC authentication middleware to protected endpoints
  - [ ] 60.1 Add OIDC authentication to POST /execute endpoint
    - Extract Authorization header from request
    - Call `request_validator.validate_oidc_token(authorization_header)` before processing the request body
    - If validation fails with 401, return HTTP 401 with error message
    - If validation fails with 403, return HTTP 403 with error message
    - Log OIDC validation result (success/failure, repository claim) excluding the token itself
    - _Requirements: 2.1, 2.3, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14_

  - [ ] 60.2 Add OIDC authentication to GET /execution/{id}/output endpoint
    - Extract Authorization header from request
    - Call `request_validator.validate_oidc_token(authorization_header)` before retrieving output
    - If validation fails with 401, return HTTP 401 with error message
    - If validation fails with 403, return HTTP 403 with error message
    - _Requirements: 2.2, 2.3, 6.3_

  - [ ] 60.3 Ensure /health endpoint remains unauthenticated
    - Verify that the GET /health endpoint does NOT call `validate_oidc_token()`
    - No Authorization header required for health checks
    - _Requirements: 2.20_

- [ ] 61. Checkpoint - Ensure OIDC implementation compiles and existing tests are updated
  - Update existing tests that construct `ServerConfig` to include `allowed_repositories` and `expected_audience` fields
  - Update existing tests that construct `RequestValidator` to pass OIDC config parameters
  - Ensure all existing tests pass with the updated signatures
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 62. Write property tests for OIDC token validation
  - [ ] 62.1 Write property test for OIDC Issuer Claim Validation
    - **Property 104: OIDC Issuer Claim Validation**
    - Generate OIDC tokens with arbitrary `iss` claims that do not match `https://token.actions.githubusercontent.com`
    - Verify the Request Validator rejects with HTTP 401
    - **Validates: Requirements 2.7, 2.8**

  - [ ] 62.2 Write property test for OIDC Audience Claim Validation
    - **Property 105: OIDC Audience Claim Validation**
    - Generate OIDC tokens with arbitrary `aud` claims that do not match the configured Expected_Audience
    - Verify the Request Validator rejects with HTTP 401
    - **Validates: Requirements 2.9, 2.10**

  - [ ] 62.3 Write property test for OIDC Repository Authorization
    - **Property 106: OIDC Repository Authorization**
    - Generate OIDC tokens with arbitrary `repository` claims not in the Allowed_Repositories list
    - Verify the Request Validator rejects with HTTP 403
    - **Validates: Requirements 2.11, 2.12**

  - [ ] 62.4 Write property test for OIDC Token Expiration Validation
    - **Property 107: OIDC Token Expiration Validation**
    - Generate OIDC tokens with `exp` claims in the past
    - Verify the Request Validator rejects with HTTP 401
    - **Validates: Requirements 2.13, 2.14**

  - [ ] 62.5 Write property test for Health Endpoint No Authentication
    - **Property 108: Health Endpoint No Authentication**
    - Send requests to /health without any Authorization header
    - Verify the server responds with HTTP 200 without requiring authentication
    - **Validates: Requirements 2.20**

  - [ ] 62.6 Write property test for OIDC Token Required on Protected Endpoints
    - **Property 8: OIDC Token Required on Protected Endpoints**
    - Send requests to /execute and /execution/{id}/output without Authorization header
    - Verify both return HTTP 401
    - **Validates: Requirements 2.1, 2.2, 2.3**

  - [ ] 62.7 Write property test for OIDC Token Signature Verification
    - **Property 10: OIDC Token Signature Verification**
    - Generate tokens signed with a different key than the JWKS
    - Verify the Request Validator rejects with HTTP 401
    - **Validates: Requirements 2.4, 2.6**

- [ ] 63. Write unit tests for OIDC validation
  - [ ] 63.1 Write unit tests for OIDC token validation
    - Test missing Authorization header returns 401
    - Test malformed Authorization header (not "Bearer <token>") returns 401
    - Test valid token with correct claims returns success
    - Test token with wrong issuer returns 401
    - Test token with wrong audience returns 401
    - Test token with unauthorized repository returns 403
    - Test expired token returns 401
    - Test token signed with wrong key returns 401
    - Test JWKS cache refresh on unknown key ID
    - Test JWKS fetch failure handling
    - Mock JWKS endpoint and JWT signing for all tests
    - _Requirements: 2.1-2.14, 2.20_

  - [ ] 63.2 Write unit tests for OIDC-protected endpoints
    - Test POST /execute without Authorization header returns 401
    - Test POST /execute with invalid token returns 401
    - Test POST /execute with unauthorized repo token returns 403
    - Test POST /execute with valid token proceeds to execution
    - Test GET /execution/{id}/output without Authorization header returns 401
    - Test GET /execution/{id}/output with valid token returns output
    - Test GET /health without Authorization header returns 200
    - _Requirements: 2.1, 2.2, 2.3, 2.20_

- [ ] 64. Update existing tests for OIDC authentication compatibility
  - [ ] 64.1 Update tests/test_server_unit.py for OIDC
    - Add OIDC token mocking/bypass to all existing endpoint tests
    - Update ServerConfig construction to include `allowed_repositories` and `expected_audience`
    - Ensure existing /execute and /output tests pass with OIDC middleware active
    - _Requirements: 2.1, 2.2_

  - [ ] 64.2 Update tests/test_integration.py for OIDC
    - Add OIDC token mocking to integration test setup
    - Update ServerConfig construction to include OIDC fields
    - Ensure end-to-end flow tests work with OIDC authentication
    - _Requirements: 2.1, 2.2_

  - [ ] 64.3 Update tests/test_config_properties.py for OIDC config fields
    - Add property tests for `ALLOWED_REPOSITORIES` and `EXPECTED_AUDIENCE` environment variable loading
    - Test comma-separated repository list parsing
    - Test missing OIDC config variables cause startup failure
    - _Requirements: 9.2, 9.3, 9.8_

  - [ ] 64.4 Update remaining test files that construct ServerConfig
    - Update tests/test_health_metrics_unit.py
    - Update tests/test_health_metrics_properties.py
    - Update tests/test_logging_error_handling_properties.py
    - Add `allowed_repositories` and `expected_audience` to all ServerConfig constructor calls
    - _Requirements: 9.2, 9.3_

- [ ] 65. Final checkpoint - Ensure all OIDC tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests validate the 108 correctness properties from the design document
- The runtime implementation (tasks 1-16) uses Python with FastAPI for the HTTP server
- The build implementation (tasks 17-31) uses GitHub Actions, KIWI NG, ORAS, Terraform, and Python
- The deployment implementation (tasks 32-36) uses Terraform and Python to provision the target EC2 instance and supporting infrastructure
- The cleanup implementation (tasks 37-38) covers testing the existing scripts/cleanup.py which is already fully implemented
- The debug SSH implementation (tasks 39-47) adds opt-in SSH debug access across build-time (KIWI image), deploy-time (Terraform + deploy script), and GitHub Actions workflow
- Python dependencies are separated into two configurations:
  - pyproject.toml: Remote executor service dependencies (fastapi, uvicorn, requests, hypothesis, pytest, pytest-asyncio, httpx)
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
- Scripts execute as root with full system privileges
- OIDC authentication (tasks 58-65) replaces the previous shared secret token approach with GitHub Actions OIDC JWT validation
- PyJWT[crypto] is used for JWT decoding and JWKS-based signature verification
- OIDC tokens are validated for signature (JWKS), issuer, audience, repository, and expiration claims
- Protected endpoints (/execute, /execution/{id}/output) require Bearer OIDC tokens; /health remains unauthenticated
- All 108 properties should be tested with hypothesis library (minimum 100 iterations each)
- Checkpoints ensure incremental validation throughout implementation
- Build tasks (17-32) can be implemented independently from runtime tasks (1-16)
- AMI build process uses Terraform to provision temporary EC2 infrastructure with complete VPC/networking setup
- Build instance uses Amazon Linux 2023 with IMDSv2 enforcement
- Signature verification is mandatory before AMI creation - no bypass mechanism
- Tool installation includes specific versions: ORAS 1.3.0, Rust via rustup, GitHub CLI via dnf, coldsnap from source
- Coldsnap installed to /home/ec2-user/.cargo/bin/coldsnap (full path required for execution)
- SSH connectivity uses paramiko with keepalive (30s intervals) and retries (10 attempts, 30s delay)
- Infrastructure cleanup guaranteed via finally block, executes even on build failure
- Terraform state isolated per build for concurrent build support
- Deployment Terraform (terraform/deploy/) creates persistent infrastructure unlike build Terraform which is temporary
- Deployment VPC uses CIDR 10.0.0.0/16 (distinct from build VPC 10.2.0.0/16)
- Target instance has HTTP-only access on port 8080 (no SSH) — reduced attack surface compared to build instance
- IMDSv2 enforced on target instance with http_tokens = "required" and hop limit = 1
- NitroTPM automatically enabled via AMI registration settings (TpmSupport = v2.0, BootMode = uefi)
- Deployment script auto-detects user IP via checkip.amazonaws.com for /32 CIDR whitelisting
- On deployment failure, user must manually run terraform destroy (no automated cleanup — infrastructure is meant to persist)
- Cleanup script (scripts/cleanup.py) is already fully implemented; tasks 37-38 focus exclusively on writing property and unit tests for the existing code
- Cleanup script uses subprocess to invoke Terraform and boto3 for AWS API calls (deregister AMI, describe resources)
- Cleanup verification checks for EC2 instances, AMIs, and EBS snapshots using project-specific tags and resource IDs
