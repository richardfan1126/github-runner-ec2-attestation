"""HTTP server for GitHub Actions Remote Executor"""
import asyncio
import logging
import os
import re
import shutil
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, Tuple

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from src.config import ServerConfig
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.repository import RepositoryClient
from src.attestation import AttestationGenerator
from src.script_executor import ScriptExecutor
from src.validation import RequestValidator
from src.models import ExecutionStatus, CloneResult
from src.logging_config import set_log_context, clear_log_context, sanitize_for_logging, sanitize_error_message, sanitize_log_message, sanitize_nonce_for_logging, truncate_field
from src.nonce_cache import NonceCache

logger = logging.getLogger(__name__)


def create_error_response(
    error_code: str,
    message: str,
    details: dict = None
) -> dict:
    """
    Create consistent error response format
    
    Args:
        error_code: Machine-readable error code
        message: Human-readable error message (should not expose internal details)
        details: Optional additional context (should not expose internal details)
    
    Returns:
        Dictionary with consistent error response structure
    """
    return {
        "error": error_code,
        "message": message,
        "details": details or {}
    }


def _encrypted_error_response(encryption_manager, shared_key, error_code, error_status_code, message, details=None):
    """
    Create an encrypted error envelope returned with HTTP 200.
    
    After successful decryption (Shared_Key established), all application errors
    are returned as encrypted JSON envelopes so observers cannot distinguish
    errors from successes at the transport layer.
    
    Args:
        encryption_manager: The encryption manager instance
        shared_key: The shared key for this session
        error_code: Machine-readable error code
        error_status_code: Application-level error status code (inside envelope)
        message: Human-readable error message
        details: Optional additional context
    
    Returns:
        JSONResponse with HTTP 200 containing encrypted error envelope
    """
    import base64
    error_payload = {
        "error": error_code,
        "error_code": error_status_code,
        "error_details": details or {},
        "message": message,
    }
    encrypted_response = encryption_manager.encrypt_response(error_payload, shared_key)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "encrypted_response": base64.b64encode(encrypted_response).decode('utf-8')
        }
    )


# Compiled regex for URL-safe nonce characters
_NONCE_PATTERN = re.compile(r'^[a-zA-Z0-9._~-]+$')


def _validate_nonce_strict(nonce_value, encryption_manager, shared_key, endpoint_name: str):
    """
    Validate nonce type, length, and format.

    Returns None if valid, or an encrypted error JSONResponse if invalid.
    Checks (in order):
      1. Type must be str (reject int, bool, list, dict, None)
      2. Length must be 16–256 characters inclusive
      3. Must contain only URL-safe characters: [a-zA-Z0-9._~-]

    Requirements: 45.10, 45.11, 45.12
    """
    # Type check
    if not isinstance(nonce_value, str):
        logger.warning(
            f"Non-string nonce type on {endpoint_name}: {type(nonce_value).__name__}"
        )
        return _encrypted_error_response(
            encryption_manager, shared_key,
            "invalid_nonce", 400,
            "Nonce must be a string"
        )

    # Length check
    if len(nonce_value) < 16 or len(nonce_value) > 256:
        logger.warning(
            f"Nonce length out of range on {endpoint_name}: {len(nonce_value)} chars"
        )
        return _encrypted_error_response(
            encryption_manager, shared_key,
            "invalid_nonce", 400,
            "Nonce must be between 16 and 256 characters"
        )

    # Format check: URL-safe characters only
    if not _NONCE_PATTERN.match(nonce_value):
        logger.warning(
            f"Nonce contains invalid characters on {endpoint_name}"
        )
        return _encrypted_error_response(
            encryption_manager, shared_key,
            "invalid_nonce", 400,
            "Nonce must contain only URL-safe characters: letters, digits, '.', '_', '~', '-'"
        )

    return None


def _categorize_clone_error(status_code: int) -> str:
    """
    Map a clone error status code to a categorized description for error envelopes.

    Error envelopes must not contain raw stderr or paths — only a safe,
    categorized description of what went wrong.

    Requirements: 7.15, 7.18
    """
    if status_code == 401:
        return "clone_authentication_failed"
    elif status_code == 404:
        return "clone_target_not_found"
    elif status_code == 400:
        return "clone_invalid_request"
    else:
        return "clone_failed"


