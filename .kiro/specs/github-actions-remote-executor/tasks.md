# Implementation Plan: GitHub Actions Remote Executor

## Overview

This implementation plan breaks down the GitHub Actions Remote Executor into discrete coding tasks. The system is an HTTP server running on AWS Nitro-based EC2 instances that executes scripts from GitHub repositories with cryptographic attestation. The implementation follows an asynchronous execution model with polling-based output retrieval.

## Tasks

- [x] 1. Set up project structure and core configuration
  - Create Python project structure with src/ directory
  - Set up requirements.txt with dependencies (Flask/FastAPI, boto3, requests, hypothesis)
  - Create configuration module for loading environment variables
  - Define ServerConfig dataclass with all configuration parameters
  - Implement configuration validation on startup
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8_

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
    - Implement SandboxConfig dataclass
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

  - [ ]* 4.2 Write property tests for repository client
    - **Property 8: GitHub Authentication**
    - **Property 9: Exact Commit File Retrieval**
    - **Property 10: Authentication Failure Response**
    - **Property 11: Repository Not Found Response**
    - **Property 12: Commit Not Found Response**
    - **Property 13: File Not Found Response**
    - **Property 14: Temporary File Storage**
    - **Validates: Requirements 3.1-3.7**

  - [ ]* 4.3 Write unit tests for repository client
    - Test with mocked GitHub API responses
    - Test error handling for various GitHub API errors
    - Test file size validation
    - _Requirements: 3.1-3.7, 8.4_

- [ ] 5. Implement AWS Nitro attestation generator
  - [ ] 5.1 Create AttestationGenerator class
    - Implement verify_nsm_available method to check NSM device
    - Implement generate_attestation method using NSM device
    - Include repository URL, commit hash, script path, timestamp in attestation
    - Sign attestation document using NSM cryptographic capabilities
    - Encode attestation in CBOR format
    - Handle attestation generation failures
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.10_

  - [ ]* 5.2 Write property tests for attestation generator
    - **Property 15: Attestation Document Generation**
    - **Property 16: Attestation Document Completeness**
    - **Property 17: Attestation Document Signing**
    - **Property 20: Attestation Failure Response**
    - **Validates: Requirements 4.1-4.6, 4.10**

  - [ ]* 5.3 Write unit tests for attestation generator
    - Test with mocked NSM device
    - Test attestation document structure
    - Test signature verification
    - _Requirements: 4.1-4.6_

- [ ] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Implement execution management
  - [ ] 7.1 Create ExecutionManager class
    - Implement create_execution method with UUID generation
    - Implement get_execution method for retrieval by ID
    - Implement update_status method for status transitions
    - Implement cleanup_expired method for retention policy
    - Use thread-safe data structure for execution storage
    - Track execution lifecycle: queued → running → (completed|failed|timed_out)
    - _Requirements: 4.7, 5.9, 6.10_

  - [ ]* 7.2 Write property tests for execution manager
    - **Property 18: Execution ID Uniqueness**
    - **Property 29: Execution Status Tracking**
    - **Property 36: Output Retention Period**
    - **Validates: Requirements 4.7, 5.9, 6.10**

  - [ ]* 7.3 Write unit tests for execution manager
    - Test concurrent access to execution store
    - Test status transition validation
    - Test cleanup of expired executions
    - _Requirements: 4.7, 5.9, 6.10_

- [ ] 8. Implement output collection
  - [ ] 8.1 Create OutputCollector class
    - Implement capture_output method for streaming output
    - Implement get_output method with offset support
    - Store stdout and stderr separately
    - Support incremental output retrieval
    - Implement thread-safe buffered writes
    - _Requirements: 5.3, 5.4, 6.3, 6.4, 6.5, 6.6_

  - [ ]* 8.2 Write property tests for output collector
    - **Property 23: Output Stream Capture**
    - **Property 24: Output Storage Round-Trip**
    - **Property 31: Output Structure Separation**
    - **Property 32: Offset-Based Output Retrieval**
    - **Validates: Requirements 5.3, 5.4, 6.3, 6.4, 6.5, 6.6**

  - [ ]* 8.3 Write unit tests for output collector
    - Test large output handling
    - Test concurrent output capture
    - Test offset edge cases (0, beyond end, negative)
    - _Requirements: 5.3, 5.4, 6.3, 6.4, 6.5, 6.6_

