# Design Document: GitHub Actions Remote Executor

## Overview

The GitHub Actions Remote Executor is an HTTP server that runs on an AWS Nitro-based EC2 instance, providing a secure and attestable environment for executing scripts from GitHub repositories. The system receives execution requests from GitHub Actions workflows, generates cryptographic attestation documents proving the execution environment, and executes scripts asynchronously while allowing clients to poll for output and status.

### Key Design Principles

1. **Asynchronous Execution Model**: Requests return immediately with an execution ID and attestation document, while script execution proceeds in the background
2. **Polling-based Output Retrieval**: Clients poll a separate endpoint to retrieve incremental output rather than maintaining long HTTP connections
3. **Security Isolation**: Scripts execute in isolated environments with restricted privileges and resource access
4. **Attestable Environment**: AWS Nitro attestation provides cryptographic proof of the execution environment
5. **Stateless Request Handling**: Each request is independent, with execution state stored separately

### Architecture Goals

- Support concurrent execution of multiple scripts
- Provide verifiable proof of execution environment through attestation
- Minimize resource consumption through efficient isolation
- Enable reliable output retrieval through polling
- Maintain security boundaries between executions

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
│                    Storage & Isolation Layer                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  Execution   │  │  Temporary   │  │   Sandbox    │      │
│  │    Store     │  │   Storage    │  │  Environment │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
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
- Executes scripts in isolated sandbox environments
- Captures stdout and stderr streams
- Monitors execution progress
- Enforces resource limits
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

**Sandbox Environment**
- Provides isolated execution environment
- Enforces security boundaries
- Restricts filesystem access
- Restricts network access
- Limits resource consumption

### Request Flow

**Execution Request Flow:**

1. Client sends POST request to `/execute` with repository URL, commit hash, script path, and GitHub token
2. Request Handler validates request structure
3. Request Validator validates all fields
4. Repository Client authenticates and fetches script file from GitHub
5. Attestation Generator creates attestation document with execution metadata
6. Execution Manager creates execution record with unique ID
7. Response returned immediately with execution ID and attestation document
8. Script Executor begins asynchronous execution in sandbox
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
    def execute_async(self, execution_id: str, script_path: str, 
                     sandbox: SandboxConfig) -> None:
        """Executes script asynchronously in sandbox"""
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

### SandboxConfig

```python
@dataclass
class SandboxConfig:
    working_directory: str
    max_memory_mb: int
    max_cpu_percent: int
    network_enabled: bool
    timeout_seconds: int
    allowed_paths: List[str]
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

### Property 44: Minimal Privilege Execution

*For any* script execution, the script process should run with minimal system privileges (non-root user, restricted capabilities).

**Validates: Requirements 8.1**

### Property 45: Network Access Restriction

*For any* script execution with network restrictions enabled, the script should be unable to access network resources.

**Validates: Requirements 8.2**

### Property 46: Filesystem Access Restriction

*For any* script execution, the script should be unable to access filesystem locations outside its temporary execution directory.

**Validates: Requirements 8.3**

### Property 47: Script Size Validation

*For any* execution request, the server should validate the script file size before execution.

**Validates: Requirements 8.4**

### Property 48: Oversized Script Rejection

*For any* script file that exceeds the maximum allowed size, the server should return HTTP 413 with a file too large error.

**Validates: Requirements 8.5**

### Property 49: Rate Limiting per IP

*For any* source IP address that exceeds the configured rate limit, subsequent requests should be rejected with HTTP 429.

**Validates: Requirements 8.7**

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

## Error Handling

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
- Unit tests: Specific privilege escalation attempts, path traversal
- Property tests: Random filesystem/network access attempts

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
- Use test containers for isolation testing

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
- Privilege escalation attempts
- Filesystem access violations
- Network access violations
- Resource exhaustion attacks
- Token extraction attempts

**Compliance Testing**:
- Verify no sensitive data in logs
- Verify no sensitive data in error responses
- Verify proper cleanup of temporary files
- Verify attestation signature validity
