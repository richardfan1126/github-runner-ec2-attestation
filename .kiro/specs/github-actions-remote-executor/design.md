# Design Document: GitHub Actions Remote Executor

## Overview

The GitHub Actions Remote Executor is an HTTP server that runs on an AWS Nitro-based EC2 instance, providing a secure and attestable environment for executing scripts from GitHub repositories. The system receives execution requests from GitHub Actions workflows, generates cryptographic attestation documents proving the execution environment, and executes scripts asynchronously while allowing clients to poll for output and status.

This design document covers two major aspects of the system:

1. **Runtime Design**: How the Remote Executor operates when deployed - the HTTP server, request handling, script execution, attestation generation, and output polling mechanisms.

2. **Build Design**: How the attestable AMI containing the Remote Executor is built - the GitHub Actions workflow that builds a KIWI image in a reproducible Docker environment, attests build artifacts using GitHub's attestation service, publishes them to GitHub Container Registry with PCR measurements, and converts the KIWI image to an AWS AMI using a temporary EC2 instance that verifies signatures before AMI creation.

### Key Design Principles

1. **Asynchronous Execution Model**: Requests return immediately with an execution ID and attestation document, while script execution proceeds in the background
2. **Polling-based Output Retrieval**: Clients poll a separate endpoint to retrieve incremental output rather than maintaining long HTTP connections
3. **Root Execution**: Scripts execute as root with full system privileges for maximum flexibility
4. **Attestable Environment**: AWS Nitro attestation provides cryptographic proof of the execution environment
5. **Stateless Request Handling**: Each request is independent, with execution state stored separately

### Architecture Goals

- Support concurrent execution of multiple scripts
- Provide verifiable proof of execution environment through attestation
- Enable reliable output retrieval through polling
- Maintain execution tracking and monitoring capabilities

---

# PART 1: RUNTIME DESIGN

## Architecture

### System Components

The system consists of the following major components:

```
┌─────────────────────────────────────────────────────────────┐
│                     GitHub Actions Workflow                  │
└────────────┬────────────────────────────────┬────────────────┘
             │ POST /execute                  │ GET /execution/{id}/output
             │                                │
┌────────────▼────────────────────────────────▼────────────────┐
│                        HTTP Server                            │
│  ┌──────────────────┐  ┌──────────────────┐                 │
│  │ Request Handler  │  │ Output Handler   │                 │
│  └────────┬─────────┘  └────────┬─────────┘                 │
└───────────┼─────────────────────┼───────────────────────────┘
            │                     │
┌───────────▼─────────────────────▼───────────────────────────┐
│                    Core Services Layer                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Request    │  │  Repository  │  │ Attestation  │      │
│  │  Validator   │  │    Client    │  │  Generator   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└───────────────────────────────────────────────────────────────┘
            │                     │                     │
┌───────────▼─────────────────────▼─────────────────────▼─────┐
│                   Execution Management Layer                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  Execution   │  │    Script    │  │    Output    │      │
│  │   Manager    │  │   Executor   │  │  Collector   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└───────────────────────────────────────────────────────────────┘
            │                     │                     │
┌───────────▼─────────────────────▼─────────────────────▼─────┐
│                    Storage Layer                              │
│  ┌──────────────┐  ┌──────────────┐                         │
│  │  Execution   │  │  Temporary   │                         │
│  │    Store     │  │   Storage    │                         │
│  └──────────────┘  └──────────────┘                         │
└───────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

**HTTP Server**
- Listens for incoming HTTP requests on configured port
- Routes requests to appropriate handlers
- Manages concurrent connections
- Implements rate limiting per source IP

**Request Handler**
- Parses and validates execution requests
- Coordinates repository file retrieval
- Generates attestation documents
- Creates execution records
- Initiates asynchronous script execution
- Returns immediate response with execution ID and attestation

**Output Handler**
- Retrieves execution status and output by execution ID
- Supports offset-based output retrieval
- Returns completion status and exit codes

**Request Validator**
- Validates request structure and required fields
- Validates repository URL format
- Validates Git commit SHA format
- Validates script file path
- Validates file size limits

**Repository Client**
- Authenticates to GitHub using provided tokens
- Fetches file content from specific commits
- Handles GitHub API errors
- Caches authentication state

**Attestation Generator**
- Interfaces with AWS Nitro Security Module (NSM) via the `nitro-tpm-attest` command-line tool
- Creates attestation documents with execution metadata
- Signs documents using NSM cryptographic capabilities
- Encodes attestation in standard format (CBOR)
- Implementation approach (based on `demo_api.py::AttestationAPIHandler.generate_attestation_document()`):
  1. Accepts optional user_data and nonce parameters for inclusion in attestation
  2. Writes user_data and nonce to temporary files if provided
  3. Invokes `/usr/bin/nitro-tpm-attest` with optional `--user-data` and `--nonce` flags
  4. Captures binary CBOR-encoded attestation document from stdout
  5. Implements 30-second timeout for attestation generation
  6. Returns attestation document as bytes or detailed error information
  7. Cleans up temporary files in finally block
  8. Error handling includes: subprocess failures, timeouts, and OS errors
  9. Error responses include command, exit code, stdout, stderr, and context for debugging

**Execution Manager**
- Generates unique execution IDs
- Maintains execution state (queued, running, completed, failed, timed_out)
- Manages execution lifecycle
- Implements execution timeout handling
- Cleans up completed executions after retention period

**Script Executor**
- Executes scripts as root with full system privileges
- Captures stdout and stderr streams
- Monitors execution progress
- Enforces resource limits (timeouts)
- Handles process termination
- Records exit codes

**Output Collector**
- Captures streaming output from script execution
- Stores output incrementally
- Supports offset-based retrieval
- Manages output retention

**Execution Store**
- Persists execution metadata and state
- Stores output data
- Provides query interface by execution ID
- Implements retention policy

**Temporary Storage**
- Manages temporary file storage for fetched scripts
- Provides isolated directories per execution
- Handles cleanup after execution

### Request Flow

**Execution Request Flow:**

1. Client sends POST request to `/execute` with repository URL, commit hash, script path, and GitHub token
2. Request Handler validates request structure
3. Request Validator validates all fields
4. Repository Client authenticates and fetches script file from GitHub
5. Attestation Generator creates attestation document with execution metadata
6. Execution Manager creates execution record with unique ID
7. Response returned immediately with execution ID and attestation document
8. Script Executor begins asynchronous execution as root
9. Output Collector captures stdout/stderr streams
10. Execution Manager updates status upon completion

**Output Polling Flow:**

1. Client sends GET request to `/execution/{id}/output` with optional offset parameter
2. Output Handler retrieves execution record by ID
3. Output Collector returns current status, output from offset, and completion flag
4. If complete, exit code is included
5. Client repeats polling until execution completes

### Concurrency Model

- HTTP server handles multiple concurrent connections using thread pool or async I/O
- Each execution runs in a separate process/container
- Execution state stored in thread-safe data structure or external store
- Output collection uses buffered writes to avoid blocking
- Maximum concurrent executions configurable to prevent resource exhaustion

## Components and Interfaces

### HTTP API Endpoints

#### POST /execute

Initiates script execution and returns attestation document.

**Request Body:**
```json
{
  "repository_url": "https://github.com/owner/repo",
  "commit_hash": "abc123def456...",
  "script_path": "scripts/build.sh",
  "github_token": "ghp_..."
}
```

**Response (200 OK):**
```json
{
  "execution_id": "uuid-v4",
  "attestation_document": "base64-encoded-cbor",
  "status": "queued"
}
```

**Error Responses:**
- 400 Bad Request: Malformed request or validation failure
- 401 Unauthorized: GitHub authentication failure
- 404 Not Found: Repository, commit, or file not found
- 413 Payload Too Large: Script file exceeds size limit
- 429 Too Many Requests: Rate limit exceeded
- 500 Internal Server Error: Attestation or system failure

#### GET /execution/{execution_id}/output

Retrieves execution status and output.

**Query Parameters:**
- `offset` (optional): Byte offset to start retrieving output from

**Response (200 OK):**
```json
{
  "execution_id": "uuid-v4",
  "status": "running|completed|failed|timed_out",
  "stdout": "output text...",
  "stderr": "error text...",
  "stdout_offset": 1024,
  "stderr_offset": 256,
  "complete": false,
  "exit_code": null
}
```

When complete:
```json
{
  "execution_id": "uuid-v4",
  "status": "completed",
  "stdout": "output text...",
  "stderr": "error text...",
  "stdout_offset": 2048,
  "stderr_offset": 512,
  "complete": true,
  "exit_code": 0
}
```

**Error Responses:**
- 404 Not Found: Execution ID does not exist

#### GET /health

Health check endpoint for monitoring.

**Response (200 OK):**
```json
{
  "status": "healthy",
  "attestation_available": true,
  "disk_space_mb": 10240,
  "active_executions": 3
}
```

#### GET /metrics

Metrics endpoint for monitoring.

**Response (200 OK):**
```json
{
  "total_executions": 1523,
  "successful_executions": 1450,
  "failed_executions": 73,
  "average_duration_ms": 3421,
  "active_executions": 3
}
```

### Internal Interfaces

#### RequestValidator Interface

```python
class RequestValidator:
    def validate_execution_request(self, request: dict) -> ValidationResult:
        """Validates execution request structure and fields"""
        pass
    
    def validate_repository_url(self, url: str) -> bool:
        """Validates GitHub repository URL format"""
        pass
    
    def validate_commit_hash(self, hash: str) -> bool:
        """Validates Git commit SHA format"""
        pass
    
    def validate_script_path(self, path: str) -> bool:
        """Validates script file path"""
        pass
