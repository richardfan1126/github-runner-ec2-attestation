# remote-executor Specification

## Purpose

Define the runtime behavior of the GitHub Actions Remote Executor: an HTTP service running on an attestable EC2 instance with NitroTPM that receives script-execution requests from GitHub Actions workflows, authenticates them with GitHub OIDC tokens, clones the requested repository at a pinned commit, generates a NitroTPM attestation document, executes the script asynchronously in an ephemeral, isolated Docker container, streams output, and serves output polling — all under enforced resource limits, error sanitization, and configuration validated at startup.

The encrypted request/response channel is specified in `request-encryption`; the per-container security posture in `container-security`. This capability covers the server endpoints, validation, cloning, attestation generation, execution lifecycle, and operational concerns.

## Requirements

### Requirement: HTTP execute endpoint

The GHA_Server SHALL listen for HTTP POST execution requests on a configured port, accept Execution_Request payloads in JSON, and return an Execution_ID for valid requests. It SHALL log all incoming requests with timestamps.

#### Scenario: Valid execution request accepted

- **WHEN** a valid Execution_Request is received
- **THEN** the server returns HTTP 200 OK with the Execution_ID in the response body

#### Scenario: Malformed payload rejected

- **WHEN** the request payload is malformed
- **THEN** the server returns HTTP 400 Bad Request

### Requirement: OIDC request authentication and validation

The Request_Validator SHALL require an `oidc_token` field in the decrypted body of each `/execute` request and authenticate it against GitHub's OIDC provider. The `/execution/{id}/output` endpoint SHALL NOT require an `oidc_token`; possession of the execution-bound Shared_Key authenticates it. The `/health` and `/attest` endpoints SHALL NOT require authentication.

#### Scenario: Missing OIDC token rejected

- **WHEN** the `oidc_token` field is missing from a decrypted `/execute` request body
- **THEN** the request is rejected with HTTP 401 Unauthorized indicating the token is required

#### Scenario: Token signature and claims verified

- **WHEN** an `/execute` request is processed
- **THEN** the validator fetches and caches the JWKS from `https://token.actions.githubusercontent.com/.well-known/jwks` (refreshing on unknown key ID), verifies the token signature, and validates the `iss`, `aud` (against Expected_Audience), and `exp` claims
- **AND** a signature, `iss`, `aud`, or `exp` failure is rejected with HTTP 401

#### Scenario: Repository authorization

- **WHEN** the `repository` claim does not match an entry in Allowed_Repositories, or does not match the request's `repository_url`
- **THEN** the request is rejected with HTTP 403 Forbidden

#### Scenario: Commit hash binding

- **WHEN** the request's `commit_hash` does not match the OIDC token `sha` claim (compared case-insensitively, after token validation and before cloning)
- **THEN** the request is rejected with HTTP 403 Forbidden indicating commit hash mismatch

#### Scenario: Optional branch and protected-ref gating

- **WHEN** Allowed_Branches is configured and the `ref` claim matches no allowed pattern, or `REQUIRE_PROTECTED_REF` is true and `ref_protected` is not `"true"`
- **THEN** the request is rejected with HTTP 403 Forbidden
- **AND** when neither is configured, the corresponding validation is skipped

#### Scenario: Input sanitization on script path