- [ ] 9. Implement sandbox environment and script executor
  - [ ] 9.1 Create SandboxEnvironment class
    - Implement isolated process creation with minimal privileges
    - Implement filesystem access restrictions using chroot or containers
    - Implement network access restrictions using network namespaces
    - Implement resource limits (memory, CPU)
    - Create temporary execution directories
    - _Requirements: 8.1, 8.2, 8.3_

  - [ ] 9.2 Create ScriptExecutor class
    - Implement execute_async method for background execution
    - Capture stdout and stderr streams
    - Implement execution timeout with process termination
    - Capture exit codes
    - Integrate with OutputCollector for stream capture
    - Update ExecutionManager status throughout lifecycle
    - Clean up temporary files after execution
    - _Requirements: 5.1, 5.2, 5.3, 5.5, 5.6, 5.7, 8.6_

  - [ ]* 9.3 Write property tests for script executor
    - **Property 21: Asynchronous Script Execution**
    - **Property 22: Process Isolation**
    - **Property 25: Execution Timeout Configuration**
    - **Property 26: Timeout Termination**
    - **Property 27: Exit Code Capture**
    - **Property 28: Temporary File Cleanup**
    - **Property 44: Minimal Privilege Execution**
    - **Property 45: Network Access Restriction**
    - **Property 46: Filesystem Access Restriction**
    - **Validates: Requirements 5.1, 5.2, 5.5, 5.6, 5.7, 8.1, 8.2, 8.3, 8.6**

  - [ ]* 9.4 Write unit tests for script executor
    - Test script execution with known output
    - Test timeout scenarios
    - Test cleanup on success and failure
    - Test privilege restrictions
    - _Requirements: 5.1-5.7, 8.1-8.3, 8.6_

- [ ] 10. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 11. Implement HTTP server and request handlers
  - [ ] 11.1 Create HTTP server with Flask or FastAPI
    - Set up HTTP server listening on configured port
    - Implement request routing
    - Implement rate limiting middleware per source IP
    - Implement request logging middleware (exclude tokens)
    - Implement error handling middleware
    - _Requirements: 1.1, 1.2, 1.5, 7.2, 8.7_

  - [ ] 11.2 Implement POST /execute endpoint
    - Parse request body into ExecutionRequest
    - Validate request using RequestValidator
    - Authenticate and fetch file using RepositoryClient
    - Validate script file size
    - Generate attestation using AttestationGenerator
    - Create execution record using ExecutionManager
    - Initiate async execution using ScriptExecutor
    - Return immediate response with execution_id and attestation_document
    - Handle all error cases with appropriate HTTP status codes
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 4.8, 4.9, 8.4, 8.5_

  - [ ] 11.3 Implement GET /execution/{execution_id}/output endpoint
    - Parse execution_id from URL path
    - Parse optional offset query parameter
    - Retrieve execution record using ExecutionManager
    - Retrieve output using OutputCollector with offset
    - Return status, stdout, stderr, offsets, complete flag, exit_code
    - Handle non-existent execution IDs with 404
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9_

  - [ ]* 11.4 Write property tests for HTTP endpoints
    - **Property 3: Concurrent Request Handling**
    - **Property 19: Immediate Response with Attestation**
    - **Property 30: Output Endpoint Status Return**
    - **Property 33: Completion Exit Code Inclusion**
    - **Property 34: Completion Flag Accuracy**
    - **Property 35: Invalid Execution ID Response**
    - **Property 47: Script Size Validation**
    - **Property 48: Oversized Script Rejection**
    - **Property 49: Rate Limiting per IP**
    - **Validates: Requirements 1.3, 1.4, 1.5, 4.8, 4.9, 6.2, 6.7, 6.8, 6.9, 8.4, 8.5, 8.7**

  - [ ]* 11.5 Write unit tests for HTTP endpoints
    - Test complete request/response flow
    - Test error responses for each error case
    - Test rate limiting behavior
    - Test concurrent request handling
    - _Requirements: 1.1-1.5, 4.8-4.10, 6.1-6.9, 8.4, 8.5, 8.7_