```

#### RepositoryClient Interface

```python
class RepositoryClient:
    def authenticate(self, token: str) -> AuthResult:
        """Authenticates with GitHub using token"""
        pass
    
    def fetch_file(self, repo_url: str, commit: str, path: str) -> FileContent:
        """Fetches file content from specific commit"""
        pass
```

#### AttestationGenerator Interface

```python
class AttestationGenerator:
    def generate_attestation(self, metadata: ExecutionMetadata) -> AttestationDocument:
        """Generates signed attestation document"""
        pass
    
    def verify_nsm_available(self) -> bool:
        """Checks if NSM device is available"""
        pass
```

#### ExecutionManager Interface

```python
class ExecutionManager:
    def create_execution(self, request: ExecutionRequest) -> ExecutionID:
        """Creates new execution record"""
        pass
    
    def get_execution(self, execution_id: str) -> ExecutionRecord:
        """Retrieves execution record by ID"""
        pass
    
    def update_status(self, execution_id: str, status: ExecutionStatus) -> None:
        """Updates execution status"""
        pass
    
    def cleanup_expired(self) -> None:
        """Removes executions past retention period"""
        pass
```

#### ScriptExecutor Interface

```python
class ScriptExecutor:
    def execute_async(self, execution_id: str, script_path: str) -> None:
        """Executes script asynchronously as root"""
        pass
    
    def terminate(self, execution_id: str) -> None:
        """Terminates running execution"""
        pass
```

#### OutputCollector Interface

```python
class OutputCollector:
    def capture_output(self, execution_id: str, stream: str, data: bytes) -> None:
        """Captures output data from execution"""
        pass
    
    def get_output(self, execution_id: str, offset: int = 0) -> OutputData:
        """Retrieves output from specified offset"""
        pass
```

## Data Models

### ExecutionRequest

```python
@dataclass
class ExecutionRequest:
    repository_url: str
    commit_hash: str
    script_path: str
    github_token: str
```

### ExecutionRecord

```python
@dataclass
class ExecutionRecord:
    execution_id: str
    repository_url: str
    commit_hash: str
    script_path: str
    status: ExecutionStatus
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    exit_code: Optional[int]
    timeout_seconds: int
```

### ExecutionStatus

```python
class ExecutionStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
```

### AttestationDocument

```python
@dataclass
class AttestationDocument:
    repository_url: str
    commit_hash: str
    script_path: str
    timestamp: datetime
    signature: bytes  # CBOR-encoded NSM attestation
```

### OutputData

```python
@dataclass
class OutputData:
    stdout: str
    stderr: str
    stdout_offset: int
    stderr_offset: int
    complete: bool
    exit_code: Optional[int]
```

### Configuration

```python
@dataclass
class ServerConfig:
    port: int
    max_concurrent_executions: int
    execution_timeout_seconds: int
    max_script_size_bytes: int
    rate_limit_per_ip: int
    rate_limit_window_seconds: int
    temp_storage_path: str
    output_retention_hours: int
    nsm_device_path: str
