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
  - [x] 11.1 Create HTTP server with FastAPI
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

- [x] 38a. Implement --keep-ami flag in cleanup script
  - [x] 38a.1 Add --keep-ami CLI argument to parse_arguments()
    - Add `--keep-ami` as a boolean flag (store_true) defaulting to False
    - Log at INFO level when --keep-ami is provided that AMI and snapshot will be preserved
    - _Requirements: 28.3, 28.8_

  - [x] 38a.2 Update deregister_ami() to respect keep_ami parameter
    - Add `keep_ami: bool = False` parameter to deregister_ami function signature
    - When keep_ami is True, log at INFO level that AMI deregistration and snapshot deletion were skipped, then return immediately
    - When keep_ami is False, proceed with existing deregistration logic unchanged
    - _Requirements: 30.2, 30.4, 30.8, 30.9_

  - [x] 38a.3 Update verify_cleanup() to respect keep_ami parameter
    - Add `keep_ami: bool = False` parameter to verify_cleanup function signature
    - When keep_ami is True, skip AMI and EBS snapshot checks (do not include them in remaining-resource list)
    - When keep_ami is True and no remaining resources found, log that cleanup is complete and AMI/snapshot were intentionally preserved
    - When keep_ami is False, retain existing verification logic unchanged
    - _Requirements: 31.2, 31.3, 31.4, 31.8_

  - [x] 38a.4 Update main() to pass keep_ami through
    - Pass args.keep_ami to deregister_ami() and verify_cleanup() calls
    - Log AMI preservation intent early in main() when flag is set
    - _Requirements: 28.3, 28.8, 30.8, 31.4_

  - [x] 38a.5 Write property test for keep-ami controls deregistration
    - **Property 95: Keep-AMI Controls Deregistration**
    - Test that deregister_ami with keep_ami=True makes zero AWS API calls for deregistration or snapshot deletion
    - Test that deregister_ami with keep_ami=False proceeds with normal deregistration flow
    - **Validates: Requirements 30.2, 30.4, 30.8, 30.9**

  - [x] 38a.6 Update existing property and unit tests for --keep-ami
    - Update Property 87 test to cover --keep-ami flag parsing (present and absent)
    - Update Property 93 test to cover keep_ami=True excluding AMI/snapshot from checks and logging preservation message
    - Update unit tests for parse_arguments, deregister_ami, verify_cleanup, and main with --keep-ami scenarios
    - _Requirements: 28.1, 28.2, 28.3, 30.8, 30.9, 31.2, 31.3, 31.4, 31.8_

- [x] 38b. Checkpoint - Ensure all --keep-ami tests pass
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

- [x] 60. Add OIDC authentication middleware to protected endpoints
  - [x] 60.1 Add OIDC authentication to POST /execute endpoint
    - Extract Authorization header from request
    - Call `request_validator.validate_oidc_token(authorization_header)` before processing the request body
    - If validation fails with 401, return HTTP 401 with error message
    - If validation fails with 403, return HTTP 403 with error message
    - Log OIDC validation result (success/failure, repository claim) excluding the token itself
    - _Requirements: 2.1, 2.3, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14_

  - [x] 60.2 Add OIDC authentication to GET /execution/{id}/output endpoint
    - Extract Authorization header from request
    - Call `request_validator.validate_oidc_token(authorization_header)` before retrieving output
    - If validation fails with 401, return HTTP 401 with error message
    - If validation fails with 403, return HTTP 403 with error message
    - _Requirements: 2.2, 2.3, 6.3_

  - [x] 60.3 Ensure /health endpoint remains unauthenticated
    - Verify that the GET /health endpoint does NOT call `validate_oidc_token()`
    - No Authorization header required for health checks
    - _Requirements: 2.20_

- [x] 61. Checkpoint - Ensure OIDC implementation compiles and existing tests are updated
  - Update existing tests that construct `ServerConfig` to include `allowed_repositories` and `expected_audience` fields
  - Update existing tests that construct `RequestValidator` to pass OIDC config parameters
  - Ensure all existing tests pass with the updated signatures
  - Ensure all tests pass, ask the user if questions arise.

- [x] 62. Write property tests for OIDC token validation
  - [x] 62.1 Write property test for OIDC Issuer Claim Validation
    - **Property 104: OIDC Issuer Claim Validation**
    - Generate OIDC tokens with arbitrary `iss` claims that do not match `https://token.actions.githubusercontent.com`
    - Verify the Request Validator rejects with HTTP 401
    - **Validates: Requirements 2.7, 2.8**

  - [x] 62.2 Write property test for OIDC Audience Claim Validation
    - **Property 105: OIDC Audience Claim Validation**
    - Generate OIDC tokens with arbitrary `aud` claims that do not match the configured Expected_Audience
    - Verify the Request Validator rejects with HTTP 401
    - **Validates: Requirements 2.9, 2.10**

  - [x] 62.3 Write property test for OIDC Repository Authorization
    - **Property 106: OIDC Repository Authorization**
    - Generate OIDC tokens with arbitrary `repository` claims not in the Allowed_Repositories list
    - Verify the Request Validator rejects with HTTP 403
    - **Validates: Requirements 2.11, 2.12**

  - [x] 62.4 Write property test for OIDC Token Expiration Validation
    - **Property 107: OIDC Token Expiration Validation**
    - Generate OIDC tokens with `exp` claims in the past
    - Verify the Request Validator rejects with HTTP 401
    - **Validates: Requirements 2.13, 2.14**

  - [x] 62.5 Write property test for Health Endpoint No Authentication
    - **Property 108: Health Endpoint No Authentication**
    - Send requests to /health without any Authorization header
    - Verify the server responds with HTTP 200 without requiring authentication
    - **Validates: Requirements 2.20**

  - [x] 62.6 Write property test for OIDC Token Required on Protected Endpoints
    - **Property 8: OIDC Token Required on Protected Endpoints**
    - Send requests to /execute and /execution/{id}/output without Authorization header
    - Verify both return HTTP 401
    - **Validates: Requirements 2.1, 2.2, 2.3**

  - [x] 62.7 Write property test for OIDC Token Signature Verification
    - **Property 10: OIDC Token Signature Verification**
    - Generate tokens signed with a different key than the JWKS
    - Verify the Request Validator rejects with HTTP 401
    - **Validates: Requirements 2.4, 2.6**

- [x] 63. Write unit tests for OIDC validation
  - [x] 63.1 Write unit tests for OIDC token validation
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

  - [x] 63.2 Write unit tests for OIDC-protected endpoints
    - Test POST /execute without Authorization header returns 401
    - Test POST /execute with invalid token returns 401
    - Test POST /execute with unauthorized repo token returns 403
    - Test POST /execute with valid token proceeds to execution
    - Test GET /execution/{id}/output without Authorization header returns 401
    - Test GET /execution/{id}/output with valid token returns output
    - Test GET /health without Authorization header returns 200
    - _Requirements: 2.1, 2.2, 2.3, 2.20_

- [x] 64. Update existing tests for OIDC authentication compatibility
  - [x] 64.1 Update tests/test_server_unit.py for OIDC
    - Add OIDC token mocking/bypass to all existing endpoint tests
    - Update ServerConfig construction to include `allowed_repositories` and `expected_audience`
    - Ensure existing /execute and /output tests pass with OIDC middleware active
    - _Requirements: 2.1, 2.2_

  - [x] 64.2 Update tests/test_integration.py for OIDC
    - Add OIDC token mocking to integration test setup
    - Update ServerConfig construction to include OIDC fields
    - Ensure end-to-end flow tests work with OIDC authentication
    - _Requirements: 2.1, 2.2_

  - [x] 64.3 Update tests/test_config_properties.py for OIDC config fields
    - Add property tests for `ALLOWED_REPOSITORIES` and `EXPECTED_AUDIENCE` environment variable loading
    - Test comma-separated repository list parsing
    - Test missing OIDC config variables cause startup failure
    - _Requirements: 9.2, 9.3, 9.8_

  - [x] 64.4 Update remaining test files that construct ServerConfig
    - Update tests/test_health_metrics_unit.py
    - Update tests/test_health_metrics_properties.py
    - Update tests/test_logging_error_handling_properties.py
    - Add `allowed_repositories` and `expected_audience` to all ServerConfig constructor calls
    - _Requirements: 9.2, 9.3_

- [x] 65. Final checkpoint - Ensure all OIDC tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 66. Add Docker SDK dependency and update ServerConfig for container execution
  - [x] 66.1 Add docker Python SDK to pyproject.toml dependencies
    - Add `docker>=7.0.0` to the `dependencies` list in pyproject.toml
    - This provides the Docker SDK for managing container lifecycle
    - _Requirements: 12.8_

  - [x] 66.2 Add container configuration fields to ServerConfig
    - Add `container_image: str` field to ServerConfig dataclass (Docker image name for Execution_Containers)
    - Add `container_memory_limit: str` field to ServerConfig dataclass (e.g., '512m')
    - Add `container_cpu_limit: float` field to ServerConfig dataclass (e.g., 1.0)
    - Load `CONTAINER_IMAGE` from environment variable
    - Load `CONTAINER_MEMORY_LIMIT` from environment variable
    - Load `CONTAINER_CPU_LIMIT` from environment variable
    - Add validation: `container_image` must be non-empty, `container_memory_limit` must be non-empty, `container_cpu_limit` must be > 0
    - _Requirements: 9.6, 9.7_

  - [x] 66.3 Update .env.example and KIWI env file with container config variables
    - Add `CONTAINER_IMAGE=python:3.11-slim` to .env.example
    - Add `CONTAINER_MEMORY_LIMIT=512m` to .env.example
    - Add `CONTAINER_CPU_LIMIT=1.0` to .env.example
    - Add same variables to kiwi-descriptions/root/etc/github-actions-remote-executor/env
    - _Requirements: 9.6, 9.7_

  - [x] 66.4 Update KIWI config.sh to verify docker package is importable
    - Add `python3.11 -c "import docker"` verification check alongside existing fastapi/uvicorn/requests/jwt checks
    - _Requirements: 12.8_

  - [x] 66.5 Update existing tests that construct ServerConfig to include container fields
    - Update tests/test_config.py, tests/test_config_properties.py
    - Update tests/test_health_metrics_unit.py, tests/test_health_metrics_properties.py
    - Update tests/test_server_unit.py, tests/test_integration.py
    - Update tests/test_logging_error_handling_properties.py
    - Add `container_image`, `container_memory_limit`, `container_cpu_limit` to all ServerConfig constructor calls
    - _Requirements: 9.6, 9.7_

- [x] 67. Checkpoint - Ensure all tests pass after adding Docker config
  - Ensure all tests pass, ask the user if questions arise.

- [x] 68. Rewrite ScriptExecutor to use Docker SDK for container-based execution
  - [x] 68.1 Update ScriptExecutor constructor for Docker SDK
    - Replace subprocess-based execution with Docker SDK (`docker` Python package)
    - Accept `docker_client: docker.DockerClient` parameter
    - Accept `container_image: str` parameter (Container_Image name)
    - Accept `memory_limit: str` parameter (Docker memory constraint)
    - Accept `cpu_limit: float` parameter (Docker CPU constraint)
    - Keep existing `execution_manager`, `output_collector`, `temp_storage_path` parameters
    - _Requirements: 5.1, 5.2, 8.1, 8.2, 9.6, 9.7_

  - [x] 68.2 Implement Docker container lifecycle in execute_async
    - Create a new Execution_Container from the configured Container_Image for each execution
    - Assign a unique container name derived from the Execution_ID (e.g., `gare-exec-{execution_id}`)
    - Configure container with security constraints:
      - Memory limit from `container_memory_limit` config
      - CPU limit from `container_cpu_limit` config (via `nano_cpus`)
      - Read-only root filesystem (`read_only=True`) with a writable tmpfs mount for the execution directory
      - Network disabled (`network_mode='none'`)
      - No privilege escalation (`security_opt=['no-new-privileges']`)
      - Non-root user (`user` parameter)
    - Bind-mount the script file read-only into the container at `/scripts/script.sh` using Docker `volumes` parameter at creation time (avoids `put_archive` failures on read-only root filesystems)
    - Execute the script via `command=["sh", "/scripts/script.sh"]`
    - Capture stdout and stderr streams from the container
    - Enforce execution timeout by stopping the container after timeout
    - Capture exit code from the container
    - Remove the container after completion, failure, or timeout
    - _Requirements: 5.1, 5.2, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.13, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [x] 68.3 Implement container removal with verification
    - After execution completes (success, failure, or timeout), remove the Execution_Container
    - After removal, verify the container no longer exists on the Docker host by attempting to inspect it
    - Log verification result
    - _Requirements: 5.4, 5.5, 8.9_

  - [x] 68.4 Implement dangling container cleanup on startup
    - Create `cleanup_dangling_containers()` method
    - On startup, list all containers matching the naming convention (e.g., prefix `gare-exec-`)
    - Stop and remove any found dangling containers
    - Log each cleaned-up container
    - _Requirements: 8.10_

  - [x] 68.5 Implement Docker daemon accessibility check
    - Create `verify_docker_daemon()` method
    - Call `docker_client.ping()` to verify the Docker daemon is accessible
    - Return True if accessible, False otherwise
    - _Requirements: 9.11, 9.12_

