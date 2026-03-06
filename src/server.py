"""HTTP server for GitHub Actions Remote Executor"""
import logging
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
from src.models import ExecutionStatus
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


def create_app(config: ServerConfig) -> FastAPI:
    """
    Create and configure FastAPI application
    
    Args:
        config: Server configuration
    
    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="GitHub Actions Remote Executor",
        description="Attestable script execution service for GitHub Actions",
        version="1.0.0"
    )
    
    # Initialize components
    execution_manager = ExecutionManager(config.output_retention_hours)
    output_collector = OutputCollector()
    repository_client = RepositoryClient(config.temp_storage_path)
    attestation_generator = AttestationGenerator(config.nsm_device_path)
    script_executor = ScriptExecutor(
        execution_manager,
        output_collector,
        config.temp_storage_path
    )
    request_validator = RequestValidator()
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
        # Skip rate limiting for health check
        if request.url.path == "/health":
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
        
        Request body:
        {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "abc123...",
            "script_path": "scripts/build.sh",
            "github_token": "ghp_..."
        }
        
        Returns:
        {
            "execution_id": "uuid",
            "attestation_document": "base64-encoded-cbor",
            "status": "queued"
        }
        """
        start_time = time.time()
        phase_times = {}
        
        try:
            # Parse request body
            try:
                body = await request.json()
            except Exception as e:
                logger.warning(f"Malformed request body: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "malformed_request",
                        "Request body must be valid JSON"
                    )
                )
            
            # Log request details (exclude token)
            sanitized_body = sanitize_for_logging(body)
            logger.info(
                f"Execution request: repo={body.get('repository_url')}, "
                f"commit={body.get('commit_hash')}, path={body.get('script_path')}"
            )
            
            # Validate request
            validation_start = time.time()
            validator = request.app.state.request_validator
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
            
            # Fetch file
            fetch_start = time.time()
            try:
                from src.repository import GitHubAPIError
                file_content = repo_client.fetch_file(
                    body['repository_url'],
                    body['commit_hash'],
                    body['script_path']
                )
            except GitHubAPIError as e:
                logger.warning(f"GitHub API error: {e.message}")
                raise HTTPException(
                    status_code=e.status_code,
                    detail=create_error_response(
                        "github_api_error",
                        e.message
                    )
                )
            
            phase_times['file_retrieval'] = (time.time() - fetch_start) * 1000
            
            # Validate script file size
            config = request.app.state.config
            if file_content.size_bytes > config.max_script_size_bytes:
                logger.warning(
                    f"Script file too large: {file_content.size_bytes} bytes "
                    f"(max: {config.max_script_size_bytes})"
                )
                # Clean up temp file
                repo_client.cleanup_temp_file(file_content.temp_path)
                
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=create_error_response(
                        "file_too_large",
                        f"Script file exceeds maximum size of {config.max_script_size_bytes} bytes",
                        {
                            "file_size": file_content.size_bytes,
                            "max_size": config.max_script_size_bytes
                        }
                    )
                )
            
            # Generate attestation
            attestation_start = time.time()
            attestation_gen = request.app.state.attestation_generator
            
            attestation_doc, attestation_error = attestation_gen.generate_attestation(
                body['repository_url'],
                body['commit_hash'],
                body['script_path']
            )
            
            if attestation_error:
                logger.error(
                    f"Attestation generation failed: {attestation_error.context}"
                )
                # Clean up temp file
                repo_client.cleanup_temp_file(file_content.temp_path)
                
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=create_error_response(
                        "attestation_failed",
                        "Failed to generate attestation document"
                    )
                )
            
            phase_times['attestation'] = (time.time() - attestation_start) * 1000
            
            # Create execution record
            exec_manager = request.app.state.execution_manager
            execution_record = exec_manager.create_execution(
                body['repository_url'],
                body['commit_hash'],
                body['script_path'],
                config.execution_timeout_seconds
            )
            
            # Set log context with execution ID
            set_log_context(execution_id=execution_record.execution_id)
            
            logger.info(f"Created execution record: {execution_record.execution_id}")
            logger.info(f"Attestation generated for execution: {execution_record.execution_id}")
            
            # Prepare response
            import base64
            response_data = {
                "execution_id": execution_record.execution_id,
                "attestation_document": base64.b64encode(attestation_doc.signature).decode('utf-8'),
                "status": execution_record.status.value
            }
            
            # Initiate async execution
            executor = request.app.state.script_executor
            executor.execute_async(execution_record.execution_id, file_content.temp_path)
            
            logger.info(f"Initiated async execution: {execution_record.execution_id}")
            
            # Log phase durations
            total_time = (time.time() - start_time) * 1000
            logger.info(
                f"Request processing phases for {execution_record.execution_id}: "
                f"validation={phase_times.get('validation', 0):.2f}ms, "
                f"auth={phase_times.get('authentication', 0):.2f}ms, "
                f"fetch={phase_times.get('file_retrieval', 0):.2f}ms, "
                f"attestation={phase_times.get('attestation', 0):.2f}ms, "
                f"total={total_time:.2f}ms"
            )
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=response_data
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

    
    @app.get("/execution/{execution_id}/output")
    async def get_execution_output(execution_id: str, request: Request, offset: int = 0):
        """
        Get execution status and output
        
        Query parameters:
        - offset: Byte offset to start retrieving output from (default: 0)
        
        Returns:
        {
            "execution_id": "uuid",
            "status": "running|completed|failed|timed_out",
            "stdout": "output text...",
            "stderr": "error text...",
            "stdout_offset": 1024,
            "stderr_offset": 256,
            "complete": false,
            "exit_code": null
        }
        """
        try:
            # Validate offset
            if offset < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=create_error_response(
                        "invalid_offset",
                        "Offset must be non-negative",
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
                response_data = {
                    "execution_id": execution_id,
                    "status": execution_record.status.value,
                    "stdout": output_data.stdout,
                    "stderr": output_data.stderr,
                    "stdout_offset": output_data.stdout_offset,
                    "stderr_offset": output_data.stderr_offset,
                    "complete": output_data.complete,
                    "exit_code": output_data.exit_code
                }
            else:
                # No output yet - return empty
                response_data = {
                    "execution_id": execution_id,
                    "status": execution_record.status.value,
                    "stdout": "",
                    "stderr": "",
                    "stdout_offset": 0,
                    "stderr_offset": 0,
                    "complete": False,
                    "exit_code": None
                }
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=response_data
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

    @app.get("/health")
    async def health_check(request: Request):
        """
        Health check endpoint for monitoring

        Returns:
        {
            "status": "healthy",
            "attestation_available": true,
            "disk_space_mb": 10240,
            "active_executions": 3
        }
        """
        try:
            import shutil

            # Check attestation capability
            attestation_gen = request.app.state.attestation_generator
            attestation_available = attestation_gen.verify_nsm_available()

            # Check disk space
            config = request.app.state.config
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