- [ ] 12. Implement health and metrics endpoints
  - [ ] 12.1 Create GET /health endpoint
    - Return HTTP 200 when operational
    - Include attestation capability status
    - Include disk space availability
    - Include active executions count
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [ ] 12.2 Create GET /metrics endpoint
    - Track total executions count
    - Track successful executions count
    - Track failed executions count
    - Track average execution duration
    - Include active executions count
    - _Requirements: 10.5, 10.6_

  - [ ]* 12.3 Write property tests for health and metrics
    - **Property 58: Health Check Attestation Status**
    - **Property 59: Health Check Disk Space**
    - **Property 60: Execution Metrics Tracking**
    - **Validates: Requirements 10.3, 10.4, 10.6**

  - [ ]* 12.4 Write unit tests for health and metrics
    - Test health endpoint response structure
    - Test metrics accuracy
    - Test metrics under concurrent executions
    - _Requirements: 10.1-10.6_

- [ ] 13. Implement logging and error handling
  - [ ] 13.1 Create logging infrastructure
    - Set up structured logging with timestamp and context
    - Implement log levels (ERROR, WARN, INFO, DEBUG)
    - Log all errors with request context
    - Log request details excluding tokens
    - Log execution lifecycle events
    - Log attestation generation events
    - Log request phase durations
    - Implement log rotation and retention
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.7_

  - [ ] 13.2 Implement error response handling
    - Return HTTP 500 for unexpected errors
    - Ensure error messages don't expose internal details
    - Use consistent error response format
    - _Requirements: 7.5, 7.6_

  - [ ]* 13.3 Write property tests for logging and error handling
    - **Property 37: Error Logging with Context**
    - **Property 38: Request Logging without Token**
    - **Property 39: Execution Event Logging**
    - **Property 40: Attestation Event Logging**
    - **Property 41: Unexpected Error Response**
    - **Property 42: Error Response Security**
    - **Property 43: Request Phase Duration Logging**
    - **Validates: Requirements 7.1-7.7**

  - [ ]* 13.4 Write unit tests for logging
    - Test log output for various scenarios
    - Test token exclusion from logs
    - Test error message sanitization
    - _Requirements: 7.1-7.7_

- [ ] 14. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 15. Integration and wiring
  - [ ] 15.1 Create main application entry point
    - Load configuration on startup
    - Verify NSM device availability
    - Initialize all components
    - Start HTTP server
    - Handle graceful shutdown
    - _Requirements: 9.1, 9.8_

  - [ ] 15.2 Wire all components together
    - Connect HTTP handlers to service layer
    - Connect service layer to execution layer
    - Connect execution layer to storage layer
    - Ensure proper dependency injection
    - _Requirements: All requirements_

  - [ ]* 15.3 Write integration tests
    - Test complete end-to-end execution flow
    - Test error scenarios (auth failure, timeout, not found)
    - Test concurrent execution handling
    - Test rate limiting enforcement
    - Test cleanup and retention policies
    - _Requirements: All requirements_

- [ ] 16. Create deployment configuration
  - Create Dockerfile for containerized deployment
  - Create example environment variable configuration
  - Create README with setup and deployment instructions
  - Document AWS Nitro EC2 instance requirements
  - _Requirements: 9.1_

- [ ] 17. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests validate the 60 correctness properties from the design document
- The implementation uses Python with Flask/FastAPI for the HTTP server
- AWS Nitro attestation requires running on a Nitro-based EC2 instance
- Sandbox isolation may use containers (Docker) or process isolation (chroot, namespaces)
- All 60 properties should be tested with hypothesis library (minimum 100 iterations each)
- Checkpoints ensure incremental validation throughout implementation