- [x] 69. Wire Docker ScriptExecutor into application startup
  - [x] 69.1 Update src/main.py to initialize Docker client and ScriptExecutor
    - Create `docker.DockerClient` instance at startup using `docker.from_env()`
    - Pass Docker client and container config from ServerConfig to ScriptExecutor constructor
    - Call `verify_docker_daemon()` at startup; fail with descriptive error if Docker is not accessible
    - Call `cleanup_dangling_containers()` at startup before accepting requests
    - _Requirements: 9.11, 9.12, 8.10_

  - [x] 69.2 Update src/server.py to pass Docker config to ScriptExecutor
    - Update `create_app` to construct ScriptExecutor with Docker client and container config
    - Pass `config.container_image`, `config.container_memory_limit`, `config.container_cpu_limit`
    - _Requirements: 9.6, 9.7_

  - [x] 69.3 Update health endpoint to include Docker availability
    - Add `docker_available` field to health check response
    - Call `verify_docker_daemon()` to determine Docker status
    - _Requirements: 10.2, 10.3_

- [x] 70. Checkpoint - Ensure Docker ScriptExecutor compiles and wires correctly
  - Ensure all tests pass, ask the user if questions arise.

- [x] 71. Write property tests for Docker container execution
  - [x] 71.1 Write property test for Container Non-Reuse
    - **Property 109: Container Non-Reuse**
    - For any two script executions, verify the Execution_Containers used are distinct and no container is reused
    - **Validates: Requirements 5.3**

  - [x] 71.2 Write property test for Container Unique Naming
    - **Property 110: Container Unique Naming**
    - For any script execution, verify the Execution_Container is assigned a unique name derived from the Execution_ID
    - **Validates: Requirements 5.13**

  - [x] 71.3 Write property test for Docker Container Security Constraints
    - **Property 111: Docker Container Security Constraints**
    - For any Execution_Container, verify it is configured with: non-root user, network disabled, read-only root filesystem (except execution directory), privilege escalation disabled, memory limits, and CPU limits
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6**

  - [x] 71.4 Write property test for Container Removal Verification
    - **Property 112: Container Removal Verification**
    - For any Execution_Container that is removed, verify the container no longer exists on the Docker host
    - **Validates: Requirements 8.9**

  - [x] 71.5 Write property test for Dangling Container Cleanup on Startup
    - **Property 113: Dangling Container Cleanup on Startup**
    - For any server startup, verify the Script_Executor removes dangling Execution_Containers matching the naming convention
    - **Validates: Requirements 8.10**

  - [x] 71.6 Write property test for Docker Daemon Accessibility Check
    - **Property 114: Docker Daemon Accessibility Check**
    - For any server startup, verify the Script_Executor checks Docker daemon accessibility; if not accessible, the server fails to start
    - **Validates: Requirements 9.11, 9.12**

  - [x] 71.7 Write property test for Container Image Configuration
    - **Property 115: Container Image Configuration**
    - For any configured Container_Image name, verify the Script_Executor uses that image when creating Execution_Containers
    - **Validates: Requirements 9.7**

- [x] 72. Write unit tests for Docker container management
  - [x] 72.1 Write unit tests for Docker ScriptExecutor
    - Test container creation with correct image, name, and security constraints (memory, CPU, read-only fs, no network, no privilege escalation, non-root user)
    - Test container execution captures stdout, stderr, and exit code
    - Test container is removed after successful execution
    - Test container is removed after failed execution
    - Test container is removed after timeout
    - Test container removal verification (container no longer exists)
    - Test dangling container cleanup on startup
    - Test Docker daemon accessibility check (success and failure)
    - Test container name derivation from Execution_ID
    - Mock Docker SDK client for all tests
    - _Requirements: 5.1-5.13, 8.1-8.10, 9.7, 9.11, 9.12_

  - [x] 72.2 Update existing ScriptExecutor tests for Docker-based execution
    - Update tests/test_script_executor.py to use mocked Docker client instead of subprocess
    - Update tests/test_script_executor_properties.py to verify Docker container behavior
    - Ensure Properties 21-28 still pass with Docker-based execution
    - _Requirements: 5.1, 5.2, 5.6, 5.7, 5.8, 5.9, 5.10_

- [x] 73. Final checkpoint - Ensure all Docker container tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 74. Add Docker package to KIWI image and enable Docker service
  - [x] 74.1 Add docker package to appliance.kiwi
    - Add `<package name="docker"/>` to the `<packages type="image">` section in `kiwi-descriptions/appliance.kiwi`
    - Place it alongside existing packages (e.g., after `python3.11-pip`)
    - _Requirements: 33.1, 33.4_

  - [x] 74.2 Enable Docker service in config.sh
    - Add a `systemctl enable docker` block to `kiwi-descriptions/config.sh`
    - Place it after the existing `systemctl enable github-actions-remote-executor.service` line and before the conditional SSH block
    - Include descriptive echo statements for build audit trail
    - _Requirements: 33.2, 33.3_

  - [x] 74.3 Write property test for Docker Package Inclusion
    - **Property 116: Docker Package Inclusion in KIWI Image**
    - Parse `kiwi-descriptions/appliance.kiwi` XML and verify the `docker` package is listed in the `<packages type="image">` section
    - **Validates: Requirements 33.1**

  - [x] 74.4 Write property test for Docker Service Enablement
    - **Property 117: Docker Service Enablement**
    - Parse `kiwi-descriptions/config.sh` and verify it contains `systemctl enable docker`
    - **Validates: Requirements 33.2**

- [x] 75. Checkpoint - Ensure Docker daemon provisioning tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 76. Pre-pull Container Image in build-kiwi-image.sh
  - [x] 76.1 Read CONTAINER_IMAGE from env file in build-kiwi-image.sh
    - Extract the `CONTAINER_IMAGE` variable from `kiwi-descriptions/root/etc/github-actions-remote-executor/env` (currently `python:3.11-slim`)
    - Use grep/sed to parse the value from the env file
    - Fail with a descriptive error if `CONTAINER_IMAGE` is not set or empty
    - _Requirements: 34.1_

  - [x] 76.2 Pull and export Container Image in build-kiwi-image.sh
    - After the Python dependency wheel download section and before the KIWI Docker build step, add a new section to:
      1. Pull the Container_Image using `docker pull "${CONTAINER_IMAGE}"`
      2. Export the pulled image as a tar archive using `docker save "${CONTAINER_IMAGE}" -o "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/container-image.tar"`
      3. Ensure the target directory exists with `mkdir -p`
    - If `docker pull` fails, exit with `::error::` and non-zero exit code
    - If `docker save` fails, exit with `::error::` and non-zero exit code
    - Include descriptive echo statements for build audit trail
    - _Requirements: 34.1, 34.2, 34.3, 34.6_

- [x] 77. Load Container Image in config.sh
  - [x] 77.1 Add container image load block to config.sh
    - Add a new section to `kiwi-descriptions/config.sh` after the Docker service enablement block and before the Python dependency installation section
    - Verify the container image tar exists at `/tmp/kiwi-build/container-image.tar`
    - Load the image using `docker load -i /tmp/kiwi-build/container-image.tar`
    - If the tar file is missing, fail with a descriptive error and `exit 1`
    - If `docker load` fails, fail with a descriptive error and `exit 1`
    - Include descriptive echo statements for build audit trail
    - _Requirements: 34.4, 34.5, 34.7_

- [x] 78. Write property tests for Container Image pre-pull
  - [x] 78.1 Write property test for Container Image Pre-Pull Round-Trip
    - **Property 118: Container Image Pre-Pull Round-Trip**
    - Verify that for any configured Container_Image name, the build process pulls the image, exports it as a tar, copies it into the KIWI build context, and loads it in config.sh — resulting in the image being available in the local Docker store
    - Test by parsing `build-kiwi-image.sh` for `docker pull` and `docker save` commands referencing the CONTAINER_IMAGE variable, and parsing `config.sh` for `docker load -i /tmp/kiwi-build/container-image.tar`
    - **Validates: Requirements 34.1, 34.2, 34.3, 34.4, 34.5**

  - [x] 78.2 Write property test for Container Image Pull Failure Halts Build
    - **Property 119: Container Image Pull Failure Halts Build**
    - Verify that if `docker pull` fails in `build-kiwi-image.sh`, the script exits with a non-zero exit code and a descriptive error message
    - Parse `build-kiwi-image.sh` for error handling around the `docker pull` command (e.g., `if ! docker pull` pattern with `exit 1`)
    - **Validates: Requirements 34.6**

  - [x] 78.3 Write property test for Container Image Load Failure Halts Build
    - **Property 120: Container Image Load Failure Halts Build**
    - Verify that if `docker load` fails in `config.sh`, the script exits with a non-zero exit code and a descriptive error message
    - Parse `config.sh` for error handling around the `docker load` command (e.g., `if ! docker load` pattern with `exit 1`)
    - **Validates: Requirements 34.7**

- [x] 79. Final checkpoint - Ensure all Docker daemon and container image pre-pull tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 80. Remove build-time container image pre-pull code
  - [x] 80.1 Remove container image pull/save section from build-kiwi-image.sh
    - Remove the section in `.github/scripts/build-kiwi-image.sh` that reads CONTAINER_IMAGE from the env file, runs `docker pull`, and runs `docker save` to export the image as a tar archive
    - Remove the `mkdir -p` for the container-image.tar target directory if it was added solely for this purpose
    - Do not remove any other sections (Python wheel download, KIWI Docker build, etc.)
    - _Requirements: 34.1 (updated — build no longer handles image pull)_

  - [x] 80.2 Remove container image load section from config.sh
    - Remove the section in `kiwi-descriptions/config.sh` that verifies and loads the container image tar via `docker load -i /tmp/kiwi-build/container-image.tar`
    - Remove associated echo statements and error handling for the container image load block
    - Do not remove the Docker service enablement (`systemctl enable docker`) or any other sections
    - _Requirements: 34.1 (updated — build no longer handles image load)_

- [x] 81. Implement server-startup container image pull
  - [x] 81.1 Add `pull_container_image` method to ScriptExecutor
    - Add a `pull_container_image(self) -> None` method to `src/script_executor.py`
    - Check if the Container_Image is already present in the local Docker image store using `docker_client.images.get(container_image)`; if present, log that the pull is being skipped and return early
    - If not present, pull the image from the registry using `docker_client.images.pull(container_image)`
    - After pulling, verify the image is available by calling `docker_client.images.get(container_image)`
    - Log the pull operation including image name, pull duration (time the pull call), and image size
    - If the pull fails (network error, image not found, authentication failure), raise an exception with a descriptive error message indicating the image name and failure reason
    - _Requirements: 34.1, 34.2, 34.3, 34.4, 34.5, 34.6_

  - [x] 81.2 Wire container image pull into server startup in src/main.py
    - In `src/main.py`, after the dangling container cleanup step and before the "Ensure temp storage directory exists" step, call `temp_executor.pull_container_image()`
    - If the pull raises an exception, catch it and raise `ConfigurationError` with a descriptive message so the server fails to start
    - This places the pull at step 4 in the startup sequence: config → Docker daemon → dangling cleanup → **pull image** → start accepting requests
    - _Requirements: 34.1, 34.2, 34.4_