- **WHEN** `validate_script_path` is called
- **THEN** it rejects paths containing null bytes, absolute paths (starting with `/` or `\`), and path-traversal sequences, returning HTTP 400 for missing required fields

### Requirement: Repository cloning

When an Execution_Request is validated, the Repository_Client SHALL shallow-clone (`git clone --depth 1`) the specified repository at the specified commit into a temporary directory, authenticating with a credential mechanism that does not expose the token in subprocess command-line arguments. After a successful clone it SHALL strip the token from the remote URL and remove the `.git` directory before the workspace is mounted into the Execution_Container.

#### Scenario: Clone, checkout, and token stripping

- **WHEN** a repository is cloned successfully
- **THEN** the exact requested commit is checked out, the script file is verified to exist, the GitHub token is stripped via `git remote set-url`, and the `.git` directory is removed before mounting
- **AND** the token never appears in `/proc/<pid>/cmdline` of the `git clone` subprocess

#### Scenario: Symlink and path-escape rejection

- **WHEN** the script path is a symlink, or its `os.path.realpath()` resolution escapes the clone directory
- **THEN** the request is rejected with an error indicating symlinks/paths outside the repository are not permitted

#### Scenario: Clone directory cleanup on unexpected failure

- **WHEN** an unexpected exception occurs after cloning but before ownership is handed to the Script_Executor
- **THEN** a `finally` block removes the clone directory with `shutil.rmtree(ignore_errors=True)`; if ownership was already handed off, the directory is left for the Script_Executor to clean up

### Requirement: Attestation document generation and execution initiation

When a script is successfully retrieved, the Attestation_Generator SHALL generate a NitroTPM-signed Attestation_Document including PCR measurements, a timestamp, and documented `user_data`, store it against the Execution_ID, and the Script_Executor SHALL then initiate execution.

#### Scenario: Documented user_data fields present

- **WHEN** an execution attestation document is generated
- **THEN** its `user_data` includes `repository_url`, `commit_hash`, `script_path`, `timestamp`, `script_env_hash`, and `execution_id`
- **AND** `script_env_hash` is the SHA-256 hex digest of the canonicalized `script_env` (keys sorted, JSON with no whitespace), or of `{}` when `script_env` is empty

#### Scenario: execution_id binds the attestation

- **WHEN** the `/execute` response and any `/execution/{id}/output` response include an attestation document
- **THEN** the `execution_id` in its `user_data` matches the execution record (the response-body id for `/execute`, the URL path id for output polling)

#### Scenario: Attestation failure recorded

- **WHEN** attestation generation fails
- **THEN** the server records an attestation error for the Execution_ID

### Requirement: Asynchronous ephemeral container execution

The Script_Executor SHALL create a new Execution_Container from the configured Container_Image for each execution, never reuse a container, and remove the container and its resources when execution completes, fails, or times out. The Output_Collector SHALL capture stdout, stderr, and exit code against the Execution_ID. Executions SHALL run concurrently without interference.

#### Scenario: One container per execution with cleanup

- **WHEN** a script execution completes or fails
- **THEN** the container (named from the Execution_ID) is removed and verified to no longer exist, and any dangling containers matching the naming convention are removed on startup

#### Scenario: Execution timeout enforced

- **WHEN** a script exceeds the maximum execution timeout (30 minutes)
- **THEN** the Script_Executor stops and removes the container and records a timeout error

#### Scenario: Output buffer bound

- **WHEN** combined stdout and stderr exceed `MAX_OUTPUT_SIZE_BYTES`
- **THEN** the Output_Collector truncates the output and marks the record as truncated, enforcing the configured limit passed at construction

### Requirement: Streaming output capture during execution

The Script_Executor SHALL start a daemon background log-streaming thread for each Execution_Container immediately after the container starts and before calling `container.wait()`, feeding incremental stdout/stderr chunks to the Output_Collector via `capture_output(execution_id, stream_name, chunk)` so polling clients observe partial output within one poll interval.

#### Scenario: Incremental capture concurrent with wait

- **WHEN** a container is running and producing output
- **THEN** the streaming thread captures chunks incrementally (without blocking `container.wait()`), the Script_Executor does not re-capture the full output in a batch after exit, and clients see partial output before the container exits

#### Scenario: Graceful termination and error handling

- **WHEN** the container exits, is stopped on timeout, or the Docker API raises a transient error
- **THEN** the daemon thread captures output up to termination, logs a warning and continues on transient errors, and terminates gracefully when the log stream ends

### Requirement: Output polling endpoint

The GHA_Server SHALL provide an HTTP POST `/execution/{id}/output` endpoint (POST because the body carries an encrypted payload) that, regardless of execution status (running, completed, failed, timed_out), returns HTTP 200 with the current status, Script_Output, the stored Attestation_Document, and a freshly generated Output_Attestation_Document.

#### Scenario: Output and attestations returned

- **WHEN** the output endpoint is polled for an existing execution
- **THEN** the response includes stdout, stderr, exit code, the base64 Attestation_Document, and a base64 Output_Attestation_Document whose `user_data` carries a SHA-256 digest of the current Script_Output and the `execution_id`

#### Scenario: Unknown execution

- **WHEN** the Execution_ID does not exist
- **THEN** the server returns HTTP 404 Not Found

#### Scenario: Output attestation failure is non-fatal

- **WHEN** Output_Attestation_Document generation fails
- **THEN** the server still returns the Script_Output and Attestation_Document with an error field indicating attestation failure

#### Scenario: Results retained

- **WHEN** an execution completes
- **THEN** results are retained for at least 1 hour

### Requirement: Script environment variable forwarding

The `/execute` decrypted payload SHALL accept an optional `script_env` dictionary of string key/value pairs to inject into the Execution_Container. The server SHALL sanitize it to string-only entries and reject any key matching the configurable Script_Env_Deny_List.

#### Scenario: Allowed variables forwarded

- **WHEN** `script_env` contains non-denied keys (e.g. `GITHUB_TOKEN`, `GITHUB_RUN_ID`)
- **THEN** they are passed as the container's `environment`; when `script_env` is absent or empty, the container is created with no additional environment

#### Scenario: Denied keys rejected

- **WHEN** a `script_env` key matches the deny-list (exact match, or prefix match for entries like `BASH_FUNC_*`; defaults include `BASH_ENV`, `ENV`, `PATH`, `LD_PRELOAD`, `LD_LIBRARY_PATH`, `NVIDIA_VISIBLE_DEVICES`, `NVIDIA_DRIVER_CAPABILITIES`, etc.)
- **THEN** the request is rejected indicating the key is not permitted, while `script_env_hash` still includes all requested keys (including denied ones)

### Requirement: Resource limits and concurrency control

The GHA_Server SHALL limit the number of concurrent Execution_Containers and enforce per-container resource limits. Concurrency SHALL be checked atomically against `MAX_CONCURRENT_EXECUTIONS` before creating a record, and requests at capacity SHALL be rejected with HTTP 503.

#### Scenario: Capacity rejection

- **WHEN** active executions (queued and running) are at `MAX_CONCURRENT_EXECUTIONS`
- **THEN** new requests are rejected with HTTP 503 Service Unavailable

#### Scenario: Script size limit

- **WHEN** the retrieved script file exceeds `MAX_SCRIPT_SIZE_BYTES`
- **THEN** the request is rejected with HTTP 413 Payload Too Large

#### Scenario: pids_limit enforced

- **WHEN** an Execution_Container is created
- **THEN** `pids_limit` is set to `MAX_CONTAINER_PIDS` (default 256, validated as a positive integer at startup) to prevent fork bombs

#### Scenario: Request body size limits

- **WHEN** a request exceeds `MAX_REQUEST_BODY_BYTES` (default 1 MB)
- **THEN** it is rejected with HTTP 413 before JSON parsing, base64 decoding, or decryption; the `encrypted_payload` (default 512 KB), `client_public_key` (max 2048 bytes), and decrypted payload (default 256 KB) limits are likewise enforced at their respective stages with HTTP 400

#### Scenario: Expired records and unbounded growth cleaned up

- **WHEN** periodic cleanup runs
- **THEN** `cleanup_expired` removes output and encryption context for expired records, the rate limiter evicts stale per-IP entries, and execution-duration history is bounded (e.g. a fixed-length deque)

### Requirement: Error handling and log sanitization

The GHA_Server SHALL log errors with severity levels and per-request context isolated via `contextvars.ContextVar`, and SHALL pass all subprocess stderr, exception messages, and external tool output through the Log_Sanitizer before logging or returning them.

#### Scenario: Sensitive data redacted

- **WHEN** subprocess stderr or an error response is produced
- **THEN** the Log_Sanitizer redacts GitHub tokens (`ghp_*`, `ghs_*`, `github_pat_*`), credentialed URLs, Authorization values, absolute paths, token-bearing env assignments, and ASCII control characters; raw subprocess stderr is never logged or returned

#### Scenario: Log injection prevented

- **WHEN** user-controlled fields are logged
- **THEN** they are JSON-escaped/`repr()`-wrapped and capped (e.g. 256 chars with a `[truncated]` suffix); raw nonce values are not logged (only a truncated hash/prefix is)

#### Scenario: Categorized encrypted error envelopes

- **WHEN** a post-decryption application error is returned to the caller
- **THEN** the encrypted error envelope contains only a categorized description (e.g. `clone_failed`, `attestation_failed`) without raw stderr, absolute paths, or internal configuration

### Requirement: Configuration management

The GHA_Server SHALL read all operational configuration (port, Allowed_Repositories, Expected_Audience, GitHub token, timeouts, resource limits, Container_Image, max concurrency) from configuration and validate it at startup, failing to start with a descriptive error if required configuration is missing or invalid. Boolean values SHALL use strict parsing.

#### Scenario: Strict boolean parsing

- **WHEN** a boolean configuration value is not in the recognized set (`true`/`1`/`yes` or `false`/`0`/`no`, case-insensitive)
- **THEN** the server fails to start with an error naming the key, the invalid value, and the accepted values (e.g. `treu`, `enabled`, `on` all fail)

#### Scenario: Docker daemon required

- **WHEN** the Docker daemon is not accessible at startup
- **THEN** the server fails to start with a descriptive error

#### Scenario: NitroTPM required in production

- **WHEN** NitroTPM is unavailable at startup
- **THEN** the server exits non-zero unless `ALLOW_NO_TPM=true` (strict boolean, default false), in which case it logs a prominent warning that attestation guarantees are disabled and starts serving; it never silently degrades

### Requirement: Health endpoint

The GHA_Server SHALL provide a rate-limited HTTP GET `/health` endpoint returning a simple healthy/unhealthy status without exposing Docker availability, disk space, or active execution counts.

#### Scenario: Health status

- **WHEN** `/health` is polled
- **THEN** it returns HTTP 200 when healthy and HTTP 503 when unhealthy, with no internal detail beyond the status

### Requirement: GPU passthrough at runtime

When `ENABLE_GPU` is true, the Script_Executor SHALL create each Execution_Container with `runtime="nvidia"` and the `NVIDIA_VISIBLE_DEVICES` (from `GPU_DEVICES`, default `all`) and `NVIDIA_DRIVER_CAPABILITIES` (default `compute,utility`) environment variables, using CDI mode exclusively. When `ENABLE_GPU` is false, no GPU access SHALL be granted. All existing container security constraints SHALL remain enforced.

#### Scenario: GPU enabled

- **WHEN** `ENABLE_GPU` is true and a container is created
- **THEN** `runtime="nvidia"` and the server-configured `NVIDIA_VISIBLE_DEVICES`/`NVIDIA_DRIVER_CAPABILITIES` are set (overriding any caller-supplied values), without adding `/dev/nvidia*` mappings, `SYS_ADMIN`, or `--privileged`

#### Scenario: GPU disabled

- **WHEN** `ENABLE_GPU` is false or unset
- **THEN** the container is created with no `runtime="nvidia"` and no NVIDIA environment variables, and has no GPU access

#### Scenario: Startup verification when enabled

- **WHEN** `ENABLE_GPU` is true at startup
- **THEN** the server verifies the `nvidia` runtime is registered (failing to start otherwise), warns if no CDI specs are found, and verifies functionality by creating and removing a test `runtime="nvidia"` container

#### Scenario: GPU posture attested

- **WHEN** GPU is enabled or disabled
- **THEN** the attestation `user_data` includes `gpu_enabled: true` or `gpu_enabled: false` per the documented schema