```


## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Valid Request Acceptance

*For any* execution request containing a valid repository URL, commit hash, script file path, and GitHub token, the server should accept and process the request.

**Validates: Requirements 1.3**

### Property 2: Malformed Request Rejection

*For any* malformed request body, the Request Validator should return HTTP 400 with a descriptive error message.

**Validates: Requirements 1.4**

### Property 3: Concurrent Request Handling

*For any* set of concurrent execution requests, the server should handle all requests without blocking or failure.

**Validates: Requirements 1.5**

### Property 4: Required Field Validation

*For any* execution request with one or more missing required fields (repository_url, commit_hash, script_path, github_token), the Request Validator should reject the request and return HTTP 400.

**Validates: Requirements 2.1, 2.6**

### Property 5: Repository URL Format Validation

*For any* invalid repository URL format, the Request Validator should reject the request and return HTTP 400.

**Validates: Requirements 2.2**

### Property 6: Commit Hash Format Validation

*For any* invalid Git commit SHA format, the Request Validator should reject the request and return HTTP 400.

**Validates: Requirements 2.3**

### Property 7: Validation Error Response

*For any* validation failure, the Request Validator should return HTTP 400 with specific validation error details.

**Validates: Requirements 2.5**

### Property 8: GitHub Authentication

*For any* valid execution request with a GitHub token, the Repository Client should authenticate to GitHub using that token before fetching files.

**Validates: Requirements 3.1**

### Property 9: Exact Commit File Retrieval

*For any* valid repository, commit hash, and file path, the Repository Client should fetch the file content that exists at that exact commit.

**Validates: Requirements 3.2**

### Property 10: Authentication Failure Response

*For any* invalid or expired GitHub token, the Repository Client should return HTTP 401 with an authentication error message.

**Validates: Requirements 3.3**

### Property 11: Repository Not Found Response

*For any* non-existent repository URL, the Repository Client should return HTTP 404 with a repository not found error.

**Validates: Requirements 3.4**

### Property 12: Commit Not Found Response

*For any* non-existent commit hash in a valid repository, the Repository Client should return HTTP 404 with a commit not found error.

**Validates: Requirements 3.5**

### Property 13: File Not Found Response

*For any* non-existent file path at a valid commit, the Repository Client should return HTTP 404 with a file not found error.

**Validates: Requirements 3.6**

### Property 14: Temporary File Storage

*For any* successfully fetched script file, the Repository Client should store the file in a temporary secure location accessible by the execution ID.

**Validates: Requirements 3.7**

### Property 15: Attestation Document Generation

*For any* successfully retrieved script file, the Attestation Generator should create an attestation document.

**Validates: Requirements 4.1**

### Property 16: Attestation Document Completeness

*For any* generated attestation document, it should include the repository URL, commit hash, script file path, and timestamp.

**Validates: Requirements 4.2, 4.3, 4.4, 4.5**

### Property 17: Attestation Document Signing

*For any* generated attestation document, it should be signed using AWS Nitro attestation capabilities and the signature should be verifiable.

**Validates: Requirements 4.6**

### Property 18: Execution ID Uniqueness

*For any* set of execution requests, all generated execution IDs should be unique.

**Validates: Requirements 4.7**

### Property 19: Immediate Response with Attestation

*For any* valid execution request, the server should return a response containing both the attestation document and execution ID before script execution completes.

**Validates: Requirements 4.8, 4.9**

### Property 20: Attestation Failure Response

*For any* attestation generation failure, the server should return HTTP 500 with an attestation error message.

**Validates: Requirements 4.10**

### Property 21: Asynchronous Script Execution

*For any* execution request, the script should execute asynchronously after the initial response is sent.

**Validates: Requirements 5.1**

### Property 22: Process Isolation

*For any* script execution, the script should run in an isolated process separate from the server process.

**Validates: Requirements 5.2**

### Property 23: Output Stream Capture

*For any* script execution, both stdout and stderr streams should be captured completely.

**Validates: Requirements 5.3**

### Property 24: Output Storage Round-Trip

*For any* script execution with captured output, storing the output by execution ID and then retrieving it should return the same output content.

**Validates: Requirements 5.4, 6.3, 6.4**

### Property 25: Execution Timeout Configuration

*For any* configured timeout value, script executions should respect that timeout limit.

**Validates: Requirements 5.5**

### Property 26: Timeout Termination

*For any* script execution that exceeds the configured timeout, the Script Executor should terminate the process and mark the execution status as timed_out.

**Validates: Requirements 5.6**

### Property 27: Exit Code Capture

*For any* completed script execution, the exit code should be captured and stored with the execution record.

**Validates: Requirements 5.7**

### Property 28: Temporary File Cleanup

*For any* script execution (successful or failed), all temporary files should be cleaned up after execution completes.

**Validates: Requirements 8.6**

### Property 29: Execution Status Tracking

*For any* script execution, the status should transition correctly through the states: queued → running → (completed|failed|timed_out).

**Validates: Requirements 5.9**

### Property 30: Output Endpoint Status Return

*For any* execution ID, accessing the output endpoint should return the current execution status.

**Validates: Requirements 6.2**

### Property 31: Output Structure Separation

*For any* output endpoint response, stdout and stderr should be in separate, distinguishable fields.

**Validates: Requirements 6.5**

### Property 32: Offset-Based Output Retrieval

*For any* execution with captured output and a specified offset, the output endpoint should return only the output from that offset onward.

**Validates: Requirements 6.6**

### Property 33: Completion Exit Code Inclusion

*For any* completed script execution, the output endpoint response should include the exit code.

**Validates: Requirements 6.7**

### Property 34: Completion Flag Accuracy

*For any* execution, the output endpoint response should include a boolean completion flag that accurately reflects whether execution is complete.

**Validates: Requirements 6.8**

### Property 35: Invalid Execution ID Response

*For any* non-existent execution ID, the output endpoint should return HTTP 404 with an execution not found error.

**Validates: Requirements 6.9**

### Property 36: Output Retention Period

*For any* completed execution, the output should be retained and accessible for the configured retention period, and removed after that period expires.

**Validates: Requirements 6.10**

### Property 37: Error Logging with Context

*For any* error that occurs, the server should create a log entry containing the error, timestamp, and request context.

**Validates: Requirements 7.1**

### Property 38: Request Logging without Token

*For any* incoming execution request, the server should log the request details (repository URL, commit hash, script path) but exclude the GitHub token.

**Validates: Requirements 7.2**

### Property 39: Execution Event Logging

*For any* script execution, the server should log both the execution start event and the completion event.

**Validates: Requirements 7.3**

### Property 40: Attestation Event Logging

*For any* attestation generation, the server should log the attestation generation event.

**Validates: Requirements 7.4**

### Property 41: Unexpected Error Response

*For any* unexpected error, the server should return HTTP 500 with a generic error message.

**Validates: Requirements 7.5**

### Property 42: Error Response Security

*For any* error response, the message should not expose internal system details such as file paths, stack traces, or configuration values.

**Validates: Requirements 7.6**

### Property 43: Request Phase Duration Logging

*For any* execution request, the server should log the duration of each processing phase (validation, authentication, file retrieval, attestation, execution).

**Validates: Requirements 7.7**

### Property 47: Script Size Validation

*For any* execution request, the server should validate the script file size before execution.

**Validates: Requirements 8.2**

### Property 48: Oversized Script Rejection

*For any* script file that exceeds the maximum allowed size, the server should return HTTP 413 with a file too large error.

**Validates: Requirements 8.3**

### Property 49: Rate Limiting per IP

*For any* source IP address that exceeds the configured rate limit, subsequent requests should be rejected with HTTP 429.

**Validates: Requirements 8.5**

### Property 50: Configuration Loading

*For any* server startup, configuration should be loaded from environment variables or a configuration file.

**Validates: Requirements 9.1**

### Property 51: Port Configuration

*For any* configured HTTP port value, the server should listen on that port.

**Validates: Requirements 9.2**

### Property 52: Timeout Configuration

*For any* configured execution timeout value, that timeout should be applied to script executions.

**Validates: Requirements 9.3**

### Property 53: Size Limit Configuration

*For any* configured maximum script file size, that limit should be enforced during validation.

**Validates: Requirements 9.4**

### Property 54: Rate Limit Configuration

*For any* configured rate limiting parameters, those limits should be enforced for incoming requests.

**Validates: Requirements 9.5**

### Property 55: Storage Path Configuration

*For any* configured temporary file storage location, temporary files should be stored in that location.

**Validates: Requirements 9.6**

### Property 56: Retention Period Configuration

*For any* configured output retention period, execution output should be retained for that duration.

**Validates: Requirements 9.7**

### Property 57: Missing Configuration Failure

*For any* required configuration parameter that is missing, the server should fail to start with a descriptive error message.

**Validates: Requirements 9.8**

### Property 58: Health Check Attestation Status

*For any* health check request, the response should include the attestation capability status.

**Validates: Requirements 10.3**

### Property 59: Health Check Disk Space

*For any* health check request, the response should include disk space availability information.

**Validates: Requirements 10.4**

### Property 60: Execution Metrics Tracking

*For any* set of script executions, the metrics endpoint should accurately track the count of successful and failed executions.

**Validates: Requirements 10.6**

### Error Categories

The system handles errors in the following categories:

1. **Client Errors (4xx)**
   - 400 Bad Request: Malformed requests, validation failures
   - 401 Unauthorized: GitHub authentication failures
   - 404 Not Found: Repository, commit, file, or execution ID not found
   - 413 Payload Too Large: Script file exceeds size limit
   - 429 Too Many Requests: Rate limit exceeded

2. **Server Errors (5xx)**
   - 500 Internal Server Error: Attestation failures, unexpected errors

### Error Response Format

All error responses follow a consistent JSON structure:

```json
{
  "error": "error_code",
  "message": "Human-readable error description",
  "details": {
    "field": "Additional context when applicable"
  }
}
```

### Error Handling Strategies

**Request Validation Errors**
- Validate all fields before processing
- Return specific error messages for each validation failure
- Log validation failures with request context

**GitHub API Errors**
- Distinguish between authentication, not found, and rate limit errors
- Map GitHub API error codes to appropriate HTTP status codes
- Retry transient errors with exponential backoff

**Attestation Errors**
- Verify NSM device availability at startup
- Return 500 errors for attestation failures
- Log detailed attestation error information
- Include health check status for attestation capability

**Execution Errors**
- Capture script stderr for error diagnosis
- Mark execution status appropriately (failed vs timed_out)
- Clean up resources even when errors occur
- Log execution errors with execution ID context

**Resource Exhaustion**
- Implement rate limiting to prevent abuse
- Limit concurrent executions to prevent resource exhaustion
- Monitor disk space and reject requests when low
- Implement execution timeouts to prevent runaway processes

### Logging Strategy

**Log Levels**
- ERROR: All errors, failed executions, attestation failures
- WARN: Rate limit hits, approaching resource limits, long execution times
- INFO: Request received, execution started, execution completed, cleanup events
- DEBUG: Detailed request/response data, GitHub API calls, attestation details

**Log Context**
- Include execution ID in all execution-related logs
- Include request ID for request tracing
- Include timestamp in ISO 8601 format
- Exclude sensitive data (tokens, credentials)

**Log Retention**
- Rotate logs daily
- Retain logs for configurable period (default 30 days)
- Compress archived logs

## Testing Strategy

### Dual Testing Approach

The system requires both unit testing and property-based testing for comprehensive coverage:

**Unit Tests** focus on:
- Specific examples demonstrating correct behavior
- Edge cases (empty inputs, boundary values, special characters)
- Error conditions and error response formats
- Integration points between components
- Mocking external dependencies (GitHub API, NSM device)

**Property-Based Tests** focus on:
- Universal properties that hold for all inputs
- Comprehensive input coverage through randomization
- Invariants that must be maintained
- Round-trip properties (serialization, storage/retrieval)
- Concurrent behavior under load

Together, unit tests catch concrete bugs while property tests verify general correctness across the input space.

### Property-Based Testing Configuration

**Testing Library**: Use `hypothesis` for Python (or `fast-check` for TypeScript, `QuickCheck` for Haskell, depending on implementation language)

**Test Configuration**:
- Minimum 100 iterations per property test
- Each property test must reference its design document property
- Tag format: `# Feature: github-actions-remote-executor, Property {number}: {property_text}`