- [x] 82. Update property tests for server-startup container image pull
  - [x] 82.1 Rewrite property test for Container Image Pull at Server Startup
    - **Property 118: Container Image Pull at Server Startup**
    - For any configured Container_Image name, verify the GHA_Server pulls the image from the container registry at startup and verifies it is available in the local Docker image store before accepting requests
    - Mock the Docker SDK client: `images.get()` raises `ImageNotFound` (image not present), then `images.pull()` succeeds, then `images.get()` succeeds
    - Verify `pull_container_image()` calls pull and verify in the correct order
    - **Validates: Requirements 34.1, 34.2, 34.3**

  - [x] 82.2 Rewrite property test for Container Image Pull Failure Halts Startup
    - **Property 119: Container Image Pull Failure Halts Startup**
    - For any Container_Image name that cannot be pulled (network error, image not found, authentication failure), verify the GHA_Server fails to start with a descriptive error message indicating the image name and failure reason
    - Mock the Docker SDK client: `images.get()` raises `ImageNotFound`, then `images.pull()` raises an exception
    - Verify `pull_container_image()` raises an exception with the image name in the error message
    - **Validates: Requirements 34.4**

  - [x] 82.3 Rewrite property test for Container Image Skip Pull When Already Present
    - **Property 120: Container Image Skip Pull When Already Present**
    - For any Container_Image that is already present in the local Docker image store, verify the GHA_Server skips pulling from the registry and uses the existing image
    - Mock the Docker SDK client: `images.get()` succeeds (image already present)
    - Verify `images.pull()` is NOT called
    - **Validates: Requirements 34.5**

- [x] 83. Write unit tests for server-startup container image pull
  - [x] 83.1 Write unit tests for pull_container_image method
    - Test successful pull flow: image not present → pull → verify available
    - Test skip pull when image already present locally
    - Test pull failure: image not found in registry
    - Test pull failure: network error during pull
    - Test pull failure: authentication error
    - Test pull logging: verify image name, duration, and size are logged
    - Test verify failure: image pulled but not available after pull
    - Test startup failure: verify ConfigurationError is raised in main.py when pull fails
    - Mock Docker SDK client for all tests
    - _Requirements: 34.1, 34.2, 34.3, 34.4, 34.5, 34.6_

