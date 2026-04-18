"""HTTP server for GitHub Actions Remote Executor"""
import logging
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, Tuple

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse

from src.config import ServerConfig
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.repository import RepositoryClient
from src.attestation import AttestationGenerator
from src.script_executor import ScriptExecutor
from src.validation import RequestValidator
from src.models import ExecutionStatus, CloneResult
from src.logging_config import set_log_context, clear_log_context, sanitize_for_logging, sanitize_error_message

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


def create_app(config: ServerConfig, docker_client=None, encryption_manager=None) -> FastAPI:
    """
    Create and configure FastAPI application
    
    Args:
        config: Server configuration
        docker_client: Optional pre-initialized Docker client. If None, creates one via docker.from_env().
        encryption_manager: Optional pre-initialized EncryptionManager instance.
    
    Returns:
        Configured FastAPI application
    """
    import docker as docker_lib

    app = FastAPI(
        title="GitHub Actions Remote Executor",
        description="Attestable script execution service for GitHub Actions",
        version="1.0.0"
    )
    
    # Initialize Docker client if not provided
    if docker_client is None:
        try:
            docker_client = docker_lib.from_env()
        except docker_lib.errors.DockerException:
            logger.warning("Docker daemon not available; ScriptExecutor will not function")
            docker_client = None
    
    # Initialize components
    execution_manager = ExecutionManager(config.output_retention_hours, encryption_manager=encryption_manager)
    output_collector = OutputCollector()
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
        # Skip rate limiting for health check and attest endpoint
        if request.url.path in ("/health", "/attest"):
            return await call_next(request)
        
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

            # Parse outer JSON envelope
            try:
                outer_body = await request.json()
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

            # Decrypt the request payload
            try:
                encrypted_payload = base64.b64decode(encrypted_payload_b64)
                client_public_key = base64.b64decode(client_public_key_b64)
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
                raise HTTPException(
                    status_code=oidc_result.status_code,
                    detail=create_error_response(
                        "oidc_authentication_failed",
                        oidc_result.error_message or "Authentication failed"
                    )
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
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=create_error_response(
                        "repository_mismatch",
                        "OIDC token repository claim does not match request repository_url"
                    )
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
                logger.warning(f"Validation failed: {validation_result.errors}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "validation_failed",
                        "Request validation failed",
                        {"errors": validation_result.errors}
                    )
                )
            
            phase_times['validation'] = (time.time() - validation_start) * 1000
            
            # Authenticate and fetch file
            auth_start = time.time()
            repo_client = request.app.state.repository_client
            
            auth_result = repo_client.authenticate(body['github_token'])
            if not auth_result.success:
                logger.warning(f"Authentication failed: {auth_result.error_message}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=create_error_response(
                        "authentication_failed",
                        auth_result.error_message or "GitHub authentication failed"
                    )
                )
            
            phase_times['authentication'] = (time.time() - auth_start) * 1000
            
            # Clone repository
            fetch_start = time.time()
            clone_result = None
            try:
                from src.repository import GitHubAPIError
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
                logger.warning(f"GitHub API error: {e.message}")
                if clone_result:
                    repo_client.cleanup_clone(clone_result.clone_path)
                raise HTTPException(
                    status_code=e.status_code,
                    detail=create_error_response(
                        "github_api_error",
                        e.message
                    )
                )
            
            phase_times['file_retrieval'] = (time.time() - fetch_start) * 1000
            
            # Get config for later use
            config = request.app.state.config
            
            # Generate attestation
            attestation_start = time.time()
            attestation_gen = request.app.state.attestation_generator
            
            attestation_doc, attestation_error = attestation_gen.generate_attestation(
                body['repository_url'],
                body['commit_hash'],
                body['script_path'],
                nonce=body.get('nonce'),
            )
            
            if attestation_error:
                logger.error(
                    f"Attestation generation failed: {attestation_error.context}"
                )
                repo_client.cleanup_clone(clone_result.clone_path)
                
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=create_error_response(
                        "attestation_failed",
                        "Failed to generate attestation document"
                    )
                )
            
            phase_times['attestation'] = (time.time() - attestation_start) * 1000
            
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
                repo_client.cleanup_clone(clone_result.clone_path)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=create_error_response(
                        "at_capacity",
                        "Server is at maximum execution capacity. Please try again later."
                    )
                )
            
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
            executor.execute_async(execution_record.execution_id, clone_result.clone_path, clone_result.script_path)
            
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

        Request body (encrypted with Shared_Key):
        {
            "encrypted_payload": "base64-encoded-ciphertext"
        }

        Decrypted request payload:
        {
            "oidc_token": "eyJhbGciOiJSUzI1NiIs...",
            "nonce": "optional-client-nonce",
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

            # Parse outer JSON envelope
            try:
                outer_body = await request.json()
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

            # Decrypt the request payload using Shared_Key
            try:
                encrypted_payload = base64.b64decode(encrypted_payload_b64)
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

            # OIDC authentication from decrypted body
            validator = request.app.state.request_validator
            oidc_token = body.get("oidc_token")
            oidc_result = validator.validate_oidc_token_from_body(oidc_token)

            if not oidc_result.valid:
                repo_claim = (oidc_result.claims or {}).get("repository", "unknown")
                logger.warning(
                    f"OIDC validation failed on output endpoint: status={oidc_result.status_code}, "
                    f"repository={repo_claim}, error={oidc_result.error_message}"
                )
                raise HTTPException(
                    status_code=oidc_result.status_code,
                    detail=create_error_response(
                        "oidc_authentication_failed",
                        oidc_result.error_message or "Authentication failed"
                    )
                )

            # Extract optional offset and nonce from decrypted body
            offset = body.get("offset", 0)
            nonce = body.get("nonce")

            # Validate offset
            if not isinstance(offset, int) or offset < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "invalid_offset",
                        "Offset must be a non-negative integer",
                        {"offset": offset}
                    )
                )
            
            # Retrieve execution record
            exec_manager = request.app.state.execution_manager
            execution_record = exec_manager.get_execution(execution_id)
            
            if not execution_record:
                logger.warning(f"Execution not found: {execution_id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=create_error_response(
                        "execution_not_found",
                        f"Execution ID not found: {execution_id}"
                    )
                )

            # Repository binding: verify OIDC repository claim matches execution record
            oidc_repo_claim = oidc_result.claims.get("repository", "")
            if oidc_repo_claim != execution_record.repository:
                logger.warning(
                    f"Output repository mismatch: OIDC claim={oidc_repo_claim}, "
                    f"execution record={execution_record.repository}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=create_error_response(
                        "repository_mismatch",
                        "OIDC token repository claim does not match execution record"
                    )
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
                attestation_gen.generate_output_attestation(script_output, nonce=nonce)
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
            "status": "healthy",
            "attestation_available": true,
            "docker_available": true,
            "disk_space_mb": 10240,
            "active_executions": 3
        }
        """
        try:
            import shutil

            # Check attestation capability
            attestation_gen = request.app.state.attestation_generator
            attestation_available = attestation_gen.verify_tpm_available()

            # Check Docker daemon availability
            script_exec = request.app.state.script_executor
            docker_available = script_exec.verify_docker_daemon()

            # Check disk space
            config = request.app.state.config
            os.makedirs(config.temp_storage_path, exist_ok=True)
            disk_usage = shutil.disk_usage(config.temp_storage_path)
            disk_space_mb = disk_usage.free // (1024 * 1024)

            # Get active executions count
            exec_manager = request.app.state.execution_manager
            active_executions = exec_manager.get_active_count()

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "healthy",
                    "attestation_available": attestation_available,
                    "docker_available": docker_available,
                    "disk_space_mb": disk_space_mb,
                    "active_executions": active_executions
                }
            )

        except Exception as e:
            logger.error(f"Error in health check endpoint: {e}", exc_info=True)
            # Still return 200 but indicate degraded status
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "degraded",
                    "attestation_available": False,
                    "docker_available": False,
                    "disk_space_mb": 0,
                    "active_executions": 0
                }
            )

    @app.get("/metrics")
    async def metrics(request: Request):
        """
        Metrics endpoint for monitoring

        Returns:
        {
            "total_executions": 1523,
            "successful_executions": 1450,
            "failed_executions": 73,
            "average_duration_ms": 3421,
            "active_executions": 3
        }
        """
        try:
            exec_manager = request.app.state.execution_manager
            metrics_data = exec_manager.get_metrics()

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=metrics_data
            )

        except Exception as e:
            logger.error(f"Error in metrics endpoint: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=create_error_response(
                    "internal_server_error",
                    "An unexpected error occurred"
                )
            )