**Example Property Test Structure**:

```python
from hypothesis import given, strategies as st

# Feature: github-actions-remote-executor, Property 18: Execution ID Uniqueness
@given(st.lists(st.builds(ExecutionRequest), min_size=2, max_size=100))
def test_execution_id_uniqueness(requests):
    """For any set of execution requests, all generated execution IDs should be unique"""
    execution_ids = [generate_execution_id(req) for req in requests]
    assert len(execution_ids) == len(set(execution_ids))
```

### Test Coverage Areas

**Request Validation Testing**
- Unit tests: Specific invalid formats, missing fields
- Property tests: Random valid/invalid requests, field combinations

**GitHub Integration Testing**
- Unit tests: Mock GitHub API responses, specific error codes
- Property tests: Random repository URLs, commit hashes, file paths

**Attestation Testing**
- Unit tests: Mock NSM device, specific attestation formats
- Property tests: Random execution metadata, attestation verification

**Execution Testing**
- Unit tests: Specific scripts with known output, timeout scenarios
- Property tests: Random script content, concurrent executions

**Output Collection Testing**
- Unit tests: Specific output patterns, offset edge cases
- Property tests: Random output sizes, offset values, concurrent access

**Security Testing**
- Unit tests: Path traversal attempts, token handling
- Property tests: Random input validation scenarios

