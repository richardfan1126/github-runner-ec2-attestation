# Implementation Plan: GitHub Actions Remote Executor

## Overview

This implementation plan breaks down the GitHub Actions Remote Executor into discrete coding tasks. The system is an HTTP server running on AWS Nitro-based EC2 instances that executes scripts from GitHub repositories with cryptographic attestation. The implementation follows an asynchronous execution model with polling-based output retrieval.

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
    - Implement verify_nsm_available method to check NSM device at `/usr/bin/nitro-tpm-attest`
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
    - Test with mocked NSM device
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
    - Verify NSM device availability
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
    - Verify raw disk image exists using ls ~/artifacts/build-output/*.raw
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
    - Find raw disk image filename in ~/artifacts/build-output using ls *.raw
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
    - Use ISO 8601 timestamp with UTC timezone
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

- [ ] 30. Final checkpoint - Ensure all build tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests validate the 80 correctness properties from the design document
- The runtime implementation (tasks 1-16) uses Python with FastAPI for the HTTP server
- The build implementation (tasks 17-30) uses GitHub Actions, KIWI NG, ORAS, Terraform, and Python
- Python dependencies are separated into two configurations:
  - pyproject.toml: Remote executor service dependencies (fastapi, uvicorn, requests, hypothesis, pytest, pytest-asyncio, httpx)
  - scripts/pyproject.toml: Build/deployment script dependencies (boto3, paramiko)
  - The remote executor does NOT use boto3 - it only runs on the EC2 instance and doesn't interact with AWS APIs
  - boto3 is ONLY used by build/deployment scripts (build-ami.py, cleanup.py, deploy.py) that run outside the KIWI image
  - When building the KIWI image, only dependencies from pyproject.toml are installed via config.sh script
  - The KIWI config.sh phase has no network access; dependency wheels are pre-downloaded by build-kiwi-image.sh (which has network) and installed offline using pip3 install --no-index --find-links
  - build-kiwi-image.sh extracts the dependency list dynamically from pyproject.toml using tomllib — package names are never hardcoded in the build script
  - No uv package manager is needed inside the KIWI image
  - Dependency installation occurs during KIWI image build phase, before image finalization
- AWS Nitro attestation requires running on a Nitro-based EC2 instance
- Scripts execute as root with full system privileges
- All 80 properties should be tested with hypothesis library (minimum 100 iterations each)
- Checkpoints ensure incremental validation throughout implementation
- Build tasks (17-30) can be implemented independently from runtime tasks (1-16)
- AMI build process uses Terraform to provision temporary EC2 infrastructure with complete VPC/networking setup
- Build instance uses Amazon Linux 2023 with IMDSv2 enforcement
- Signature verification is mandatory before AMI creation - no bypass mechanism
- Tool installation includes specific versions: ORAS 1.3.0, Rust via rustup, GitHub CLI via dnf, coldsnap from source
- Coldsnap installed to /home/ec2-user/.cargo/bin/coldsnap (full path required for execution)
- SSH connectivity uses paramiko with keepalive (30s intervals) and retries (10 attempts, 30s delay)
- Infrastructure cleanup guaranteed via finally block, executes even on build failure
- Terraform state isolated per build for concurrent build support