class RateLimiter:
    """Rate limiter per source IP address"""
    
    def __init__(self, max_requests: int, window_seconds: int):
        """
        Initialize rate limiter
        
        Args:
            max_requests: Maximum requests per IP in window
            window_seconds: Time window in seconds
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: Dict[str, list] = defaultdict(list)
        self._lock = Lock()
    
    def check_rate_limit(self, ip_address: str) -> Tuple[bool, int]:
        """
        Check if IP address is within rate limit
        
        Args:
            ip_address: Source IP address
        
        Returns:
            Tuple of (allowed, remaining_requests)
        """
        now = time.time()
        cutoff = now - self.window_seconds
        
        with self._lock:
            # Remove old requests outside window
            self._requests[ip_address] = [
                req_time for req_time in self._requests[ip_address]
                if req_time > cutoff
            ]
            
            # Check if under limit
            current_count = len(self._requests[ip_address])
            if current_count >= self.max_requests:
                return False, 0
            
            # Add current request
            self._requests[ip_address].append(now)
            remaining = self.max_requests - current_count - 1
            
            return True, remaining

    def cleanup_stale_ips(self) -> int:
        """
        Remove IP entries that have no requests within the current window.

        An IP is considered stale when all of its recorded timestamps fall
        outside the current rate-limit window, meaning the IP has made no
        recent requests and its entry no longer contributes to rate limiting.
        Removing these entries prevents the ``_requests`` dict from growing
        without bound under distributed or spoofed-source traffic.

        Returns:
            Number of IP entries removed.

        Requirements: 8.5
        """
        now = time.time()
        cutoff = now - self.window_seconds
        stale_ips = []

        with self._lock:
            for ip, timestamps in list(self._requests.items()):
                # Keep only timestamps still inside the window
                recent = [t for t in timestamps if t > cutoff]
                if not recent:
                    stale_ips.append(ip)
                else:
                    # Update the list in-place to drop expired timestamps
                    self._requests[ip] = recent

            for ip in stale_ips:
                del self._requests[ip]

        return len(stale_ips)


def create_app(config: ServerConfig, docker_client=None, encryption_manager=None) -> FastAPI:
    """
    Create and configure FastAPI application
    
    Args:
        config: Server configuration
        docker_client: Optional pre-initialized Docker client. If None, creates one using the rootless Docker socket.
        encryption_manager: Optional pre-initialized EncryptionManager instance.
    
    Returns:
        Configured FastAPI application
    """
    import docker as docker_lib

    # Cleanup interval in seconds (configurable, default 60s)
    cleanup_interval_seconds = int(os.environ.get("CLEANUP_INTERVAL_SECONDS", "60"))

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """Manage background tasks for the application lifecycle."""
        async def periodic_cleanup():
            """Periodically invoke cleanup_expired on the ExecutionManager and
            cleanup_stale_ips on the RateLimiter."""
            while True:
                try:
                    await asyncio.sleep(cleanup_interval_seconds)
                    removed = application.state.execution_manager.cleanup_expired()
                    if removed > 0:
                        logger.info(f"Periodic cleanup removed {removed} expired execution(s)")
                    stale_ips = application.state.rate_limiter.cleanup_stale_ips()
                    if stale_ips > 0:
                        logger.debug(f"Periodic cleanup removed {stale_ips} stale IP(s) from rate limiter")
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("Error during periodic cleanup")

        cleanup_task = asyncio.create_task(periodic_cleanup())
        yield
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    app = FastAPI(
        title="GitHub Actions Remote Executor",
        description="Attestable script execution service for GitHub Actions",
        version="1.0.0",
        lifespan=lifespan,
    )
    
    # Initialize Docker client if not provided
    if docker_client is None:
        try:
            uid = os.getuid()
            docker_client = docker_lib.DockerClient(
                base_url=f"unix:///run/user/{uid}/docker.sock"
            )
        except docker_lib.errors.DockerException:
            logger.warning("Docker daemon not available; ScriptExecutor will not function")
            docker_client = None
    
    # Initialize components
    output_collector = OutputCollector(max_output_size_bytes=config.max_output_size_bytes)
    execution_manager = ExecutionManager(config.output_retention_hours, encryption_manager=encryption_manager, output_collector=output_collector)
    repository_client = RepositoryClient(config.temp_storage_path)
    attestation_generator = AttestationGenerator(config.tpm_attest_path)
    script_executor = ScriptExecutor(
        docker_client=docker_client,
        container_image=config.container_image,
        memory_limit=config.container_memory_limit,
        cpu_limit=config.container_cpu_limit,
        timeout_seconds=config.execution_timeout_seconds,
        execution_manager=execution_manager,
        output_collector=output_collector,
        temp_storage_path=config.temp_storage_path,
    )
    request_validator = RequestValidator(
        allowed_repositories=config.allowed_repositories,
        expected_audience=config.expected_audience,
        allowed_branches=config.allowed_branches,
        require_protected_ref=config.require_protected_ref,
    )
    rate_limiter = RateLimiter(
        config.rate_limit_per_ip,
        config.rate_limit_window_seconds
    )
    
    # Store components in app state
    app.state.config = config
    app.state.execution_manager = execution_manager
    app.state.output_collector = output_collector
    app.state.repository_client = repository_client
    app.state.attestation_generator = attestation_generator
    app.state.script_executor = script_executor
    app.state.request_validator = request_validator
    app.state.rate_limiter = rate_limiter
    app.state.encryption_manager = encryption_manager
    app.state.nonce_cache = NonceCache(config.nonce_cache_ttl_seconds)
    
    # Request logging middleware (exclude tokens)
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Log all requests excluding sensitive tokens"""
        start_time = time.time()
        
        # Generate request ID for tracing
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        # Set log context
        set_log_context(request_id=request_id)
        
        # Log request (will exclude token in endpoint handlers)
        logger.info(
            f"Request: {request.method} {request.url.path} from {request.client.host}"
        )
        
        try:
            response = await call_next(request)
            
            # Log response time
            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                f"Response: {request.method} {request.url.path} "
                f"status={response.status_code} duration={duration_ms:.2f}ms"
            )
            
            return response
        finally:
            # Clear log context after request
            clear_log_context()
    
    # Rate limiting middleware
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        """Apply rate limiting per source IP"""
        ip_address = request.client.host
        allowed, remaining = rate_limiter.check_rate_limit(ip_address)
        
        if not allowed:
            logger.warning(f"Rate limit exceeded for IP: {ip_address}")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content=create_error_response(
                    "rate_limit_exceeded",
                    "Too many requests. Please try again later.",
                    {"retry_after_seconds": config.rate_limit_window_seconds}
                )
            )
        
        response = await call_next(request)
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(config.rate_limit_per_ip)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Window"] = str(config.rate_limit_window_seconds)
        
        return response
    
    # Error handling middleware
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Handle unexpected errors without exposing internal details"""
        # Get request ID if available
        request_id = getattr(request.state, 'request_id', '-')
        
        # Log error with full context and stack trace
        logger.error(
            f"Unexpected error processing {request.method} {request.url.path}: {exc}",
            exc_info=True
        )
        
        # Return sanitized error message
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=create_error_response(
                "internal_server_error",
                "An unexpected error occurred. Please try again later."
            )
        )
    
    # Add routes
    add_routes(app)
    
    return app


def add_routes(app: FastAPI) -> None:
    """Add all API routes to the application"""
    
    @app.post("/execute")
    async def execute_script(request: Request):
        """
        Execute a script from a GitHub repository

        Request body (encrypted envelope):
        {
            "encrypted_payload": "base64-encoded-ciphertext",
            "client_public_key": "base64-encoded-client-public-key"
        }

        Decrypted payload:
        {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "abc123...",
            "script_path": "scripts/build.sh",
            "github_token": "ghp_...",
            "oidc_token": "eyJhbGciOiJSUzI1NiIs...",
            "nonce": "optional-client-nonce"
        }

        Returns (encrypted with Shared_Key):
        {
            "execution_id": "uuid",
            "attestation_document": "base64-encoded-cbor",
            "status": "queued"
        }
        """
        import base64

        start_time = time.time()
        phase_times = {}
        shared_key = None

        try:
            # Check encryption manager is available
            encryption_manager = request.app.state.encryption_manager
            if encryption_manager is None:
                logger.error("Encryption manager not configured")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=create_error_response(
                        "encryption_not_configured",
                        "Server encryption is not configured"
                    )
                )

            # Check raw request body size before JSON parsing
            raw_body = await request.body()
            config = request.app.state.config
            if len(raw_body) > config.max_request_body_bytes:
                logger.warning(
                    f"Request body too large: {len(raw_body)} bytes exceeds limit of {config.max_request_body_bytes}"
                )
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=create_error_response(
                        "request_body_too_large",
                        "Request body exceeds maximum allowed size"
                    )
                )

            # Parse outer JSON envelope
            import json as json_module
            try:
                outer_body = json_module.loads(raw_body)
            except Exception as e:
                logger.warning(f"Malformed request body: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "malformed_request",
                        "Request body must be valid JSON"
                    )
                )

            encrypted_payload_b64 = outer_body.get("encrypted_payload")
            client_public_key_b64 = outer_body.get("client_public_key")

            if not encrypted_payload_b64 or not client_public_key_b64:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "malformed_request",
                        "Request must include encrypted_payload and client_public_key"
                    )
                )

            # Check encrypted_payload and client_public_key sizes
            if len(encrypted_payload_b64) > config.max_encrypted_payload_bytes:
                logger.warning(
                    f"Encrypted payload too large: {len(encrypted_payload_b64)} bytes"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "payload_too_large",
                        "Encrypted payload exceeds maximum allowed size"
                    )
                )

            if len(client_public_key_b64) > 2048:
                logger.warning(
                    f"Client public key too large: {len(client_public_key_b64)} bytes"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "client_key_too_large",
                        "Client public key exceeds maximum allowed size"
                    )
                )

            # Decode base64 fields with strict validation
            import binascii
            try:
                encrypted_payload = base64.b64decode(encrypted_payload_b64, validate=True)
            except (binascii.Error, ValueError) as e:
                logger.warning(f"Invalid base64 in encrypted_payload: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "invalid_base64",
                        "Invalid base64 encoding in encrypted_payload"
                    )
                )
            try:
                client_public_key = base64.b64decode(client_public_key_b64, validate=True)
            except (binascii.Error, ValueError) as e:
                logger.warning(f"Invalid base64 in client_public_key: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "invalid_base64",
                        "Invalid base64 encoding in client_public_key"
                    )
                )

            # Decrypt the request payload
            try:
                body, shared_key = encryption_manager.decrypt_request(
                    encrypted_payload, client_public_key
                )
            except (ValueError, Exception) as e:
                logger.warning(f"Decryption failed: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "decryption_failed",
                        "Failed to decrypt request payload"
                    )
                )

            # Check decrypted payload size
            import json as json_mod
            decrypted_json = json_mod.dumps(body)
            if len(decrypted_json) > config.max_decrypted_payload_bytes:
                logger.warning(
                    f"Decrypted payload too large: {len(decrypted_json)} bytes"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "decrypted_payload_too_large",
                        "Decrypted payload exceeds maximum allowed size"
                    )
                )

            # Mandatory nonce validation (must occur BEFORE nonce cache duplicate check)
            request_nonce = body.get("nonce")
            if request_nonce is None or (isinstance(request_nonce, str) and not request_nonce.strip()):
                logger.warning("Missing or empty nonce on /execute request")
                return _encrypted_error_response(
                    encryption_manager, shared_key,
                    "missing_nonce", 400,
                    "Nonce is required and must be a non-empty string"
                )

            # Strict nonce type, length, and format validation (Requirements: 45.10, 45.11, 45.12)
            nonce_error = _validate_nonce_strict(request_nonce, encryption_manager, shared_key, "/execute")
            if nonce_error is not None:
                return nonce_error

            # OIDC authentication from decrypted body
            oidc_start = time.time()
            validator = request.app.state.request_validator
            oidc_token = body.get("oidc_token")
            oidc_result = validator.validate_oidc_token_from_body(oidc_token)

            if not oidc_result.valid:
                repo_claim = (oidc_result.claims or {}).get("repository", "unknown")
                logger.warning(
                    f"OIDC validation failed: status={oidc_result.status_code}, "
                    f"repository={repo_claim}, error={oidc_result.error_message}"
                )
                return _encrypted_error_response(
                    encryption_manager, shared_key,
                    "oidc_authentication_failed", oidc_result.status_code,
                    oidc_result.error_message or "Authentication failed"
                )

            logger.info(
                f"OIDC validation succeeded: repository={oidc_result.claims.get('repository')}"
            )
            phase_times['oidc_auth'] = (time.time() - oidc_start) * 1000

            # Repository claim binding: verify OIDC repository matches request repository_url
            oidc_repo_claim = oidc_result.claims.get("repository", "")
            request_repo_url = body.get("repository_url", "")
            # Extract owner/repo from URL: strip trailing slashes, .git suffix, take last two segments
            stripped_url = request_repo_url.rstrip("/")
            if stripped_url.endswith(".git"):
                stripped_url = stripped_url[:-4]
            url_parts = stripped_url.split("/")
            repo_from_url = "/".join(url_parts[-2:]) if len(url_parts) >= 2 else ""

            if oidc_repo_claim != repo_from_url:
                logger.warning(
                    f"Repository mismatch: OIDC claim={oidc_repo_claim}, "
                    f"request URL={request_repo_url} (parsed={repo_from_url})"
                )
                return _encrypted_error_response(
                    encryption_manager, shared_key,
                    "repository_mismatch", 403,
                    "OIDC token repository claim does not match request repository_url"
                )

            # Anti-replay nonce cache duplicate check
            nonce_cache = request.app.state.nonce_cache
            if not nonce_cache.check_and_store(request_nonce):
                logger.warning(f"Duplicate nonce detected on /execute: {sanitize_nonce_for_logging(request_nonce)}")
                return _encrypted_error_response(
                    encryption_manager, shared_key,
                    "duplicate_nonce", 400,
                    "Duplicate nonce detected; request rejected as potential replay"
                )

            # Log request details (exclude token)
            logger.info(
                f"Execution request: repo={body.get('repository_url')}, "
                f"commit={body.get('commit_hash')}, path={body.get('script_path')}"
            )
            
            # Validate request
            validation_start = time.time()
            validation_result = validator.validate_execution_request(body)
            
            if not validation_result.valid:
                logger.warning(f"Validation failed: {truncate_field(str(validation_result.errors))}")
                return _encrypted_error_response(
                    encryption_manager, shared_key,
                    "validation_failed", 400,
                    "Request validation failed",
                    {"errors": validation_result.errors}
                )
            
            phase_times['validation'] = (time.time() - validation_start) * 1000
            
            # Authenticate and fetch file
            auth_start = time.time()
            repo_client = request.app.state.repository_client
            
            auth_result = repo_client.authenticate(body['github_token'])
            if not auth_result.success:
                logger.warning(f"Authentication failed: {auth_result.error_message}")
                return _encrypted_error_response(
                    encryption_manager, shared_key,
                    "authentication_failed", 401,
                    auth_result.error_message or "GitHub authentication failed"
                )
            
            phase_times['authentication'] = (time.time() - auth_start) * 1000
            
            # Clone repository
            fetch_start = time.time()
            clone_result = None
            execution_handed_off = False
            try:
                from src.repository import GitHubAPIError
                try:
                    clone_result = repo_client.clone_repo(
                        body['repository_url'],
                        body['commit_hash'],
                        body['github_token']
                    )
                    # Validate script exists in cloned repo
                    repo_client.validate_script_exists(
                        clone_result.clone_path,
                        body['script_path']
                    )
                    clone_result = CloneResult(
                        clone_path=clone_result.clone_path,
                        script_path=body['script_path']
                    )
                except GitHubAPIError as e:
                    logger.warning(f"GitHub API error: {sanitize_log_message(e.message)}")
                    if clone_result:
                        repo_client.cleanup_clone(clone_result.clone_path)
                    return _encrypted_error_response(
                        encryption_manager, shared_key,
                        "github_api_error", e.status_code,
                        _categorize_clone_error(e.status_code)
                    )
                
                phase_times['file_retrieval'] = (time.time() - fetch_start) * 1000
                
                # Check script file size against MAX_SCRIPT_SIZE_BYTES
                script_full_path = os.path.join(clone_result.clone_path, clone_result.script_path)
                script_size = os.path.getsize(script_full_path)
                if script_size > config.max_script_size_bytes:
                    logger.warning(
                        f"Script file too large: {script_size} bytes exceeds limit of {config.max_script_size_bytes} bytes"
                    )
                    return _encrypted_error_response(
                        encryption_manager, shared_key,
                        "script_too_large", 413,
                        "Script file exceeds maximum allowed size"
                    )
                
                # Extract and sanitize script_env from decrypted body
                script_env = body.get('script_env') or {}
                script_env = {str(k): str(v) for k, v in script_env.items() if isinstance(k, str) and isinstance(v, str)}
                
                # Check script_env keys against deny-list (Requirements: 52.7, 52.8, 52.9)
                if script_env:
                    deny_list = config.script_env_deny_list
                    # Separate exact-match entries from prefix-match entries (ending with '*')
                    exact_deny = {e for e in deny_list if not e.endswith('*')}
                    prefix_deny = [e[:-1] for e in deny_list if e.endswith('*')]
                    
                    denied_keys = []
                    for key in script_env:
                        if key in exact_deny:
                            denied_keys.append(key)
                        else:
                            for prefix in prefix_deny:
                                if key.startswith(prefix):
                                    denied_keys.append(key)
                                    break
                    
                    if denied_keys:
                        logger.warning(
                            f"script_env contains denied keys: {denied_keys}"
                        )
                        return _encrypted_error_response(
                            encryption_manager, shared_key,
                            "denied_env_key", 400,
                            "script_env contains denied environment variable keys",
                            {"denied_keys": denied_keys}
                        )
                
                # Create execution record with atomic concurrency check
                exec_manager = request.app.state.execution_manager
                execution_record, accepted = exec_manager.try_create_execution(
                    body['repository_url'],
                    body['commit_hash'],
                    body['script_path'],
                    config.execution_timeout_seconds,
                    max_concurrent=config.max_concurrent_executions,
                    repository=oidc_repo_claim
                )

                if not accepted:
                    return _encrypted_error_response(
                        encryption_manager, shared_key,
                        "at_capacity", 503,
                        "Server is at maximum execution capacity. Please try again later."
                    )
                
                # Generate attestation with execution_id in user_data
                attestation_start = time.time()
                attestation_gen = request.app.state.attestation_generator
                
                attestation_doc, attestation_error = attestation_gen.generate_attestation(
                    body['repository_url'],
                    body['commit_hash'],
                    body['script_path'],
                    nonce=body.get('nonce'),
                    script_env=script_env,
                    execution_id=execution_record.execution_id,
                )
                
                if attestation_error:
                    logger.error(
                        f"Attestation generation failed: {attestation_error.context}"
                    )
                    return _encrypted_error_response(
                        encryption_manager, shared_key,
                        "attestation_failed", 500,
                        "Failed to generate attestation document"
                    )
                
                phase_times['attestation'] = (time.time() - attestation_start) * 1000
                
                # Store encryption context for this execution
                encryption_manager.store_encryption_context(
                    execution_record.execution_id, shared_key
                )
                
                # Set log context with execution ID
                set_log_context(execution_id=execution_record.execution_id)
                
                logger.info(f"Created execution record: {execution_record.execution_id}")
                logger.info(f"Attestation generated for execution: {execution_record.execution_id}")
                
                # Prepare response
                response_data = {
                    "execution_id": execution_record.execution_id,
                    "attestation_document": base64.b64encode(attestation_doc.signature).decode('utf-8'),
                    "status": execution_record.status.value
                }
                
                # Initiate async execution
                executor = request.app.state.script_executor
                
                executor.execute_async(execution_record.execution_id, clone_result.clone_path, clone_result.script_path, script_env=script_env)
                execution_handed_off = True
                
                logger.info(f"Initiated async execution: {execution_record.execution_id}")
                
                # Log phase durations
                total_time = (time.time() - start_time) * 1000
                logger.info(
                    f"Request processing phases for {execution_record.execution_id}: "
                    f"oidc_auth={phase_times.get('oidc_auth', 0):.2f}ms, "
                    f"validation={phase_times.get('validation', 0):.2f}ms, "
                    f"auth={phase_times.get('authentication', 0):.2f}ms, "
                    f"fetch={phase_times.get('file_retrieval', 0):.2f}ms, "
                    f"attestation={phase_times.get('attestation', 0):.2f}ms, "
                    f"total={total_time:.2f}ms"
                )
                
                # Encrypt response with shared key
                encrypted_response = encryption_manager.encrypt_response(
                    response_data, shared_key
                )
                
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={
                        "encrypted_response": base64.b64encode(encrypted_response).decode('utf-8')
                    }
                )
            finally:
                # Post-clone cleanup: remove clone directory if execution was NOT handed off
                if clone_result and not execution_handed_off:
                    shutil.rmtree(clone_result.clone_path, ignore_errors=True)
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in execute endpoint: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=create_error_response(
                    "internal_server_error",
                    "An unexpected error occurred"
                )
            )

    
    @app.post("/execution/{execution_id}/output")
    async def get_execution_output(execution_id: str, request: Request):
        """
        Get execution status and output (encrypted request/response).

        Authentication is provided by possession of the execution-bound
        Shared_Key itself — only the original caller who performed the
        PQ_Hybrid_KEM exchange during /execute possesses this key, so no
        separate OIDC token validation is required.

        Request body (encrypted with Shared_Key):
        {
            "encrypted_payload": "base64-encoded-ciphertext"
        }

        Decrypted request payload:
        {
            "nonce": "client-nonce (mandatory)",
            "offset": 0
        }

        Response (encrypted with Shared_Key):
        {
            "encrypted_response": "base64-encoded-ciphertext"
        }
        """
        import base64

        try:
            # Look up Encryption_Context for execution_id
            encryption_manager = request.app.state.encryption_manager
            if encryption_manager is None:
                logger.error("Encryption manager not configured")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=create_error_response(
                        "encryption_not_configured",
                        "Server encryption is not configured"
                    )
                )

            shared_key = encryption_manager.get_shared_key(execution_id)
            if shared_key is None:
                # Check if the execution itself exists before returning 400;
                # a cleaned-up execution should yield 404, not 400.
                exec_manager = request.app.state.execution_manager
                if not exec_manager.get_execution(execution_id):
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=create_error_response(
                            "execution_not_found",
                            f"Execution ID not found: {execution_id}"
                        )
                    )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "no_encryption_context",
                        "No encryption context available for this execution ID"
                    )
                )

            # Check raw request body size before JSON parsing
            raw_body = await request.body()
            config = request.app.state.config
            if len(raw_body) > config.max_request_body_bytes:
                logger.warning(
                    f"Request body too large on output endpoint: {len(raw_body)} bytes"
                )
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=create_error_response(
                        "request_body_too_large",
                        "Request body exceeds maximum allowed size"
                    )
                )

            # Parse outer JSON envelope
            import json as json_module
            try:
                outer_body = json_module.loads(raw_body)
            except Exception as e:
                logger.warning(f"Malformed request body on output endpoint: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "malformed_request",
                        "Request body must be valid JSON"
                    )
                )

            encrypted_payload_b64 = outer_body.get("encrypted_payload")
            if not encrypted_payload_b64:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "malformed_request",
                        "Request must include encrypted_payload"
                    )
                )

            # Decode base64 with strict validation
            import binascii
            try:
                encrypted_payload = base64.b64decode(encrypted_payload_b64, validate=True)
            except (binascii.Error, ValueError) as e:
                logger.warning(f"Invalid base64 in encrypted_payload on output endpoint: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "invalid_base64",
                        "Invalid base64 encoding in encrypted_payload"
                    )
                )

            # Decrypt the request payload using Shared_Key;
            # successful decryption proves caller identity (no separate OIDC validation needed)
            try:
                body = encryption_manager.decrypt_with_shared_key(encrypted_payload, shared_key)
            except (ValueError, Exception) as e:
                logger.warning(f"Decryption failed on output endpoint: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "decryption_failed",
                        "Failed to decrypt request payload"
                    )
                )

            # Mandatory nonce validation (must occur BEFORE nonce cache duplicate check)
            nonce = body.get("nonce")
            if nonce is None or (isinstance(nonce, str) and not nonce.strip()):
                logger.warning("Missing or empty nonce on /output request")
                return _encrypted_error_response(
                    encryption_manager, shared_key,
                    "missing_nonce", 400,
                    "Nonce is required and must be a non-empty string"
                )

            # Strict nonce type, length, and format validation (Requirements: 45.10, 45.11, 45.12)
            nonce_error = _validate_nonce_strict(nonce, encryption_manager, shared_key, "/output")
            if nonce_error is not None:
                return nonce_error

            # Anti-replay nonce cache duplicate check
            nonce_cache = request.app.state.nonce_cache
            if not nonce_cache.check_and_store(nonce):
                logger.warning(f"Duplicate nonce detected on /output: {sanitize_nonce_for_logging(nonce)}")
                return _encrypted_error_response(
                    encryption_manager, shared_key,
                    "duplicate_nonce", 400,
                    "Duplicate nonce detected; request rejected as potential replay"
                )

            # Extract offset from decrypted body
            offset = body.get("offset", 0)

            # Validate offset
            if not isinstance(offset, int) or offset < 0:
                return _encrypted_error_response(
                    encryption_manager, shared_key,
                    "invalid_offset", 400,
                    "Offset must be a non-negative integer",
                    {"offset": offset}
                )
            
            # Retrieve execution record
            exec_manager = request.app.state.execution_manager
            execution_record = exec_manager.get_execution(execution_id)
            
            if not execution_record:
                logger.warning(f"Execution not found: {execution_id}")
                return _encrypted_error_response(
                    encryption_manager, shared_key,
                    "execution_not_found", 404,
                    f"Execution ID not found: {execution_id}"
                )

            # Retrieve output
            output_collector = request.app.state.output_collector
            
            try:
                output_data = output_collector.get_output(execution_id, offset)
            except ValueError as e:
                # Execution exists but no output buffer yet (very early in lifecycle)
                # Return empty output with current status
                logger.debug(f"No output buffer yet for {execution_id}: {e}")
                output_data = None
            
            # Build response
            if output_data:
                stdout = output_data.stdout
                stderr = output_data.stderr
                exit_code = output_data.exit_code
                response_data = {
                    "execution_id": execution_id,
                    "status": execution_record.status.value,
                    "stdout": stdout,
                    "stderr": stderr,
                    "stdout_offset": output_data.stdout_offset,
                    "stderr_offset": output_data.stderr_offset,
                    "complete": output_data.complete,
                    "exit_code": exit_code
                }
            else:
                # No output yet - return empty
                stdout = ""
                stderr = ""
                exit_code = None
                response_data = {
                    "execution_id": execution_id,
                    "status": execution_record.status.value,
                    "stdout": stdout,
                    "stderr": stderr,
                    "stdout_offset": 0,
                    "stderr_offset": 0,
                    "complete": False,
                    "exit_code": exit_code
                }

            # Generate Output_Attestation_Document on every poll response
            script_output = (
                f"stdout:{stdout}\n"
                f"stderr:{stderr}\n"
                f"exit_code:{exit_code}"
            )

            attestation_gen = request.app.state.attestation_generator
            attestation_bytes, attestation_error_msg = (
                attestation_gen.generate_output_attestation(
                    script_output, nonce=nonce, execution_id=execution_id
                )
            )

            if attestation_bytes is not None:
                response_data["output_attestation_document"] = (
                    base64.b64encode(attestation_bytes).decode("utf-8")
                )
            else:
                response_data["output_attestation_document"] = None
                response_data["attestation_error"] = attestation_error_msg
            
            # Encrypt response with shared key
            encrypted_response = encryption_manager.encrypt_response(
                response_data, shared_key
            )

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "encrypted_response": base64.b64encode(encrypted_response).decode("utf-8")
                }
            )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in output endpoint for {execution_id}: {e}",
                exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=create_error_response(
                    "internal_server_error",
                    "An unexpected error occurred"
                )
            )

    @app.get("/attest")
    async def attest(request: Request, nonce: str = None):
        """
        Return an attestation document with SHA-256 fingerprint of the
        Server_Public_Key in the attestation's public_key field, and the
        full composite Server_Public_Key as a separate JSON field.
        No authentication required.

        Query parameters:
        - nonce (optional): Client-provided nonce for attestation freshness

        Returns:
        {
            "attestation_document": "base64-encoded-cbor",
            "server_public_key": "base64-encoded-composite-key"
        }
        """
        try:
            import base64

            attestation_gen = request.app.state.attestation_generator

            encryption_manager = request.app.state.encryption_manager
            # Pass the SHA-256 fingerprint (not the full key) as public_key
            # for inclusion in the attestation document, because the composite
            # key exceeds the 1024-byte public_key field limit.
            fingerprint = (
                encryption_manager.server_public_key_fingerprint
                if encryption_manager is not None
                else None
            )

            attestation_doc, attestation_error = attestation_gen.generate_attestation(
                nonce=nonce,
                public_key=fingerprint,
            )

            if attestation_error:
                logger.error(
                    f"Attestation generation failed on /attest: {attestation_error.context}"
                )
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content=create_error_response(
                        "attestation_failed",
                        "Failed to generate attestation document"
                    )
                )

            # Build response with attestation doc and full composite key
            response_content = {
                "attestation_document": base64.b64encode(attestation_doc.signature).decode("utf-8"),
            }
            if encryption_manager is not None:
                response_content["server_public_key"] = base64.b64encode(
                    encryption_manager.server_public_key
                ).decode("utf-8")

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=response_content,
            )

        except Exception as e:
            logger.error(f"Unexpected error in /attest endpoint: {e}", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=create_error_response(
                    "attestation_failed",
                    "Failed to generate attestation document"
                )
            )

    @app.get("/health")
    async def health_check(request: Request):
        """
        Health check endpoint for monitoring

        Returns:
        {
            "status": "healthy"
        }
        """
        try:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "healthy"
                }
            )

        except Exception as e:
            logger.error(f"Error in health check endpoint: {e}", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "unhealthy"
                }
            )