**Configuration Testing**
- Unit tests: Specific missing config, invalid values
- Property tests: Random configuration combinations

### Integration Testing

**End-to-End Scenarios**:
1. Complete execution flow: request → attestation → execution → output retrieval
2. Error scenarios: authentication failure, timeout, file not found
3. Concurrent execution: multiple simultaneous requests
4. Rate limiting: exceeding limits from single IP
5. Cleanup: verify temporary files removed after execution

**External Dependencies**:
- Mock GitHub API for predictable testing
- Mock NSM device for attestation testing
- Use separate processes for execution testing

### Performance Testing

**Load Testing**:
- Concurrent request handling capacity
- Execution throughput under load
- Memory usage during concurrent executions
- Disk I/O performance for output collection

**Stress Testing**:
- Maximum concurrent executions
- Large script file handling
- Long-running script behavior
- Output retention with many executions

### Security Testing

**Penetration Testing**:
- Token extraction attempts
- Resource exhaustion attacks
- Input validation bypass attempts

**Compliance Testing**:
- Verify no sensitive data in logs
- Verify no sensitive data in error responses
- Verify proper cleanup of temporary files
- Verify attestation signature validity


## Error Handling


---

# PART 2: BUILD DESIGN

## Build Overview

The build process creates an attestable AMI containing the GitHub Actions Remote Executor. The build is performed in two distinct phases:

1. **KIWI Image Build Phase**: A GitHub Actions workflow builds a KIWI image inside a Docker container, generates PCR measurements, attests the artifacts using GitHub's attestation service, and publishes them to GitHub Container Registry (GHCR).

2. **AMI Conversion Phase**: A Python script provisions a temporary EC2 instance using Terraform, installs required tools, verifies artifact signatures, downloads the KIWI image, uploads it as an EBS snapshot using coldsnap, and registers it as an AMI with TPM 2.0 support.

### Build Design Principles

1. **Reproducible Builds**: KIWI image built in Docker with pinned dependency versions
2. **Cryptographic Attestation**: Build artifacts signed using GitHub's attestation service with Sigstore
3. **Signature Verification**: AMI conversion only proceeds after verifying artifact signatures
4. **Isolated Build Environment**: Temporary EC2 instance provisioned for each AMI build
5. **Infrastructure as Code**: Terraform manages all build infrastructure
6. **Automated Cleanup**: All temporary resources destroyed after build completion

## Build Architecture

### Build System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                    GitHub Actions Workflow                       │
│                  (build-attestable-image.yml)                    │
└────────┬────────────────────────────────────┬───────────────────┘
         │                                    │
         │ 1. Build KIWI Image                │ 2. Attest & Publish
         │                                    │
┌────────▼────────────────────┐    ┌─────────▼──────────────────┐
│   KIWI Builder Container    │    │  GitHub Attestation Service │
│  (Docker + KIWI NG)         │    │     (Sigstore)              │
└────────┬────────────────────┘    └─────────┬──────────────────┘
         │                                    │
         │ Produces                           │ Signs
         │                                    │
┌────────▼────────────────────────────────────▼──────────────────┐
│              GitHub Container Registry (GHCR)                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Artifact Bundle:                                        │  │
│  │    - KIWI raw disk image (.raw)                         │  │
│  │    - PCR measurements (pcr_measurements.json)           │  │
│  │    - Attestation bundle (Sigstore signature)            │  │
│  │  Annotations: pcr4, pcr7                                │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             │ 3. Pull & Verify
                             │
┌────────────────────────────▼───────────────────────────────────┐
│                    AMI Converter Script                         │
│                     (build-ami.py)                              │
└────────┬───────────────────────────────────────┬───────────────┘
         │                                       │
         │ 4. Provision                          │ 5. Convert
         │                                       │
┌────────▼────────────────────┐    ┌────────────▼───────────────┐
│  Terraform Infrastructure   │    │   Build Instance (EC2)     │
│  - EC2 Instance             │    │   - Verify Signature       │
│  - Security Groups          │    │   - Download Artifacts     │
│  - SSH Key Pair             │    │   - Upload Snapshot        │
└─────────────────────────────┘    │   - Register AMI           │
                                   └────────────────────────────┘
                                              │
                                              │ 6. Create
                                              │
                                   ┌──────────▼─────────────────┐
                                   │   AWS AMI with TPM 2.0     │
                                   │   - EBS Snapshot           │
                                   │   - UEFI Boot Mode         │
                                   │   - PCR Measurements       │
                                   └────────────────────────────┘