- [x] 84. Checkpoint - Ensure all server-startup container image pull tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 85. Rewrite RepositoryClient to clone repositories instead of fetching single files
  - [x] 85.1 Replace fetch_file with clone_repo method
    - Replace the `fetch_file` method with a `clone_repo(repo_url, commit, token)` method
    - Use `git clone --depth 1` with the token embedded in the URL (`https://{token}@github.com/owner/repo.git`) to clone into a temp directory under `temp_storage_path`
    - After cloning, run `git checkout {commit}` to ensure the exact commit is checked out
    - Return a `CloneResult` dataclass with `clone_path` (path to cloned repo directory) and `script_path` (relative path within repo)
    - Handle clone failures: authentication errors, repository not found, network errors
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 3.7, 3.8_

  - [x] 85.2 Add validate_script_exists method
    - Add a `validate_script_exists(clone_path, script_path)` method that checks the script file exists within the cloned repo
    - Return True if the file exists, raise GitHubAPIError with 404 if not
    - _Requirements: 3.5_

  - [x] 85.3 Replace cleanup_temp_file with cleanup_clone method
    - Replace `cleanup_temp_file` with `cleanup_clone(clone_path)` that removes the entire cloned repo directory using `shutil.rmtree`
    - Handle cleanup errors gracefully (log but don't raise)
    - _Requirements: 3.5_

  - [x] 85.4 Add CloneResult data model
    - Add `CloneResult` dataclass to `src/models.py` with fields: `clone_path: str`, `script_path: str`
    - _Requirements: 3.1_

  - [x] 85.5 Remove FileContent data model and _store_temp_file method
    - Remove the `FileContent` dataclass, `_store_temp_file`, and `_check_commit_exists` methods that are no longer needed
    - Keep `_parse_repo_url` and `_check_repository_exists` if still useful, otherwise remove
    - _Requirements: 3.1_

- [x] 86. Update ScriptExecutor to mount cloned repo directory
  - [x] 86.1 Update execute_async signature to accept repo_path and script_path
    - Change `execute_async(execution_id, script_path)` to `execute_async(execution_id, repo_path, script_path)`
    - `repo_path` is the path to the cloned repository directory on the host
    - `script_path` is the relative path to the script within the repo
    - _Requirements: 5.1, 5.2_

  - [x] 86.2 Update _execute_in_container to mount repo directory
    - Replace the single-file bind-mount with a directory mount: mount `repo_path` read-only at `/workspace` in the container
    - Set the container working directory to `/workspace` using `working_dir="/workspace"`
    - Update the command to `["sh", "/workspace/{script_path}"]`
    - Keep all existing security constraints (memory, CPU, read-only rootfs, tmpfs, no network, no-new-privileges, non-root user)
    - _Requirements: 5.1, 5.2, 5.13, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [x] 86.3 Update _cleanup_temp_files to remove cloned repo directory
    - Update `_cleanup_temp_files` to accept `repo_path` instead of `script_path`
    - Use `shutil.rmtree(repo_path)` to remove the entire cloned repo directory
    - _Requirements: 5.4, 5.5_

- [x] 87. Update server.py to wire new RepositoryClient and ScriptExecutor
  - [x] 87.1 Update POST /execute endpoint
    - Replace `repo_client.fetch_file(...)` call with `repo_client.clone_repo(repo_url, commit, token)`
    - Add `repo_client.validate_script_exists(clone_result.clone_path, script_path)` call
    - Update `executor.execute_async(execution_id, clone_result.clone_path, clone_result.script_path)` call
    - Replace `repo_client.cleanup_temp_file(file_content.temp_path)` with `repo_client.cleanup_clone(clone_result.clone_path)` in error paths
    - Remove script file size validation (no longer fetching individual files)
    - _Requirements: 1.1, 1.2, 1.3, 3.1, 3.5_

- [x] 88. Update tests for repository cloning approach
  - [x] 88.1 Rewrite RepositoryClient tests
    - Update tests/test_repository.py to test clone_repo instead of fetch_file
    - Mock `subprocess.run` for git clone and git checkout commands
    - Test authentication via token-embedded URL
    - Test clone failure scenarios (auth error, repo not found, network error)
    - Test validate_script_exists with existing and missing files
    - Test cleanup_clone removes directory
    - _Requirements: 3.1-3.9_

  - [x] 88.2 Rewrite RepositoryClient property tests
    - Update property tests to test clone_repo behavior
    - **Property 9: Exact Commit Repository Clone** — verify clone checks out the exact commit
    - **Property 11: Repository Not Found Response** — verify clone failure for non-existent repo
    - **Property 14: Temporary Repository Clone Storage** — verify clone stored in temp directory
    - _Requirements: 3.1-3.9_

  - [x] 88.3 Update ScriptExecutor tests for repo directory mounting
    - Update tests to verify container is created with repo directory mounted at `/workspace`
    - Verify working_dir is set to `/workspace`
    - Verify command uses `/workspace/{script_path}`
    - Verify repo directory is cleaned up after execution
    - _Requirements: 5.1, 5.2, 5.4, 5.5_

  - [x] 88.4 Update server endpoint tests
    - Update tests/test_server_unit.py to mock clone_repo instead of fetch_file
    - Update tests/test_integration.py for the new flow
    - _Requirements: 1.1, 3.1_

- [x] 89. Checkpoint - Ensure all repository cloning tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 90. Add git package to KIWI image
  - [x] 90.1 Add git package to appliance.kiwi
    - Add `<package name="git"/>` to the `<packages type="image">` section in `kiwi-descriptions/appliance.kiwi`
    - Place it alongside existing packages (e.g., after `docker`)
    - _Requirements: 35.1, 35.2_

  - [x] 90.2 Write property test for Git Package Inclusion
    - **Property 121: Git Package Inclusion in KIWI Image**
    - Parse `kiwi-descriptions/appliance.kiwi` XML and verify the `git` package is listed in the `<packages type="image">` section
    - **Validates: Requirements 35.1**

- [x] 91. Implement EncryptionManager class
  - [x] 91.1 Create src/encryption.py with EncryptionManager class
    - Generate Server_Keypair (X25519) at initialization using the `cryptography` library; hold in memory only, never persist to disk
    - Implement `server_public_key` property returning serialized Server_Public_Key bytes
    - Implement `decrypt_request(encrypted_payload, client_public_key)` that derives Shared_Key via HPKE from Client_Public_Key and Server_Keypair, decrypts the payload, and returns `(decrypted_dict, shared_key_bytes)`
    - Implement `encrypt_response(payload_dict, shared_key)` that encrypts a response payload using the given Shared_Key
    - Implement `decrypt_with_shared_key(encrypted_payload, shared_key)` for decrypting /execution/{id}/output requests using a stored Shared_Key
    - Implement `store_encryption_context(execution_id, shared_key)` to store Shared_Key keyed by execution_id in a thread-safe dict
    - Implement `get_shared_key(execution_id)` to retrieve Shared_Key or None
    - Implement `remove_encryption_context(execution_id)` to remove context on execution cleanup
    - Raise ValueError on decryption failure
    - Log keypair generation at INFO level without logging private key material
    - _Requirements: 36.1, 36.2, 36.3, 36.4, 36.5, 40.3, 40.4, 40.5, 41.1, 41.2, 41.6, 41.7_

  - [x] 91.2 Write property test for Server Keypair Consistency
    - **Property 122: Server Keypair Consistency**
    - Create a single EncryptionManager instance and verify that `server_public_key` returns the same bytes across multiple accesses
    - **Validates: Requirements 36.3, 37.4**

  - [x] 91.3 Write property test for Server Public Key Serialization Round-Trip
    - **Property 127: Server Public Key Serialization Round-Trip**
    - Serialize the Server_Public_Key, deserialize it, and verify it can be used for HPKE key exchange producing the same Shared_Key
    - **Validates: Requirements 39.3, 39.4**

  - [x] 91.4 Write property test for HPKE Encrypt-Decrypt Round-Trip
    - **Property 128: HPKE Encrypt-Decrypt Round-Trip for Execute**
    - For random valid payloads, client-side encrypt with Server_Public_Key, server-side decrypt with Server_Keypair, verify original payload is recovered
    - **Validates: Requirements 40.1, 40.3, 40.4, 40.8**

  - [x] 91.5 Write unit tests for EncryptionManager
    - Test keypair generation at startup
    - Test encrypt/decrypt round-trip with known payloads
    - Test decryption failure with wrong key or corrupted ciphertext raises ValueError
    - Test Encryption_Context store/get/remove lifecycle
    - Test thread-safety of context operations
    - _Requirements: 36.1, 36.2, 40.3, 40.4, 40.5, 41.1, 41.6, 41.7_

- [x] 92. Integrate EncryptionManager into server startup
  - [x] 92.1 Update src/main.py to generate Server_Keypair at startup
    - Instantiate EncryptionManager before creating the FastAPI app
    - Log Server_Keypair generation at INFO level (no private key material)
    - Pass EncryptionManager instance to create_app
    - _Requirements: 36.1, 36.4, 36.5_

  - [x] 92.2 Update create_app in src/server.py to accept and store EncryptionManager
    - Add encryption_manager parameter to create_app
    - Store as app.state.encryption_manager
    - _Requirements: 36.1_

- [x] 93. Implement /attest endpoint
  - [x] 93.1 Add GET /attest route to src/server.py
    - Accept optional `nonce` query parameter
    - No authentication required
    - Call AttestationGenerator.generate_attestation with Server_Public_Key in `public_key` parameter and optional nonce
    - Return `{"attestation_document": "base64-encoded-cbor"}` unencrypted
    - Return HTTP 500 if attestation generation fails
    - _Requirements: 37.1, 37.2, 37.3, 37.4, 37.5, 37.6, 37.7, 37.8_

  - [x] 93.2 Update AttestationGenerator.generate_attestation to accept optional public_key parameter
    - When `public_key` bytes are provided, write them to a temp file and pass `--public-key` flag to nitro-tpm-attest
    - Clean up temp file in finally block
    - Only /attest callers pass public_key; /execute and /output callers do not
    - _Requirements: 39.1, 39.2_

  - [x] 93.3 Write property test for Attest Endpoint No Authentication
    - **Property 123: Attest Endpoint No Authentication**
    - Verify /attest returns 200 with attestation document without any auth credentials
    - **Validates: Requirements 37.2, 2.21**

  - [x] 93.4 Write property test for Attest Attestation Contains Server Public Key
    - **Property 124: Attest Attestation Contains Server Public Key**
    - Verify /attest attestation document includes Server_Public_Key in public_key field
    - **Validates: Requirements 37.4, 39.1**

  - [x] 93.5 Write property test for Non-Attest Attestation Excludes Server Public Key
    - **Property 125: Non-Attest Attestation Excludes Server Public Key**
    - Verify attestation documents generated for /execute and /execution/{id}/output do NOT include Server_Public_Key
    - **Validates: Requirements 37.9, 39.2**

- [x] 94. Checkpoint - Ensure attest endpoint and encryption manager tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 95. Add nonce support across all attestation-producing endpoints
  - [x] 95.1 Update /execute endpoint to pass nonce from decrypted body to attestation generator
    - Extract optional `nonce` field from decrypted request body
    - Pass nonce to AttestationGenerator.generate_attestation
    - _Requirements: 38.2, 38.4_

  - [x] 95.2 Update /execution/{id}/output endpoint to pass nonce to attestation generator
    - Extract optional `nonce` from decrypted request body
    - Pass nonce to generate_output_attestation when generating Output_Attestation_Document
    - Update generate_output_attestation to accept optional nonce parameter
    - _Requirements: 38.3, 38.4_

  - [x] 95.3 Write property test for Nonce Passthrough in Attestation
    - **Property 126: Nonce Passthrough in Attestation**
    - For random nonce values on /attest, /execute, and /execution/{id}/output, verify the nonce is passed to nitro-tpm-attest and included in the attestation document
    - **Validates: Requirements 37.5, 38.2, 38.3, 38.4, 38.6**

- [x] 96. Update /execute endpoint for encrypted payloads
  - [x] 96.1 Modify POST /execute to accept encrypted request envelope
    - Parse outer JSON body: `{"encrypted_payload": "base64", "client_public_key": "base64"}`
    - Call EncryptionManager.decrypt_request to derive Shared_Key and decrypt payload
    - Return HTTP 400 on decryption failure
    - Extract OIDC token from decrypted body `oidc_token` field instead of Authorization header
    - Extract all execution request fields from decrypted body
    - _Requirements: 40.1, 40.2, 40.3, 40.4, 40.5, 40.6, 40.7, 40.9_

  - [x] 96.2 Store Encryption_Context after successful /execute decryption
    - After creating execution record, call EncryptionManager.store_encryption_context(execution_id, shared_key)
    - _Requirements: 41.1, 41.2, 41.7_

  - [x] 96.3 Encrypt /execute response payload
    - Encrypt the response dict (execution_id, attestation_document, status) using EncryptionManager.encrypt_response with the Shared_Key
    - Return base64-encoded encrypted response
    - _Requirements: 41.3, 42.1_

  - [x] 96.4 Write property test for Decryption Failure Returns HTTP 400
    - **Property 129: Decryption Failure Returns HTTP 400**
    - Send random bytes, wrong key, or corrupted ciphertext to /execute and verify HTTP 400 response
    - **Validates: Requirements 40.5, 42.7**

  - [x] 96.5 Write property test for OIDC Token Extracted from Decrypted Body
    - **Property 130: OIDC Token Extracted from Decrypted Body**
    - Verify the server extracts and validates OIDC token from decrypted body `oidc_token` field, not from Authorization header
    - **Validates: Requirements 40.6, 40.9, 2.1, 2.2**

  - [x] 96.6 Write property test for Execute Response Encryption Round-Trip
    - **Property 132: Execute Response Encryption Round-Trip**
    - Encrypt /execute response with Shared_Key, client decrypts with same key, verify original content recovered
    - **Validates: Requirements 41.3, 42.1, 42.8**

- [x] 97. Update RequestValidator for OIDC token from body
  - [x] 97.1 Add validate_oidc_token_from_body method to RequestValidator
    - Accept raw OIDC token string (not Authorization header) from decrypted body `oidc_token` field
    - Reuse existing JWT verification logic (JWKS fetch, signature check, claims validation)
    - Return OIDCValidationResult with appropriate status codes
    - _Requirements: 2.1, 2.2, 2.3, 40.6, 40.9_

  - [x] 97.2 Update /execute and /output endpoints to use new validation method
    - Replace `validate_oidc_token(authorization_header)` calls with `validate_oidc_token_from_body(oidc_token_string)` on encrypted endpoints
    - Keep existing validate_oidc_token for any non-encrypted endpoints if needed
    - _Requirements: 2.1, 2.2, 40.9_

- [x] 98. Change /execution/{id}/output from GET to POST with encrypted request/response
  - [x] 98.1 Change route from GET to POST in src/server.py
    - Change `@app.get("/execution/{execution_id}/output")` to `@app.post("/execution/{execution_id}/output")`
    - Accept encrypted request body instead of query parameters
    - Look up Encryption_Context for execution_id; return HTTP 400 if not found
    - Decrypt request body using Shared_Key from Encryption_Context
    - Extract `oidc_token`, optional `nonce`, and optional `offset` from decrypted body
    - Validate OIDC token from decrypted body
    - _Requirements: 42.2, 42.3, 42.6, 42.7_

  - [x] 98.2 Encrypt /execution/{id}/output response payload
    - Encrypt the response dict (execution_id, status, stdout, stderr, offsets, complete, exit_code, attestation docs) using Shared_Key
    - Return base64-encoded encrypted response
    - _Requirements: 41.4, 41.5, 42.4, 42.5_

  - [x] 98.3 Write property test for Output Request-Response Encryption Round-Trip
    - **Property 133: Output Request-Response Encryption Round-Trip**
    - Client encrypts output request with Shared_Key, server decrypts, processes, encrypts response, client decrypts — verify original content
    - **Validates: Requirements 41.4, 41.5, 42.2, 42.3, 42.4, 42.8**

  - [x] 98.4 Write property test for Missing Encryption Context Returns HTTP 400
    - **Property 134: Missing Encryption Context Returns HTTP 400**
    - Request /execution/{id}/output with an execution_id that has no Encryption_Context, verify HTTP 400
    - **Validates: Requirements 42.6**

- [x] 99. Checkpoint - Ensure encrypted endpoint tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 100. Implement Encryption_Context lifecycle management
  - [x] 100.1 Integrate Encryption_Context cleanup with execution record cleanup
    - When ExecutionManager.cleanup_expired removes an execution record, also call EncryptionManager.remove_encryption_context for that execution_id
    - Ensure cleanup is thread-safe
    - _Requirements: 41.6, 41.7_

  - [x] 100.2 Write property test for Encryption Context Lifecycle
    - **Property 131: Encryption Context Lifecycle**
    - Verify Shared_Key is stored after /execute, persists during execution, and is removed when execution record is cleaned up
    - **Validates: Requirements 41.1, 41.2, 41.6**

- [x] 101. Verify encryption exemption for non-context endpoints
  - [x] 101.1 Ensure /attest, /health, /metrics return plain unencrypted JSON
    - Verify no encryption middleware is applied to these endpoints
    - /attest returns plain JSON with base64-encoded attestation document
    - /health and /metrics return plain JSON as before
    - _Requirements: 43.1, 43.2, 43.3, 43.4_

  - [x] 101.2 Write property test for Encryption Exemption
    - **Property 135: Encryption Exemption for Non-Context Endpoints**
    - Verify /attest, /health, /metrics responses are plain unencrypted JSON
    - **Validates: Requirements 43.1, 43.2, 43.3, 43.4**

- [x] 102. Update existing tests for OIDC header-to-body change
  - [x] 102.1 Update tests/test_oidc_property.py
    - Update property tests that send OIDC tokens via Authorization header to instead send them in encrypted request body `oidc_token` field
    - Ensure existing OIDC validation properties still pass with the new token extraction path
    - _Requirements: 2.1, 2.2, 40.6, 40.9_

  - [x] 102.2 Update tests/test_server_unit.py and tests/test_integration.py
    - Update /execute tests to send encrypted payloads with `oidc_token` in body
    - Update /execution/{id}/output tests to use POST with encrypted payloads
    - Update all assertions for encrypted response format
    - _Requirements: 2.1, 2.2, 40.6, 42.1, 42.4_

- [x] 103. Write integration tests for HPKE encrypted communication
  - [x] 103.1 Write end-to-end encrypted execution flow test
    - Test complete flow: GET /attest → extract Server_Public_Key → HPKE key exchange → encrypted POST /execute → encrypted POST /execution/{id}/output → decrypt responses
    - Verify attestation documents, execution results, and output integrity through encryption
    - _Requirements: 36.1, 37.1, 40.1, 41.1, 42.1, 42.4_

  - [x] 103.2 Write integration tests for error scenarios
    - Test decryption failure on /execute (wrong key)
    - Test missing Encryption_Context on /execution/{id}/output
    - Test expired OIDC token in encrypted body
    - Test unauthorized repository in encrypted body
    - _Requirements: 40.5, 42.6, 42.7_

- [x] 104. Final checkpoint - Ensure all HPKE encryption tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 105. Update /attest endpoint to exclude user_data from attestation document
  - [x] 105.1 Update AttestationGenerator.generate_attestation to support omitting user_data
    - When metadata is None (i.e., called from /attest), do NOT create user_data JSON and do NOT pass `--user-data` flag to nitro-tpm-attest
    - When metadata is provided (i.e., called from /execute or /execution/{id}/output), continue to include user_data as before
    - _Requirements: 37.10_

  - [x] 105.2 Update /attest endpoint handler to not pass user_data parameters
    - Remove the empty string arguments for repository_url, commit_hash, and script_path when calling generate_attestation from the /attest handler
    - Pass metadata=None (or omit metadata) so that user_data is excluded from the attestation document
    - _Requirements: 37.10_

  - [x] 105.3 Write property test for Attest Attestation Excludes User Data
    - **Property 136: Attest Attestation Excludes User Data**
    - Verify that for any request to /attest, the generate_attestation call does NOT include user_data (no `--user-data` flag passed to nitro-tpm-attest)
    - **Validates: Requirements 37.10**

- [x] 106. Checkpoint - Ensure /attest user_data exclusion tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 107. Open port 8080 to the world and add allowed_ssh_cidr variable in Terraform
  - [x] 107.1 Update terraform/deploy/main.tf security group port 8080 ingress
    - Change the port 8080 ingress `cidr_blocks` from `[var.allowed_http_cidr]` to `["0.0.0.0/0"]`
    - Port 8080 is now open to the world; authentication is handled at the application layer
    - _Requirements: 23.2, 23.4, 23.5_

  - [x] 107.2 Update terraform/deploy/main.tf SSH dynamic ingress to use allowed_ssh_cidr
    - Change the dynamic SSH ingress `cidr_blocks` from `[var.allowed_http_cidr]` to `[var.allowed_ssh_cidr]`
    - SSH access (when enabled) is restricted to the deployer's IP, not the HTTP CIDR
    - _Requirements: 32.22, 32.24_

  - [x] 107.3 Update terraform/deploy/variables.tf to remove allowed_http_cidr and add allowed_ssh_cidr
    - Remove the `allowed_http_cidr` variable entirely
    - Add a new `allowed_ssh_cidr` variable (type string, default `""`, description: CIDR for SSH access on port 22, only used when enable_ssh is true)
    - _Requirements: 23.6, 24.1, 32.22, 32.27_

- [x] 108. Update deploy script to remove unconditional IP detection and allowed_http_cidr
  - [x] 108.1 Update scripts/deploy.py main() function
    - Remove the unconditional `get_user_public_ip()` call and `allowed_http_cidr` construction from the default flow
    - Remove `allowed_http_cidr` from the `terraform_apply()` call arguments
    - _Requirements: 25.1, 25.2, 26.7_

  - [x] 108.2 Update scripts/deploy.py terraform_apply() function signature and body
    - Remove the `allowed_http_cidr` parameter from `terraform_apply()`
    - Remove `allowed_http_cidr` from the default `tf_vars` dict
    - Default `tf_vars` should only contain `attestable_ami_id`, `instance_type`, and `aws_region`
    - _Requirements: 25.1, 25.2_

  - [x] 108.3 Move IP detection into the --enable-ssh block in main()
    - When `args.enable_ssh` is True, call `get_user_public_ip()` to detect the user's IP
    - Construct `allowed_ssh_cidr` as `{detected_ip}/32`
    - Pass `allowed_ssh_cidr` to `terraform_apply()` via the enable_ssh code path
    - _Requirements: 32.27_

  - [x] 108.4 Update terraform_apply() to accept and pass allowed_ssh_cidr when SSH is enabled
    - Add optional `allowed_ssh_cidr` parameter (default `""`)
    - When `enable_ssh` is True, add `allowed_ssh_cidr` to `tf_vars`
    - _Requirements: 32.26, 32.27_

- [x] 109. Update cleanup script to remove allowed_http_cidr from terraform destroy
  - [x] 109.1 Update scripts/cleanup.py destroy_infrastructure() function
    - Remove `-var allowed_http_cidr=0.0.0.0/0` from the `terraform destroy` command
    - The destroy command should only pass `-var attestable_ami_id=dummy`
    - _Requirements: 29.5_

- [x] 110. Checkpoint - Ensure Terraform and script changes are consistent
  - Ensure all tests pass, ask the user if questions arise.

- [x] 111. Update existing tests for allowed_http_cidr to allowed_ssh_cidr migration
  - [x] 111.1 Update tests/property/test_deployment_infrastructure.py test_property_82
    - Property 82 now validates that port 8080 is open to `0.0.0.0/0` (the world) instead of using `var.allowed_http_cidr`
    - Assert `"0.0.0.0/0"` appears in the port 8080 ingress block
    - Remove assertion for `var.allowed_http_cidr` in the security group
    - Verify SSH dynamic ingress (if present) uses `var.allowed_ssh_cidr` instead of `var.allowed_http_cidr`
    - _Requirements: 23.2, 23.4, 23.5, 32.22_

  - [x] 111.2 Update tests/property/test_deployment_infrastructure.py test_deployment_variables_configuration
    - Remove assertion for `variable "allowed_http_cidr"` existence
    - Remove assertion that `allowed_http_cidr` has no default
    - Add assertion for `variable "allowed_ssh_cidr"` existence
    - Add assertion that `allowed_ssh_cidr` has a default value of `""`
    - _Requirements: 23.6, 24.1, 32.22_

  - [x] 111.3 Update tests/test_deployment_script_properties.py test_property_85
    - Property 85 now validates that IP detection only happens when `--enable-ssh` is provided
    - Update test to verify `allowed_ssh_cidr` is constructed as `{ip}/32` (not `allowed_http_cidr`)
    - Verify IP detection is tied to the SSH enable path, not the default deploy path
    - _Requirements: 32.27_

  - [x] 111.4 Update tests/test_debug_ssh_unit.py for terraform_apply signature change
    - Update test_without_ssh to expect 3 default vars (`attestable_ami_id`, `instance_type`, `aws_region`) instead of 4 (no `allowed_http_cidr`)
    - Update test_with_ssh to expect 6 vars including `allowed_ssh_cidr` instead of `allowed_http_cidr`
    - Update all `terraform_apply()` call sites to match the new function signature (no `allowed_http_cidr` positional arg)
    - _Requirements: 25.1, 32.26, 32.27_

  - [x] 111.5 Update tests/test_cleanup_unit.py for terraform destroy command change
    - Update TestDestroyInfrastructure tests that verify the terraform destroy command
    - Verify the destroy command no longer includes `-var allowed_http_cidr=0.0.0.0/0`
    - Verify the destroy command only passes `-var attestable_ami_id=dummy`
    - _Requirements: 29.5_

  - [x] 111.6 Update tests/test_cleanup_script_properties.py for terraform destroy command change
    - Update property test 90 (terraform destroy failure) if it verifies the exact destroy command args
    - Ensure mocked subprocess calls match the new destroy command without `allowed_http_cidr`
    - _Requirements: 29.5_

- [x] 112. Final checkpoint - Ensure all updated tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 113. Add wolfcrypt-py dependency and update KIWI config verification
  - [x] 113.1 Add wolfcrypt-py to pyproject.toml dependencies
    - Add `wolfcrypt-py` to the `dependencies` list in pyproject.toml alongside existing packages
    - _Requirements: 12.9, 36.4_

  - [x] 113.2 Update kiwi-descriptions/config.sh to verify wolfcrypt is importable
    - Add `python3.11 -c "import wolfcrypt" || { echo "ERROR: wolfcrypt not importable"; exit 1; }` after the existing critical package verification lines
    - _Requirements: 12.21_

- [x] 114. Rewrite EncryptionManager for PQ Hybrid KEM (X25519 + ML-KEM-768)
  - [x] 114.1 Rewrite EncryptionManager.__init__ to generate composite Server_Keypair
    - Replace the single X25519 keypair generation with composite keypair: generate an X25519 key pair using `cryptography` and an ML-KEM-768 key pair using `wolfcrypt.ciphers.MlKemPrivate.make_key(MlKemType.ML_KEM_768)`
    - Store both private keys (X25519 private key + ML-KEM-768 decapsulation key) in memory
    - Serialize the composite Server_Public_Key as length-prefixed concatenation: 4-byte big-endian length prefix + 32-byte X25519 public key + 4-byte big-endian length prefix + 1184-byte ML-KEM-768 encapsulation key
    - Log keypair generation at INFO level without logging private key or decapsulation key material
    - _Requirements: 36.1, 36.2, 36.3, 36.4, 36.5, 36.6_

  - [x] 114.2 Implement server_public_key property with length-prefixed serialization
    - Return the serialized composite Server_Public_Key (length-prefixed X25519 pubkey + ML-KEM-768 encapsulation key)
    - _Requirements: 36.6_

  - [x] 114.3 Implement server_public_key_fingerprint property
    - Compute and return SHA-256 digest of the serialized Server_Public_Key
    - This fingerprint is used in the attestation document's public_key field because the composite key exceeds the 1024-byte field limit
    - _Requirements: 39.1, 39.3_

  - [x] 114.4 Rewrite decrypt_request for PQ_Hybrid_KEM key derivation
    - Parse the Client_Public_Key (length-prefixed: 4-byte big-endian length + X25519 public key + 4-byte big-endian length + ML-KEM-768 ciphertext)
    - Perform X25519 ECDH using the server's X25519 private key and the client's X25519 public key
    - Perform ML-KEM-768 decapsulation using the server's ML-KEM-768 decapsulation key and the client's ML-KEM-768 ciphertext
    - Combine both shared secrets via HKDF-SHA256 with info label `b"pq-hybrid-shared-key"` to derive the 256-bit Shared_Key
    - Decrypt the encrypted payload using AES-256-GCM with the derived Shared_Key
    - Return (decrypted_dict, shared_key_bytes)
    - Raise ValueError on invalid Client_Public_Key (bad X25519 or ML-KEM-768 components) or decryption failure
    - _Requirements: 40.1, 40.2, 40.3, 40.4, 40.5, 40.6, 40.11_

  - [x] 114.5 Update HKDF info label from b"hpke-shared-key" to b"pq-hybrid-shared-key"
    - Change the `_HKDF_INFO` constant to `b"pq-hybrid-shared-key"` for domain separation
    - _Requirements: 40.11_

- [x] 115. Checkpoint - Ensure EncryptionManager compiles and basic tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 116. Update /attest endpoint for composite key response
  - [x] 116.1 Update /attest handler to return composite Server_Public_Key in JSON body
    - Return JSON with `attestation_document` (base64-encoded CBOR) and `server_public_key` (base64-encoded composite key)
    - Pass the SHA-256 fingerprint (from `encryption_manager.server_public_key_fingerprint`) as the `public_key` parameter to `generate_attestation`
    - _Requirements: 37.4, 37.6, 39.1_

  - [x] 116.2 Verify /attest attestation document includes fingerprint, not full key
    - Ensure the attestation document's public_key field contains the SHA-256 fingerprint of the composite Server_Public_Key, not the full key
    - Ensure the attestation document does NOT include user_data
    - _Requirements: 37.9, 37.10, 39.1, 39.3_

- [x] 117. Update /execute endpoint for new Client_Public_Key format
  - [x] 117.1 Update /execute handler to pass length-prefixed Client_Public_Key to decrypt_request
    - The client_public_key field now contains a length-prefixed concatenation of the client's X25519 public key + ML-KEM-768 ciphertext (instead of a raw 32-byte X25519 key)
    - No handler changes needed beyond ensuring the base64-decoded bytes are passed directly to `decrypt_request` (the new `decrypt_request` handles parsing internally)
    - _Requirements: 40.2, 40.3_

- [x] 118. Update encryption_test_helpers.py for PQ Hybrid KEM
  - [x] 118.1 Rewrite EncryptionTestContext for composite key exchange
    - Generate a client X25519 key pair and perform X25519 ECDH against the server's X25519 public key (extracted from the composite Server_Public_Key)
    - Perform ML-KEM-768 encapsulation against the server's ML-KEM-768 encapsulation key (extracted from the composite Server_Public_Key)
    - Combine both shared secrets via HKDF-SHA256 with info label `b"pq-hybrid-shared-key"` to derive the Shared_Key
    - Build the client_public_key as length-prefixed concatenation of the client's X25519 public key + ML-KEM-768 ciphertext
    - Update `_encrypt_with_shared_key` to use the new Shared_Key
    - _Requirements: 40.1, 40.2, 40.11_

  - [x] 118.2 Update make_encrypted_execute_request to use composite client_public_key
    - Ensure the `client_public_key` field in the outer JSON uses the length-prefixed composite value
    - _Requirements: 40.2_

- [x] 119. Update all existing encryption property tests for PQ Hybrid KEM
  - [x] 119.1 Update test_encryption_properties.py for PQ Hybrid round-trip
    - Update existing HPKE-based property tests to use PQ Hybrid KEM (X25519 + ML-KEM-768)
    - Update HKDF info label references from `b"hpke-shared-key"` to `b"pq-hybrid-shared-key"`
    - Ensure all property tests use the new composite key format
    - _Requirements: 40.1, 40.3, 40.4, 40.11_

  - [x] 119.2 Write property test for Server Keypair Consistency
    - **Property 122: Server Keypair Consistency**
    - For any two calls to `server_public_key` on the same EncryptionManager instance, the composite key and its SHA-256 fingerprint should be identical
    - **Validates: Requirements 36.3, 37.4, 37.6**

  - [x] 119.3 Write property test for Server Public Key Serialization Round-Trip
    - **Property 127: Server Public Key Serialization Round-Trip**
    - For any Server_Public_Key, serializing as length-prefixed concatenation, computing SHA-256 fingerprint, then deserializing and recomputing fingerprint should produce the same result; deserialized components should be usable for PQ_Hybrid_KEM key exchange
    - **Validates: Requirements 36.6, 37.6, 37.11, 39.3, 39.4**

  - [x] 119.4 Write property test for PQ Hybrid Encrypt-Decrypt Round-Trip for Execute
    - **Property 128: PQ Hybrid Encrypt-Decrypt Round-Trip for Execute**
    - For any valid execution request payload, client-side PQ_Hybrid_KEM encryption followed by server-side decryption should produce the original payload
    - **Validates: Requirements 40.1, 40.3, 40.4, 40.8, 40.11**

  - [x] 119.5 Write property test for Decryption Failure Returns HTTP 400
    - **Property 129: Decryption Failure Returns HTTP 400**
    - For any request with invalid encrypted payload (random bytes, wrong key, corrupted ciphertext), the server should return HTTP 400
    - **Validates: Requirements 40.5, 42.7**

  - [x] 119.6 Write property test for Execute Response Encryption Round-Trip
    - **Property 132: Execute Response Encryption Round-Trip**
    - For any /execute response payload, server encrypts with Shared_Key and client decrypts with same Shared_Key, producing original content
    - **Validates: Requirements 41.3, 42.1, 42.8**

  - [x] 119.7 Write property test for Output Request-Response Encryption Round-Trip
    - **Property 133: Output Request-Response Encryption Round-Trip**
    - For any /execution/{id}/output request and response, client encrypts request, server decrypts, processes, encrypts response, client decrypts — producing original content
    - **Validates: Requirements 41.4, 41.5, 42.2, 42.3, 42.4, 42.8**

- [x] 120. Update all existing encryption unit tests for PQ Hybrid KEM
  - [x] 120.1 Update test_encryption_unit.py for composite keypair
    - Update unit tests to verify composite keypair generation (X25519 + ML-KEM-768)
    - Test length-prefixed serialization/deserialization of composite keys
    - Test SHA-256 fingerprint computation of composite Server_Public_Key
    - Test PQ_Hybrid_KEM key derivation (X25519 ECDH + ML-KEM-768 decapsulation + HKDF-SHA256)
    - Test encrypt/decrypt round-trip with PQ hybrid derived Shared_Key
    - Test decryption failure with invalid Client_Public_Key (bad X25519 or ML-KEM-768 components)
    - Test Encryption_Context storage and cleanup (unchanged logic, but verify with PQ-derived keys)
    - _Requirements: 36.1, 36.4, 36.6, 39.1, 39.3, 40.1, 40.3, 40.4, 40.5, 40.6, 40.11_

  - [x] 120.2 Update test_encryption_context_lifecycle_properties.py for PQ Hybrid KEM
    - Update Encryption_Context lifecycle tests to use PQ hybrid derived Shared_Keys
    - **Property 131: Encryption Context Lifecycle**
    - **Validates: Requirements 41.1, 41.2, 41.6**

  - [x] 120.3 Update test_encryption_exemption_properties.py for PQ Hybrid KEM
    - Verify /attest, /health, /metrics still return unencrypted responses with PQ hybrid EncryptionManager
    - **Property 135: Encryption Exemption for Non-Context Endpoints**
    - **Validates: Requirements 43.1, 43.2, 43.3, 43.4**

- [x] 121. Update /attest endpoint property tests for PQ Hybrid KEM
  - [x] 121.1 Update test_attest_endpoint_properties.py for composite key response
    - Update tests to verify /attest returns JSON with `server_public_key` (base64-encoded composite key) and `attestation_document`
    - **Property 123: Attest Endpoint No Authentication**
    - **Property 124: Attest Attestation Contains Server Public Key Fingerprint**
    - **Property 125: Non-Attest Attestation Excludes Server Public Key**
    - **Property 126: Nonce Passthrough in Attestation**
    - **Property 136: Attest Attestation Excludes User Data**
    - **Validates: Requirements 37.2, 37.4, 37.5, 37.6, 37.9, 37.10, 39.1, 39.2, 39.3, 2.21**

- [x] 122. Update /execute and /output endpoint property tests for PQ Hybrid KEM
  - [x] 122.1 Update test_execute_encryption_properties.py for PQ Hybrid KEM
    - Update all /execute encryption property tests to use PQ Hybrid KEM key exchange
    - Update EncryptionTestContext usage to use composite keys
    - **Property 130: OIDC Token Extracted from Decrypted Body**
    - **Property 134: Missing Encryption Context Returns HTTP 400**
    - **Validates: Requirements 40.6, 40.9, 42.6, 2.1, 2.2**

- [x] 123. Update integration tests for PQ Hybrid KEM
  - [x] 123.1 Update integration tests for end-to-end PQ hybrid encrypted flow
    - Update integration tests to use PQ Hybrid KEM key exchange for /execute and /execution/{id}/output
    - Verify attestation documents, execution results, and output integrity through PQ hybrid encryption
    - _Requirements: 36.1, 37.1, 40.1, 41.1, 42.1, 42.4_

  - [x] 123.2 Update integration tests for error scenarios with PQ Hybrid KEM
    - Test decryption failure on /execute with wrong PQ hybrid key
    - Test invalid Client_Public_Key (bad ML-KEM-768 ciphertext)
    - Test missing Encryption_Context on /execution/{id}/output
    - _Requirements: 40.5, 40.6, 42.6, 42.7_

- [x] 124. Final checkpoint - Ensure all PQ Hybrid KEM tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 125. Move output attestation generation to run on every poll response
  - [x] 125.1 Update /execution/{id}/output handler in src/server.py to generate Output_Attestation_Document on every poll
    - Move the output attestation generation block OUTSIDE the `if output_data.complete:` conditional so it executes for every poll response where output_data is available
    - The canonical Script_Output concatenation (`stdout:{stdout}\nstderr:{stderr}\nexit_code:{exit_code}`) must be computed for every poll, not just when complete
    - When output_data is None (no output buffer yet), generate attestation using empty stdout, empty stderr, and None exit_code
    - The `else` branch (no output yet) must also generate an Output_Attestation_Document with the empty Script_Output
    - Ensure `output_attestation_document` field is present in every poll response regardless of execution status (running, completed, failed, timed_out)
    - Ensure `attestation_error` field is included when attestation generation fails, with `output_attestation_document` set to null
    - _Requirements: 6.4, 6.7, 6.8, 6.9, 6.11_

- [x] 126. Update property tests for output attestation on every poll
  - [x] 126.1 Update property test for Output Attestation Digest Integrity
    - **Property 44: Output Attestation Digest Integrity**
    - Update test to verify that the Output_Attestation_Document is generated on every poll response, not just when execution is complete
    - Test with execution statuses: running, completed, failed, timed_out
    - Verify the SHA-256 digest in user_data matches the current Script_Output at each poll regardless of status
    - **Validates: Requirements 6.7, 6.9**

  - [x] 126.2 Update property test for Output Attestation Base64 Encoding
    - **Property 45: Output Attestation Base64 Encoding**
    - Update test to verify that output_attestation_document is a valid base64-encoded string on every poll response, not just when complete
    - Test with execution statuses: running, completed, failed, timed_out
    - **Validates: Requirements 6.8**

  - [x] 126.3 Update property test for Output Attestation Failure Graceful Degradation
    - **Property 46: Output Attestation Failure Graceful Degradation**
    - Update test to verify graceful degradation on every poll response regardless of execution status
    - When attestation generation fails during a running/failed/timed_out poll, response must still include Script_Output with attestation_error field and output_attestation_document set to null
    - **Validates: Requirements 6.11**

- [x] 127. Update unit tests for output endpoint attestation on every poll
  - [x] 127.1 Update unit tests in tests/test_server_unit.py for output attestation
    - Add or update test cases to verify output_attestation_document is present in responses for running executions (not just completed)
    - Add test case for failed execution status returning output_attestation_document
    - Add test case for timed_out execution status returning output_attestation_document
    - Add test case for early poll (no output buffer yet) returning output_attestation_document with empty Script_Output digest
    - Verify attestation_error field is returned when attestation generation fails during non-complete polls
    - _Requirements: 6.4, 6.7, 6.8, 6.9, 6.11_

- [x] 128. Checkpoint - Ensure all output attestation every-poll tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 129. Implement streaming output capture via Log_Streaming_Thread
  - [x] 129.1 Replace batch log capture with streaming log capture in ScriptExecutor._execute_in_container
    - After `container.start()` and before `container.wait()`, start a Log_Streaming_Thread (daemon thread) that calls `container.logs(stream=True, follow=True, stdout=True, stderr=False)` for stdout and a separate call for stderr
    - Each streaming thread reads chunks from the Docker log stream and calls `self._output_collector.capture_output(execution_id, stream_name, chunk)` for each chunk received
    - The streaming threads run concurrently with `container.wait()` so that the Script_Executor can detect container completion while output is being streamed
    - Remove the call to `self._capture_container_logs(execution_id, container)` after `container.wait()` returns, since the streaming threads have already captured all output incrementally
    - Keep the `_capture_container_logs` call in the timeout/error path as a fallback only if the streaming thread did not run (defensive)
    - Ensure the streaming threads are daemon threads so they do not prevent server shutdown
    - Handle Docker API errors in the streaming threads gracefully by logging a warning
    - After `container.wait()` returns, join the streaming threads with a short timeout to ensure they have finished processing
    - _Requirements: 5.14, 44.1, 44.2, 44.3, 44.4, 44.5, 44.6, 44.7, 44.9, 44.10, 44.11_

  - [x] 129.2 Write property tests for streaming output capture
    - **Property 137: Incremental Output Availability During Execution**
    - Verify that for any script execution producing output, the Output_Collector contains partial output before the container exits
    - **Property 138: Log Streaming Thread Concurrent with Container Wait**
    - Verify that the Log_Streaming_Thread runs concurrently with container.wait() without blocking it
    - **Property 139: Log Streaming Thread Graceful Termination**
    - Verify that the streaming thread terminates when the container exits and captures output up to the point of timeout termination
    - **Property 140: No Batch Re-Capture After Streaming**
    - Verify that _capture_container_logs is NOT called after container.wait() when streaming was active
    - **Property 141: Log Streaming Thread Is Daemon Thread**
    - Verify that the streaming thread is a daemon thread
    - **Validates: Requirements 5.14, 44.1-44.11**

  - [x] 129.3 Write unit tests for streaming output capture
    - Test that streaming threads are started after container.start() and before container.wait()
    - Test that output chunks are fed to OutputCollector incrementally during execution
    - Test that _capture_container_logs is not called after successful streaming
    - Test that streaming threads handle Docker API errors gracefully
    - Test that streaming threads terminate when the container exits
    - Test that streaming threads capture partial output on timeout
    - Test that streaming threads are daemon threads
    - Mock Docker SDK container.logs(stream=True, follow=True) to return an iterator of chunks
    - _Requirements: 5.14, 44.1-44.11_

- [x] 130. Checkpoint - Ensure all streaming output tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 131. Implement OIDC repository claim binding on /execute
  - [x] 131.1 Add repository claim vs repository_url comparison in src/server.py
    - After OIDC validation succeeds on /execute, extract the `repository` claim from the validated token
    - Compare the `repository` claim against the `repository_url` field in the decrypted Execution_Request (extract owner/repo from the URL)
    - If mismatch, reject with HTTP 403 Forbidden and an error message indicating repository mismatch
    - Perform this check before repository cloning begins
    - _Requirements: 2.22, 2.23, 2.24_

  - [x] 131.2 Write property test for OIDC Repository Claim Binding
    - **Property 142: OIDC Repository Claim Binding**
    - **Validates: Requirements 2.22, 2.23, 2.24**

  - [x] 131.3 Write unit tests for repository claim binding
    - Test matching repository claim and repository_url (allowed)
    - Test mismatched repository claim and repository_url (403)
    - Test various URL formats (with/without .git suffix, trailing slashes)
    - _Requirements: 2.22, 2.23, 2.24_

- [x] 132. Implement extended OIDC claim restrictions (branch and protected ref)
  - [x] 132.1 Add ALLOWED_BRANCHES and REQUIRE_PROTECTED_REF config to src/config.py
    - Add optional `allowed_branches` field (comma-separated list, default None)
    - Add optional `require_protected_ref` field (boolean, default False)
    - Parse ALLOWED_BRANCHES env var into a list of branch patterns
    - Parse REQUIRE_PROTECTED_REF env var as boolean
    - _Requirements: 2.25, 2.28_

  - [x] 132.2 Add branch and ref validation to src/validation.py
    - When `allowed_branches` is configured, validate the `ref` claim against allowed branch patterns; reject with HTTP 403 if no match
    - When `require_protected_ref` is True, validate the `ref_protected` claim is "true"; reject with HTTP 403 if not
    - Skip branch validation when `allowed_branches` is not configured
    - Skip protected ref validation when `require_protected_ref` is not configured or False
    - _Requirements: 2.26, 2.27, 2.29, 2.30, 2.31, 2.32_

  - [x] 132.3 Write property test for Branch Restriction Enforcement
    - **Property 143: Branch Restriction Enforcement**
    - **Validates: Requirements 2.25, 2.26, 2.27, 2.31**

  - [x] 132.4 Write property test for Protected Ref Enforcement
    - **Property 144: Protected Ref Enforcement**
    - **Validates: Requirements 2.28, 2.29, 2.30, 2.32**

  - [x] 132.5 Write unit tests for branch and protected ref validation
    - Test with ALLOWED_BRANCHES configured: matching ref (allowed), non-matching ref (403)
    - Test with ALLOWED_BRANCHES not configured: any ref allowed
    - Test with REQUIRE_PROTECTED_REF=true: ref_protected="true" (allowed), ref_protected="false" (403)
    - Test with REQUIRE_PROTECTED_REF=false or unset: any ref_protected value allowed
    - _Requirements: 2.25, 2.26, 2.27, 2.28, 2.29, 2.30, 2.31, 2.32_

- [x] 133. Implement token stripping and .git directory removal
  - [x] 133.1 Update src/repository.py clone_repo to strip token and remove .git
    - After successful clone, run `git remote set-url origin https://github.com/{owner}/{repo}.git` to strip the token from .git/config
    - After token stripping, remove the `.git` directory entirely using `shutil.rmtree`
    - Perform both operations before the cloned repo is mounted into the Execution_Container
    - _Requirements: 3.10, 3.11, 3.12_

  - [x] 133.2 Write property test for Token Stripping and .git Removal
    - **Property 145: Token Stripping and .git Removal**
    - **Validates: Requirements 3.10, 3.11, 3.12**

  - [x] 133.3 Write unit tests for token stripping and .git removal
    - Test that git remote set-url is called after clone
    - Test that .git directory is removed after token stripping
    - Test ordering: clone → strip token → remove .git → mount
    - _Requirements: 3.10, 3.11, 3.12_

- [x] 134. Checkpoint - Ensure OIDC binding and repo security tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 135. Implement output buffer size limits
  - [x] 135.1 Add MAX_OUTPUT_SIZE_BYTES config to src/config.py
    - Add `max_output_size_bytes` field with a sensible default (e.g., 10MB)
    - Read from MAX_OUTPUT_SIZE_BYTES environment variable
    - _Requirements: 5.15_

  - [x] 135.2 Enforce output size limit in src/output_collector.py
    - Track combined stdout and stderr buffer size
    - When appending output would exceed MAX_OUTPUT_SIZE_BYTES, truncate the output
    - Mark the output record as truncated (add a `truncated` flag)
    - _Requirements: 5.15, 5.16_

  - [x] 135.3 Write property test for Output Buffer Size Enforcement
    - **Property 146: Output Buffer Size Enforcement**
    - **Validates: Requirements 5.15, 5.16**

  - [x] 135.4 Write unit tests for output buffer size limits
    - Test output within limit (not truncated)
    - Test output exceeding limit (truncated, flag set)
    - Test exact boundary condition
    - _Requirements: 5.15, 5.16_

- [ ] 136. Implement execution output repository binding
  - [ ] 136.1 Store repository claim in execution record
    - When creating an execution record in src/execution_manager.py, accept and store the `repository` claim from the validated OIDC_Token
    - Add `repository` field to the execution record data structure
    - _Requirements: 6.14_

  - [ ] 136.2 Verify repository claim on /output retrieval in src/server.py
    - On /execution/{id}/output, after OIDC validation, compare the `repository` claim from the token against the repository stored in the execution record
    - Reject with HTTP 403 if mismatch
    - _Requirements: 6.15, 6.16_

  - [ ] 136.3 Write property test for Execution Output Repository Binding
    - **Property 147: Execution Output Repository Binding**
    - **Validates: Requirements 6.14, 6.15, 6.16**

  - [ ] 136.4 Write unit tests for output repository binding
    - Test matching repository (allowed)
    - Test mismatched repository (403)
    - Test repository stored at creation and checked at retrieval
    - _Requirements: 6.14, 6.15, 6.16_

- [ ] 137. Implement contextvars-based logging
  - [ ] 137.1 Replace global mutable log context with contextvars.ContextVar in src/logging_config.py
    - Replace the process-global mutable dictionary with a `contextvars.ContextVar` for per-request/per-task log context
    - Update log formatter to read from the ContextVar
    - Ensure each request/task gets its own isolated context
    - _Requirements: 7.9, 7.10_

  - [ ] 137.2 Update src/server.py and src/script_executor.py to use contextvars
    - Replace all mutations of the global log context dict with ContextVar token-based set/reset
    - Ensure background tasks (script execution) copy the context or create a new one
    - _Requirements: 7.9, 7.10_

  - [ ] 137.3 Write property test for Contextvars Log Isolation
    - **Property 148: Contextvars Log Isolation**
    - **Validates: Requirements 7.9, 7.10**

  - [ ] 137.4 Write unit tests for contextvars logging
    - Test that concurrent requests have isolated log contexts
    - Test that background tasks do not leak context to other requests
    - _Requirements: 7.9, 7.10_

- [ ] 138. Implement concurrency enforcement
  - [ ] 138.1 Add atomic concurrency check in src/execution_manager.py
    - Before creating a new execution record, check the count of active executions (queued and running) against MAX_CONCURRENT_EXECUTIONS
    - Use a lock or atomic operation to prevent race conditions
    - Return a rejection indicator when at capacity
    - _Requirements: 8.11, 8.12_

  - [ ] 138.2 Wire concurrency check into /execute endpoint in src/server.py
    - Before creating the execution record, call the concurrency check
    - Return HTTP 503 Service Unavailable when at capacity
    - _Requirements: 8.11, 8.12_

  - [ ] 138.3 Write property test for Concurrency Enforcement
    - **Property 149: Concurrency Enforcement**
    - **Validates: Requirements 8.11, 8.12**

  - [ ] 138.4 Write unit tests for concurrency enforcement
    - Test that requests are accepted when below capacity
    - Test that requests are rejected with 503 when at capacity
    - Test atomicity under concurrent request simulation
    - _Requirements: 8.11, 8.12_

- [ ] 139. Implement script size enforcement
  - [ ] 139.1 Add file size check after clone in src/server.py
    - After cloning the repository and before execution, check the script file size against MAX_SCRIPT_SIZE_BYTES
    - Return HTTP 413 Payload Too Large if exceeded
    - _Requirements: 8.13, 8.14_

  - [ ] 139.2 Write property test for Script Size Enforcement
    - **Property 150: Script Size Enforcement**
    - **Validates: Requirements 8.13, 8.14**

  - [ ] 139.3 Write unit tests for script size enforcement
    - Test script within size limit (allowed)
    - Test script exceeding size limit (413)
    - _Requirements: 8.13, 8.14_

- [ ] 140. Implement periodic cleanup scheduling
  - [ ] 140.1 Schedule periodic cleanup_expired invocation in src/server.py or src/main.py
    - Use asyncio or a background task to periodically invoke `cleanup_expired` on the ExecutionManager
    - Cleanup should call `remove_output` on the OutputCollector and `remove_encryption_context` on the EncryptionManager for each expired record
    - _Requirements: 8.15, 8.16_

  - [ ] 140.2 Write property test for Periodic Cleanup Scheduling
    - **Property 151: Periodic Cleanup Scheduling**
    - **Validates: Requirements 8.15, 8.16**

  - [ ] 140.3 Write unit tests for periodic cleanup
    - Test that cleanup_expired is invoked periodically
    - Test that expired records trigger remove_output and remove_encryption_context
    - _Requirements: 8.15, 8.16_

- [ ] 141. Implement capability dropping for execution containers
  - [ ] 141.1 Add cap_drop=["ALL"] to container creation in src/script_executor.py
    - Add `cap_drop=["ALL"]` to the Docker container creation call
    - Do not add back any capabilities
    - _Requirements: 8.17, 8.18_

  - [ ] 141.2 Write property test for Capability Dropping
    - **Property 152: Capability Dropping**
    - **Validates: Requirements 8.17, 8.18**

  - [ ] 141.3 Write unit tests for capability dropping
    - Test that container is created with cap_drop=["ALL"]
    - Test that no cap_add is present
    - _Requirements: 8.17, 8.18_

- [ ] 142. Checkpoint - Ensure runtime security hardening tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 143. Harden health endpoint and remove /metrics
  - [ ] 143.1 Apply rate limiting to /health endpoint in src/server.py
    - Remove /health from rate limiting exemptions
    - Ensure /health is subject to the same per-IP rate limiting as other endpoints
    - _Requirements: 10.4_

  - [ ] 143.2 Simplify /health response
    - Return only a simple healthy/unhealthy status
    - Remove Docker availability, disk space, and active execution count from the response
    - _Requirements: 10.5_

  - [ ] 143.3 Remove GET /metrics endpoint entirely
    - Remove the /metrics route from src/server.py
    - Remove metrics tracking code (total executions, success/failure counts, average duration)
    - _Requirements: 10.5 (removed)_

  - [ ] 143.4 Write property test for Health Endpoint Rate Limiting
    - **Property 153: Health Endpoint Rate Limiting**
    - **Validates: Requirements 10.4**

  - [ ] 143.5 Update existing health and metrics tests
    - Update tests to reflect simplified /health response
    - Remove /metrics endpoint tests
    - Update Property 58, 59, 60 tests as needed
    - _Requirements: 10.4, 10.5_

- [ ] 144. Implement anti-replay nonce cache
  - [ ] 144.1 Create NonceCache class in src/server.py or a new src/nonce_cache.py
    - Implement `NonceCache` with configurable TTL (NONCE_CACHE_TTL_SECONDS)
    - Implement `check_and_store(nonce)` returning True if new, False if duplicate
    - Implement `cleanup_expired()` to purge expired entries
    - Thread-safe for concurrent access
    - _Requirements: 45.1, 45.4_

  - [ ] 144.2 Add NONCE_CACHE_TTL_SECONDS config to src/config.py
    - Add `nonce_cache_ttl_seconds` field with default matching OIDC token lifetime
    - _Requirements: 45.4_

  - [ ] 144.3 Wire nonce validation into /execute and /output endpoints
    - After decryption, extract nonce from decrypted payload
    - Check nonce against NonceCache; reject with HTTP 400 if duplicate
    - _Requirements: 45.2, 45.3, 45.5_

  - [ ] 144.4 Write property test for Anti-Replay Nonce Validation
    - **Property 154: Anti-Replay Nonce Validation**
    - **Validates: Requirements 45.1, 45.2, 45.3, 45.4, 45.5**

  - [ ] 144.5 Write unit tests for nonce cache
    - Test new nonce accepted
    - Test duplicate nonce rejected with 400
    - Test nonce expiry after TTL
    - Test concurrent nonce checks
    - _Requirements: 45.1, 45.2, 45.3, 45.4, 45.5_

- [ ] 145. Apply rate limiting to /attest endpoint
  - [ ] 145.1 Remove /attest from rate limiting exemptions in src/server.py
    - Remove /attest from the list of paths exempt from rate limiting
    - Ensure /attest is subject to per-IP rate limiting
    - _Requirements: 37.12, 37.13_

  - [ ] 145.2 Write property test for Attest Endpoint Rate Limiting
    - **Property 155: Attest Endpoint Rate Limiting**
    - **Validates: Requirements 37.12, 37.13**

  - [ ] 145.3 Write unit tests for /attest rate limiting
    - Test that /attest requests are rate limited per IP
    - Test that exceeding rate limit returns 429
    - _Requirements: 37.12, 37.13_

- [ ] 146. Implement container image digest pinning
  - [ ] 146.1 Add CONTAINER_IMAGE_DIGEST config to src/config.py
    - Add optional `container_image_digest` field (default None)
    - Read from CONTAINER_IMAGE_DIGEST environment variable
    - _Requirements: 34.7_

  - [ ] 146.2 Verify pulled image digest in src/script_executor.py pull_container_image
    - After pulling the image, retrieve its digest
    - If CONTAINER_IMAGE_DIGEST is configured, compare against the pulled image digest
    - If mismatch, raise an error to prevent server startup
    - Support digest-pinned image references (e.g., `image@sha256:...`)
    - _Requirements: 34.8, 34.9, 34.10_

  - [ ] 146.3 Write property test for Container Image Digest Verification
    - **Property 156: Container Image Digest Verification**
    - **Validates: Requirements 34.7, 34.8, 34.9, 34.10**

  - [ ] 146.4 Write unit tests for container image digest pinning
    - Test matching digest (startup succeeds)
    - Test mismatched digest (startup fails)
    - Test no digest configured (skip verification)
    - _Requirements: 34.7, 34.8, 34.9, 34.10_

- [ ] 147. Checkpoint - Ensure all runtime security hardening tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 148. Implement artifact ref input validation in build-ami.py
  - [ ] 148.1 Add strict regex validation for artifact_ref in scripts/build-ami.py
    - Validate artifact_ref against `^ghcr\.io/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+:[a-zA-Z0-9._-]+$`
    - Reject and terminate before any shell interpolation if invalid
    - _Requirements: 15.15, 15.16_

  - [ ] 148.2 Write property test for Artifact Ref Validation
    - **Property 157: Artifact Ref Validation**
    - **Validates: Requirements 15.15, 15.16**

  - [ ] 148.3 Write unit tests for artifact ref validation
    - Test valid artifact refs (accepted)
    - Test refs with shell metacharacters (rejected)
    - Test refs with spaces, semicolons, pipes (rejected)
    - _Requirements: 15.15, 15.16_

- [ ] 149. Implement ORAS checksum verification and coldsnap version pinning
  - [ ] 149.1 Add SHA-256 checksum verification for ORAS download in scripts/build-ami.py
    - After downloading the ORAS tar.gz, compute its SHA-256 checksum
    - Compare against a known expected checksum hardcoded in the script
    - Fail with an integrity verification error if mismatch
    - _Requirements: 17.13, 17.14_

  - [ ] 149.2 Pin coldsnap clone to a specific git tag or commit
    - Change the coldsnap `git clone` to checkout a specific tag or commit hash
    - _Requirements: 17.15_

  - [ ] 149.3 Add trust assumption documentation comments
    - Add code comments documenting trust assumptions for rustup installer and GitHub CLI package repository
    - _Requirements: 17.16_

  - [ ] 149.4 Write property test for ORAS Checksum Verification
    - **Property 158: ORAS Checksum Verification**
    - **Validates: Requirements 17.13, 17.14**

  - [ ] 149.5 Write property test for Coldsnap Pinned Version
    - **Property 159: Coldsnap Pinned Version**
    - **Validates: Requirements 17.15**

- [ ] 150. Implement secure SSH key deletion
  - [ ] 150.1 Update cleanup_infrastructure in scripts/build-ami.py
    - Before unlinking the SSH key file, overwrite it with random bytes (e.g., `os.urandom(len)`)
    - Then unlink the file
    - Add a comment documenting Terraform state sensitivity
    - _Requirements: 21.15, 21.16_

  - [ ] 150.2 Write property test for Secure SSH Key Deletion
    - **Property 160: Secure SSH Key Deletion**
    - **Validates: Requirements 21.15**

- [ ] 151. Checkpoint - Ensure build security hardening tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 152. Implement debug image annotation
  - [ ] 152.1 Add debug annotation to ORAS push in .github/workflows/build-attestable-image.yml
    - When building with --enable-ssh, add `--annotation "debug=true"` to the ORAS push command
    - When building without --enable-ssh, add `--annotation "debug=false"` to the ORAS push command
    - _Requirements: 45.1, 45.2_

  - [ ] 152.2 Add --allow-debug CLI flag to scripts/build-ami.py
    - Add `--allow-debug` as a boolean flag (store_true)
    - After downloading the artifact, check for the `debug` annotation using `oras manifest fetch`
    - If `debug=true` and `--allow-debug` is not provided, refuse to build and terminate with error
    - If `debug=true` and `--allow-debug` is provided, log a prominent warning and proceed
    - _Requirements: 45.3, 45.4, 45.5_

  - [ ] 152.3 Write property test for Debug Image Annotation
    - **Property 161: Debug Image Annotation**
    - **Validates: Requirements 46.1, 46.2**

  - [ ] 152.4 Write property test for Debug Image Production Gate
    - **Property 162: Debug Image Production Gate**
    - **Validates: Requirements 46.3, 46.4, 46.5**

- [ ] 153. Implement artifact provenance workflow verification
  - [ ] 153.1 Add --expected-workflow CLI arg to scripts/build-ami.py
    - Add optional `--expected-workflow` argument specifying the expected workflow file path
    - When provided, after downloading the attestation bundle, extract the workflow identity and compare
    - If mismatch, terminate with an error indicating workflow mismatch
    - When not provided, skip workflow identity verification
    - _Requirements: 46.1, 46.2, 46.3, 46.4_

  - [ ] 153.2 Write property test for Artifact Provenance Workflow Verification
    - **Property 163: Artifact Provenance Workflow Verification**
    - **Validates: Requirements 47.1, 47.2, 47.3, 47.4**

- [ ] 154. Implement Docker daemon security configuration
  - [ ] 154.1 Add daemon.json to KIWI image
    - Create `kiwi-descriptions/root/etc/docker/daemon.json` with user-namespace remapping and restrictive seccomp profile
    - Include `"userns-remap": "default"`, `"no-new-privileges": true`, and seccomp profile reference
    - Add code comments documenting the expected Docker daemon security configuration
    - _Requirements: 47.1, 47.2, 47.3, 47.4_

  - [ ] 154.2 Write property test for Docker Daemon Security Configuration
    - **Property 164: Docker Daemon Security Configuration**
    - **Validates: Requirements 48.1, 48.2, 48.3**

- [ ] 155. Implement systemd service hardening
  - [ ] 155.1 Update kiwi-descriptions/root/etc/systemd/system/github-actions-remote-executor.service
    - Set `NoNewPrivileges=true`
    - Set `PrivateTmp=true`
    - Set `ProtectSystem=strict`
    - Set `ProtectHome=true`
    - Set `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK`
    - Set `ReadWritePaths=/tmp/github-actions-remote-executor /var/run/docker.sock`
    - _Requirements: 48.1, 48.2, 48.3, 48.4, 48.5, 48.6_

  - [ ] 155.2 Write property test for Systemd Service Hardening
    - **Property 165: Systemd Service Hardening**
    - **Validates: Requirements 49.1, 49.2, 49.3, 49.4, 49.5, 49.6**

- [ ] 156. Implement AMI build IAM permission scoping
  - [ ] 156.1 Update terraform/build-ami/iam.tf to scope permissions
    - Replace `Resource = "*"` with resource ARN patterns scoped to the specific region and account
    - Add `aws:RequestedRegion` or resource ARN region scoping condition keys
    - Add `aws:ResourceAccount` condition to restrict operations to the current account
    - _Requirements: 49.1, 49.2, 49.3, 49.4_

  - [ ] 156.2 Write property test for AMI Build IAM Permission Scoping
    - **Property 166: AMI Build IAM Permission Scoping**
    - **Validates: Requirements 50.1, 50.2, 50.3, 50.4**

- [ ] 157. Implement build environment pinning
  - [ ] 157.1 Pin GitHub Actions runner to specific Ubuntu version
    - Change `ubuntu-latest` to a specific version (e.g., `ubuntu-24.04`) in `.github/workflows/build-attestable-image.yml`
    - _Requirements: 11.9_

  - [ ] 157.2 Pin Build_Instance AMI to specific version
    - Change `most_recent = true` in `terraform/build-ami/data.tf` to use a specific AMI ID or name filter with a specific version
    - _Requirements: 11.10_

  - [ ] 157.3 Add builder package documentation comments
    - Add comments to the Dockerfile about DNF package version limitations and why exact NEVRA pinning is not used
    - _Requirements: 11.11, 11.12_

  - [ ] 157.4 Write property test for Build Environment Pinning
    - **Property 167: Build Environment Pinning**
    - **Validates: Requirements 11.9, 11.10**

- [ ] 158. Checkpoint - Ensure all build/deploy security hardening tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 159. Write integration tests for security hardening changes
  - [ ] 159.1 Write integration tests for OIDC repository binding end-to-end
    - Test /execute with matching repository claim and URL (success)
    - Test /execute with mismatched repository claim and URL (403)
    - Test /output with matching repository claim and execution record (success)
    - Test /output with mismatched repository claim and execution record (403)
    - _Requirements: 2.22, 2.23, 2.24, 6.14, 6.15, 6.16_

  - [ ] 159.2 Write integration tests for anti-replay nonce validation
    - Test /execute with unique nonce (success)
    - Test /execute with duplicate nonce (400)
    - Test /output with duplicate nonce (400)
    - _Requirements: 44.1, 44.2, 44.3, 44.5_

  - [ ] 159.3 Write integration tests for concurrency enforcement
    - Test that MAX_CONCURRENT_EXECUTIONS is enforced with 503
    - _Requirements: 8.11, 8.12_

- [ ] 160. Final checkpoint - Ensure all security hardening tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests validate the 167 correctness properties from the design document
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
- Docker container execution replaces direct subprocess execution: each script runs in an ephemeral container with memory limits, CPU limits, read-only filesystem, no network, no privilege escalation, and non-root user
- Docker SDK (`docker` Python package) manages container lifecycle: create, run, capture output, remove, and verify removal
- Container naming convention uses `gare-exec-{execution_id}` prefix for identification and dangling cleanup
- OIDC authentication (tasks 58-65) adds GitHub Actions OIDC JWT validation for request authentication
- PyJWT[crypto] is used for JWT decoding and JWKS-based signature verification
- OIDC tokens are validated for signature (JWKS), issuer, audience, repository, and expiration claims
- Protected endpoints (/execute, /execution/{id}/output) require Bearer OIDC tokens; /health remains unauthenticated
- Docker daemon provisioning (tasks 74-75) adds the docker package to the KIWI image and enables the docker service so the Script_Executor can manage Execution_Containers at runtime
- Git package provisioning (task 90) adds the git package to the KIWI image so the Repository_Client can clone repositories at runtime using git commands
- Container image pre-pull (tasks 76-79) originally baked the configured Container_Image into the KIWI image during build; tasks 80-84 reverse this by removing the build-time pre-pull code and implementing server-startup pull instead — the GHA_Server now pulls the Container_Image from the registry at startup before accepting requests
- All 167 properties should be tested with hypothesis library (minimum 100 iterations each)
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
- Target instance has port 8080 open to the world (0.0.0.0/0); authentication is handled at the application layer via HPKE + OIDC
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
- HPKE encrypted communication (tasks 91-104) adds end-to-end encryption for /execute and /execution/{id}/output using Hybrid Public Key Encryption (RFC 9180)
- PQ Hybrid KEM migration (tasks 113-124) replaces HPKE with PQ_Hybrid_KEM (X25519 + ML-KEM-768) for post-quantum resistance; the `wolfcrypt-py` package (via `wolfcrypt.ciphers` module: `MlKemType`, `MlKemPrivate`, `MlKemPublic`) provides FIPS 203 ML-KEM-768 key generation, encapsulation, and decapsulation
- Security hardening (tasks 125-154) implements fixes from the security review: OIDC repository claim binding, extended OIDC claim restrictions, token stripping/.git removal, output buffer limits, execution output repository binding, contextvars logging, concurrency enforcement, script size enforcement, periodic cleanup, capability dropping, health endpoint hardening, /metrics removal, anti-replay nonce cache, /attest rate limiting, container image digest pinning, artifact ref validation, ORAS checksum verification, coldsnap pinning, secure SSH key deletion, debug image annotation, artifact provenance workflow verification, Docker daemon security configuration, systemd service hardening, AMI build IAM permission scoping, and build environment pinning
- The `cryptography` library (already included via PyJWT[crypto]) provides X25519 key generation support; `wolfcrypt-py` provides ML-KEM-768 support
- Server_Keypair is a composite key (X25519 + ML-KEM-768) generated once at startup and held in memory; never persisted to disk
- Server_Public_Key is serialized as length-prefixed concatenation (4-byte big-endian length + component bytes) of X25519 public key (32 bytes) + ML-KEM-768 encapsulation key (1184 bytes)
- SHA-256 fingerprint of the composite Server_Public_Key is used in attestation documents because the composite key exceeds the 1024-byte public_key field limit
- /attest is the only endpoint that includes Server_Public_Key in attestation documents; /execute and /output attestations exclude it
- /attest attestation documents do NOT include user_data — only public_key and optional nonce are included
- OIDC tokens move from Authorization header to encrypted request body `oidc_token` field on /execute and /execution/{id}/output
- /execution/{id}/output changes from GET to POST to support encrypted request bodies
- Encryption_Context (Shared_Key per execution_id) is stored in memory and cleaned up with execution records
- /attest, /health, and /metrics remain unencrypted plain JSON endpoints
- Output attestation every-poll change (tasks 125-128): Output_Attestation_Document is now generated on EVERY /execution/{id}/output poll response, not just when execution is complete; the SHA-256 digest covers the current Script_Output (stdout + stderr + exit_code) at the time of each poll regardless of execution status (running, completed, failed, timed_out)
- Streaming output capture (tasks 129-130): Replaces the batch log capture pattern (capture all output after container.wait() returns) with a Log_Streaming_Thread that uses `container.logs(stream=True, follow=True)` to incrementally feed output chunks to the Output_Collector during execution; this ensures polling clients observe partial output while the script is still running rather than seeing empty output until the container exits