```

### Build Component Responsibilities

**Build_Workflow (GitHub Actions)**
- Orchestrates the entire KIWI image build process
- Checks out repository with submodules
- Builds KIWI builder Docker image
- Configures loop devices for KIWI image building
- Executes KIWI NG build script inside container
- Extracts PCR measurements from build output
- Publishes artifacts to GHCR with ORAS
- Triggers GitHub attestation service
- Generates workflow summary with verification instructions

**KIWI_Builder (Docker Container)**
- Provides reproducible build environment
- Contains KIWI NG and all build dependencies with pinned versions
- Executes KIWI image build process
- Generates raw disk image (.raw file)
- Calculates PCR4 and PCR7 measurements
- Outputs pcr_measurements.json file

**Artifact_Publisher (ORAS)**
- Authenticates to GitHub Container Registry
- Bundles KIWI image and PCR measurements
- Annotates artifacts with PCR values
- Pushes artifacts to GHCR
- Calculates and returns artifact digest

**Attestation_Service (GitHub + Sigstore)**
- Generates build provenance attestation
- Signs attestation using Sigstore
- Includes artifact digest and repository identity
- Pushes attestation bundle to registry
- Provides attestation ID and verification URL

**AMI_Converter (Python Script)**
- Provisions temporary EC2 build instance using Terraform
- Detects user's public IP for SSH access configuration
- Manages SSH connectivity with keepalive
- Installs required tools (git, gcc, Rust, ORAS, GitHub CLI, coldsnap)
- Verifies artifact signatures before proceeding
- Downloads artifacts from GHCR
- Uploads raw disk image to EBS snapshot
- Registers AMI with TPM 2.0 and UEFI boot mode
- Saves build results with PCR measurements
- Cleans up all temporary infrastructure

**Signature_Verifier (GitHub CLI)**
- Extracts repository identity from artifact reference
- Fetches artifact manifest digest using ORAS
- Downloads GitHub attestation bundle
- Verifies attestation using GitHub CLI in offline mode
- Terminates build process if verification fails

**Build_Instance (EC2)**
- Temporary Amazon Linux 2023 instance
- Provides environment for artifact verification and AMI conversion
- Runs coldsnap for snapshot upload
- Automatically destroyed after build completion

### Build Request Flow

**KIWI Image Build Flow:**

1. GitHub Actions workflow triggered (push to main or manual dispatch)
2. Repository checked out with submodules
3. KIWI builder Docker image built from Dockerfile
4. Build output directory created on host
5. Loop devices configured on host for KIWI
6. KIWI NG build script executed inside container
7. Raw disk image and PCR measurements generated
8. PCR4 and PCR7 extracted from pcr_measurements.json
9. Artifacts pushed to GHCR with ORAS (with PCR annotations)
10. GitHub attestation service signs artifacts
11. Workflow summary generated with verification instructions

**Artifact Publishing Flow:**

1. Artifact tag generated from branch name and timestamp
2. ORAS authenticates to GHCR using GitHub token
3. Artifact bundle created with raw image and PCR measurements
4. PCR4 and PCR7 added as artifact annotations
5. Artifact pushed to GHCR
6. Manifest digest calculated and returned
7. GitHub attestation action triggered with artifact digest
8. Attestation bundle pushed to registry

**Signature Verification Flow:**

1. Repository identity extracted from artifact reference
2. Artifact manifest fetched using ORAS
3. Manifest digest calculated
4. Attestation bundle downloaded from GitHub API
5. GitHub CLI verifies attestation in offline mode
6. Verification result logged
7. Build proceeds only if verification succeeds

**AMI Conversion Flow:**

1. User's public IP detected for SSH access
2. Terraform provisions EC2 instance with security groups
3. SSH key pair generated and saved
4. Script waits for instance to be running and pass status checks
5. SSH connectivity verified with retries
6. System dependencies installed (git, gcc, Rust)
7. ORAS CLI installed from GitHub releases
8. GitHub CLI installed from official repository
9. Coldsnap cloned and built from AWS Labs repository
10. Artifact signature verified using GitHub CLI
11. Artifacts downloaded from GHCR using ORAS
12. Raw disk image uploaded to EBS snapshot using coldsnap
13. Snapshot completion awaited
14. AMI registered with TPM 2.0, UEFI, and ENA support
15. Build result saved with AMI ID, snapshot ID, and PCR measurements
16. SSH connection closed
17. Terraform destroys all infrastructure
18. Temporary SSH key deleted

### Build Concurrency Model

- GitHub Actions workflow runs on ubuntu-latest runner
- KIWI build executes inside Docker container with privileged access
- Loop devices shared between host and container
- AMI conversion uses single EC2 instance per build
- Multiple builds can run concurrently (separate instances)
- Terraform state isolated per build execution
- Each build creates unique artifact tags with timestamps

## Build Components and Interfaces

### GitHub Actions Workflow Interface

**Workflow Triggers:**
- Push to main branch
- Manual workflow dispatch

**Workflow Permissions:**
- `contents: read` - Read repository contents
- `packages: write` - Push to GitHub Container Registry
- `id-token: write` - Generate attestation tokens
- `attestations: write` - Create attestations

**Workflow Outputs:**
- Artifact digest (sha256)
- Artifact path (GHCR URL)
- Artifact tag (branch-timestamp)
- PCR4 measurement
- PCR7 measurement
- Attestation ID
- Attestation URL

### KIWI Builder Interface

**Docker Image:**
- Base: openSUSE or compatible Linux distribution
- Installed: KIWI NG, Python, system build tools
- Privileged: Required for loop device access

**Build Script Interface:**
```bash
# Executed inside container
.github/scripts/build-kiwi-image.sh

# Inputs:
#   - KIWI image description files (from repository)
#   - Loop devices (from host)

# Outputs:
#   - build-output/*.raw (raw disk image)
#   - build-output/pcr_measurements.json (PCR values)
```

**PCR Measurements Format:**
```json
{
  "Measurements": {
    "PCR4": "hex-encoded-sha384-hash",
    "PCR7": "hex-encoded-sha384-hash"
  }
}
```

### ORAS Interface

**Push Command:**
```bash
oras push <artifact-path>:<tag> \
  --annotation "pcr4=<value>" \
  --annotation "pcr7=<value>" \
  <file1>:<media-type> \
  <file2>:<media-type>
```

**Pull Command:**
```bash
oras pull <artifact-path>@<digest>
```

**Manifest Fetch:**
```bash
oras manifest fetch <artifact-path>:<tag>
```

### GitHub Attestation Interface

**Attestation Action:**
```yaml
- uses: actions/attest-build-provenance@v3
  with:
    subject-name: <artifact-path>
    subject-digest: <artifact-digest>
    push-to-registry: true
```

**Verification Command:**
```bash
gh attestation verify oci://<artifact-path> -R <repository> -b <bundle-file>
```

### AMI Converter Script Interface

**Command-Line Arguments:**
```python
python scripts/build-ami.py \
  --artifact-ref <ghcr-artifact-reference> \
  --region <aws-region> \
  --instance-type <ec2-instance-type> \
  --output-file <result-json-file>
```

**Build Result Format:**
```json
{
  "ami_id": "ami-xxxxx",
  "snapshot_id": "snap-xxxxx",
  "region": "us-east-1",
  "build_timestamp": "2024-01-15T10:30:00Z",
  "pcr_measurements": {
    "pcr4": "hex-encoded-hash",
    "pcr7": "hex-encoded-hash"
  }
}
```

### Terraform Interface

**Module Location:**
```
terraform/build-ami/
```

**Input Variables:**
```hcl
variable "region" {
  description = "AWS region for build instance"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
}

variable "allowed_ssh_cidr" {
  description = "CIDR block for SSH access"
  type        = string
}
```

**Outputs:**
```hcl
output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.build_instance.id
}

output "instance_public_ip" {
  description = "Public IP address"
  value       = aws_instance.build_instance.public_ip
}

output "ssh_private_key" {
  description = "SSH private key in PEM format"
  value       = tls_private_key.ssh_key.private_key_pem
  sensitive   = true
}
```

### SSH Command Execution Interface

```python
def execute_remote_command(
    ssh_client: paramiko.SSHClient,
    command: str,
    stream_output: bool = True
) -> tuple[int, str, str]:
    """
    Execute command on remote instance.
    
    Args:
        ssh_client: Connected SSH client
        command: Shell command to execute
        stream_output: Whether to stream output to logger
    
    Returns:
        Tuple of (exit_code, stdout, stderr)
    """
```

### Coldsnap Interface

**Upload Command:**
```bash
coldsnap upload <raw-disk-image-path>
```

**Output Format:**
```
Uploading snapshot...
Progress: 100%
Snapshot ID: snap-xxxxx
```

### AWS EC2 AMI Registration Interface

```python
ec2_client.register_image(
    Name=ami_name,
    VirtualizationType='hvm',
    BootMode='uefi',
    Architecture='x86_64',
    RootDeviceName='/dev/xvda',
    BlockDeviceMappings=[{
        'DeviceName': '/dev/xvda',
        'Ebs': {'SnapshotId': snapshot_id}
    }],
    TpmSupport='v2.0',
    EnaSupport=True
)
```

## Build Data Models

### ArtifactReference

```python
@dataclass
class ArtifactReference:
    registry: str  # ghcr.io
    owner: str
    repository: str
    tag: str
    digest: Optional[str]
```

### PCRMeasurements

```python
@dataclass
class PCRMeasurements:
    pcr4: str  # Hex-encoded SHA-384 hash
    pcr7: str  # Hex-encoded SHA-384 hash
```

### BuildResult

```python
@dataclass
class BuildResult:
    ami_id: str
    snapshot_id: str
    region: str
    build_timestamp: datetime
    pcr_measurements: PCRMeasurements
```

### BuildInstanceConfig

```python
@dataclass
class BuildInstanceConfig:
    region: str
    instance_type: str
    allowed_ssh_cidr: str
    ssh_username: str = "ec2-user"
```

### TerraformOutputs

```python
@dataclass
class TerraformOutputs:
    instance_id: str
    instance_public_ip: str
    ssh_private_key: str
```

### AttestationBundle

```python
@dataclass
class AttestationBundle:
    attestation_id: str
    attestation_url: str
    subject_name: str
    subject_digest: str
    repository: str
```

## Build Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 61: KIWI Build Reproducibility

*For any* KIWI image build executed with the same source code and Docker image, the build should produce identical PCR measurements.

**Validates: Requirements 11.1, 11.2**

### Property 62: PCR Measurements Presence

*For any* successful KIWI build, the build output should contain both pcr_measurements.json file and a .raw disk image file.

**Validates: Requirements 11.6, 11.7**

### Property 63: PCR Extraction Validation

*For any* pcr_measurements.json file, extracting PCR4 and PCR7 values should succeed and return non-empty hex-encoded strings.

**Validates: Requirements 12.1**

### Property 64: Artifact Annotation Completeness

*For any* artifact pushed to GHCR, the artifact annotations should include both pcr4 and pcr7 values.

**Validates: Requirements 12.5**

### Property 65: Artifact Tag Uniqueness

*For any* two artifact builds from the same branch, the generated tags should be unique due to timestamp inclusion.

**Validates: Requirements 12.3**

### Property 66: Attestation Bundle Completeness

*For any* attested artifact, the attestation bundle should include the artifact digest and repository identity.

**Validates: Requirements 13.3, 13.4**

### Property 67: Signature Verification Requirement

*For any* AMI conversion attempt, the process should verify artifact signatures before downloading artifacts.

**Validates: Requirements 16.5, 16.6**

### Property 68: Untrusted Artifact Rejection

*For any* artifact with invalid or missing attestation, the AMI converter should terminate without creating an AMI.

**Validates: Requirements 16.6, 16.8**

### Property 69: SSH Access Configuration

*For any* build instance provisioning, the security group should allow SSH access only from the user's detected public IP address.

**Validates: Requirements 14.3**

### Property 70: Tool Installation Verification

*For any* tool installation on the build instance, the installation should be verified before proceeding to the next step.

**Validates: Requirements 15.6**

### Property 71: Artifact Download Completeness

*For any* artifact download, both the raw disk image and pcr_measurements.json should be present in the expected directory.

**Validates: Requirements 17.3, 17.4**

### Property 72: PCR Measurements Round-Trip

*For any* artifact with PCR measurements, the PCR values in the artifact annotations should match the values in the downloaded pcr_measurements.json file.

**Validates: Requirements 12.1, 17.5**

### Property 73: Snapshot Upload Success

*For any* successful coldsnap upload, the output should contain a valid snapshot ID starting with "snap-".

**Validates: Requirements 18.3**

### Property 74: AMI Registration Configuration

*For any* registered AMI, it should have TPM 2.0 support, UEFI boot mode, and ENA support enabled.

**Validates: Requirements 18.5, 18.6, 18.7**

### Property 75: Build Result Completeness

*For any* successful AMI build, the build result file should contain ami_id, snapshot_id, region, build_timestamp, and pcr_measurements.

**Validates: Requirements 19.2, 19.3, 19.4, 19.5, 19.6**

### Property 76: Infrastructure Cleanup Guarantee

*For any* AMI build (successful or failed), all temporary infrastructure should be destroyed and SSH keys deleted.

**Validates: Requirements 20.1, 20.2, 20.3, 20.4, 20.5**

### Property 77: Build Failure Cleanup

*For any* build failure at any stage, the cleanup process should still execute and destroy all provisioned resources.

**Validates: Requirements 20.5**

### Property 78: Terraform State Isolation

*For any* concurrent AMI builds, each build should use isolated Terraform state and not interfere with other builds.

**Validates: Build concurrency requirements**

### Property 79: SSH Keepalive Maintenance

*For any* long-running SSH operation, the connection should remain active through keepalive packets.

**Validates: Requirements 14.7**

### Property 80: Coldsnap Output Streaming

*For any* snapshot upload operation, coldsnap output should be streamed to logs in real-time.

**Validates: Requirements 18.2**

## Build Error Handling

### Build Error Categories

1. **KIWI Build Errors**
   - Missing dependencies in Docker image
   - Loop device configuration failures
   - KIWI NG build script failures
   - Missing PCR measurements file
   - Invalid PCR measurements format

2. **Artifact Publishing Errors**
   - GHCR authentication failures
   - ORAS push failures
   - Missing artifact files
   - Invalid PCR values
   - Network connectivity issues

3. **Attestation Errors**
   - GitHub attestation service failures
   - Sigstore signing failures
   - Attestation bundle creation failures

4. **Infrastructure Provisioning Errors**
   - Terraform initialization failures
   - Terraform apply failures
   - Instance provisioning timeouts
   - Security group configuration errors
   - SSH key generation failures

5. **Tool Installation Errors**
   - Package manager failures
   - Rust toolchain installation failures
   - ORAS download failures
   - GitHub CLI installation failures
   - Coldsnap build failures

6. **Signature Verification Errors**
   - Missing attestation bundle
   - Invalid signature
   - Repository identity mismatch
   - GitHub CLI verification failures

7. **Artifact Download Errors**
   - ORAS pull failures
   - Missing artifact files
   - Invalid artifact structure
   - PCR measurements parsing errors

8. **Snapshot Upload Errors**
   - Coldsnap upload failures
   - Snapshot creation timeouts
   - AWS API errors
   - Insufficient permissions

9. **AMI Registration Errors**
   - Invalid snapshot ID
   - AMI registration API failures
   - Unsupported configuration
   - Region-specific errors

10. **Cleanup Errors**
    - Terraform destroy failures
    - SSH key deletion failures
    - Resource leak warnings

### Build Error Handling Strategies

**KIWI Build Errors**
- Validate Docker image build before KIWI execution
- Check loop device availability before build
- Verify PCR measurements file exists and is valid JSON
- Fail workflow with descriptive error message
- Log complete KIWI build output for debugging

**Artifact Publishing Errors**
- Validate GHCR authentication before push
- Verify artifact files exist before ORAS push
- Validate PCR values are non-empty hex strings
- Retry transient network errors with exponential backoff
- Fail workflow if artifacts cannot be published

**Attestation Errors**
- Verify GitHub token has attestation permissions
- Log attestation service responses
- Fail workflow if attestation cannot be created
- Include attestation error details in workflow summary

**Signature Verification Errors**
- Log detailed verification output
- Terminate AMI build immediately on verification failure
- Provide clear error message about security implications
- Do not proceed with untrusted artifacts under any circumstances

**Infrastructure Provisioning Errors**
- Validate AWS credentials before Terraform execution
- Check user's public IP detection
- Retry SSH connectivity with exponential backoff
- Log Terraform output for debugging
- Ensure cleanup runs even if provisioning fails

**Tool Installation Errors**
- Verify each tool installation before proceeding
- Log installation output for debugging
- Fail fast if required tools cannot be installed
- Provide clear error messages about missing tools

**Snapshot Upload Errors**
- Stream coldsnap output for progress monitoring
- Parse snapshot ID from output
- Wait for snapshot completion before AMI registration
- Retry transient AWS API errors
- Log detailed error information

**Cleanup Errors**
- Log cleanup errors but do not fail overall process
- Attempt to destroy resources even if previous steps failed
- Warn about potential resource leaks
- Provide manual cleanup instructions if automated cleanup fails

### Build Logging Strategy

**Log Levels**
- ERROR: Build failures, verification failures, infrastructure errors
- WARN: Retries, cleanup issues, approaching timeouts
- INFO: Build progress, tool installations, artifact operations, AMI creation
- DEBUG: Terraform output, SSH commands, API responses

**Log Context**
- Include build timestamp in all logs
- Include artifact reference in AMI conversion logs
- Include instance ID in infrastructure logs
- Include step names for workflow tracking

**Log Retention**
- GitHub Actions logs retained per repository settings
- AMI build script logs written to build_ami.log file
- Terraform logs captured in script output

## Build Testing Strategy

### Dual Testing Approach

The build system requires both unit testing and property-based testing:

**Unit Tests** focus on:
- Specific error conditions (missing files, invalid formats)
- Tool installation verification
- PCR measurement parsing
- Artifact reference parsing
- Terraform output parsing
- SSH command execution
- Snapshot ID extraction

**Property-Based Tests** focus on:
- PCR measurement format validation across random inputs
- Artifact tag generation uniqueness
- Build result JSON serialization round-trips
- Infrastructure cleanup completeness
- Concurrent build isolation

### Property-Based Testing Configuration

**Testing Library**: Use `hypothesis` for Python components

**Test Configuration**:
- Minimum 100 iterations per property test
- Each property test must reference its design document property
- Tag format: `# Feature: github-actions-remote-executor, Property {number}: {property_text}`

### Build Test Coverage Areas

**KIWI Build Testing**
- Unit tests: Missing PCR file, invalid JSON format, missing .raw file
- Property tests: PCR measurement format validation, build reproducibility

**Artifact Publishing Testing**
- Unit tests: GHCR authentication, missing files, invalid PCR values
- Property tests: Tag uniqueness, annotation completeness

**Signature Verification Testing**
- Unit tests: Missing attestation, invalid signature, verification failure
- Property tests: Repository identity extraction, verification determinism

**Infrastructure Provisioning Testing**
- Unit tests: Terraform failures, SSH connectivity failures
- Property tests: Security group configuration, cleanup completeness

**Tool Installation Testing**
- Unit tests: Installation failures, verification failures
- Property tests: Installation idempotence

**Artifact Download Testing**
- Unit tests: Missing files, invalid structure
- Property tests: PCR round-trip consistency

**Snapshot Upload Testing**
- Unit tests: Coldsnap failures, snapshot ID parsing
- Property tests: Upload progress tracking

**AMI Registration Testing**
- Unit tests: Invalid configuration, API failures
- Property tests: AMI configuration completeness

### Build Integration Testing

**End-to-End Build Scenarios**:
1. Complete build flow: KIWI build → attestation → publish → verify → convert → AMI
2. Signature verification failure: Invalid attestation should prevent AMI creation
3. Tool installation failure: Should fail before artifact download
4. Snapshot upload failure: Should cleanup infrastructure
5. Concurrent builds: Multiple builds should not interfere

**External Dependencies**:
- Mock GitHub Container Registry for artifact operations
- Mock GitHub attestation service for signing
- Mock AWS APIs for infrastructure and AMI operations
- Use test fixtures for PCR measurements and artifacts

### Build Performance Testing

**Build Time Metrics**:
- KIWI image build duration
- Docker image build duration
- Artifact upload duration
- Signature verification duration
- Tool installation duration
- Snapshot upload duration
- Total end-to-end build time

**Resource Usage**:
- Docker container memory usage during KIWI build
- Build instance disk space usage
- Network bandwidth for artifact transfer
- Coldsnap memory usage during upload

### Build Security Testing

**Signature Verification Testing**:
- Verify rejection of unsigned artifacts
- Verify rejection of artifacts with invalid signatures
- Verify rejection of artifacts from wrong repository
- Verify attestation bundle integrity

**Access Control Testing**:
- Verify SSH access restricted to user's IP
- Verify GHCR authentication required for private repos
- Verify AWS credentials required for infrastructure
- Verify GitHub token permissions sufficient for attestation

**Artifact Integrity Testing**:
- Verify PCR measurements match between annotations and file
- Verify artifact digest matches manifest
- Verify downloaded files match expected checksums
