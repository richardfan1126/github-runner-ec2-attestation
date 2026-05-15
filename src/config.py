"""Configuration management for GitHub Actions Remote Executor"""
import os
from dataclasses import dataclass, field
from typing import Optional


class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing"""
    pass


def parse_strict_bool(value: str, key_name: str) -> bool:
    """
    Parse a boolean value from a string with strict validation.
    
    Only accepts: true, 1, yes (case-insensitive) → True
                  false, 0, no (case-insensitive) → False
    
    Args:
        value: The string value to parse
        key_name: The configuration key name (for error messages)
    
    Returns:
        The parsed boolean value
    
    Raises:
        ValueError: If the value is not in the recognized set
    """
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    raise ValueError(
        f"Invalid boolean value for {key_name}: '{value}'. "
        f"Accepted values: true, 1, yes, false, 0, no"
    )


@dataclass
class ServerConfig:
    """Server configuration loaded from environment variables"""
    
    # HTTP Server Configuration
    port: int
    
    # Execution Configuration
    max_concurrent_executions: int
    execution_timeout_seconds: int
    max_script_size_bytes: int
    
    # Rate Limiting Configuration
    rate_limit_per_ip: int
    rate_limit_window_seconds: int
    
    # Storage Configuration
    temp_storage_path: str
    output_retention_hours: int
    
    # NitroTPM Configuration
    tpm_attest_path: str
    
    # OIDC Authentication Configuration
    allowed_repositories: list[str]
    expected_audience: str
    
    # Container Execution Configuration
    container_image: str
    container_memory_limit: str
    container_cpu_limit: float
    
    # Output Buffer Size Configuration
    max_output_size_bytes: int = 10_485_760  # 10MB default
    
    # Anti-replay nonce cache TTL (matches OIDC token lifetime)
    nonce_cache_ttl_seconds: int = 300

    # Container Image Digest Pinning
    container_image_digest: Optional[str] = None

    # Request Body Size Limits
    max_request_body_bytes: int = 1_048_576  # 1MB default
    max_encrypted_payload_bytes: int = 524_288  # 512KB default
    max_decrypted_payload_bytes: int = 262_144  # 256KB default

    # Script environment variable deny-list
    # Keys matching this list (exact or prefix for '*' entries) are rejected
    script_env_deny_list: list[str] = field(default_factory=lambda: [
        "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "BASH_FUNC_*",
        "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PROMPT_COMMAND",
        "PS1", "PS2", "PS4", "IFS", "CDPATH", "GLOBIGNORE", "BASH_XTRACEFD",
        "BASH_LOADABLES_PATH",
    ])

    # Optional OIDC Branch/Ref Restrictions
    allowed_branches: Optional[list[str]] = None
    require_protected_ref: bool = False
    
    @classmethod
    def from_env(cls) -> "ServerConfig":
        """Load configuration from environment variables"""
        missing_vars = []
        
        # Required environment variables
        port = os.getenv("SERVER_PORT")
        if port is None:
            missing_vars.append("SERVER_PORT")
        
        max_concurrent = os.getenv("MAX_CONCURRENT_EXECUTIONS")
        if max_concurrent is None:
            missing_vars.append("MAX_CONCURRENT_EXECUTIONS")
        
        timeout = os.getenv("EXECUTION_TIMEOUT_SECONDS")
        if timeout is None:
            missing_vars.append("EXECUTION_TIMEOUT_SECONDS")
        
        max_size = os.getenv("MAX_SCRIPT_SIZE_BYTES")
        if max_size is None:
            missing_vars.append("MAX_SCRIPT_SIZE_BYTES")
        
        rate_limit = os.getenv("RATE_LIMIT_PER_IP")
        if rate_limit is None:
            missing_vars.append("RATE_LIMIT_PER_IP")
        
        rate_window = os.getenv("RATE_LIMIT_WINDOW_SECONDS")
        if rate_window is None:
            missing_vars.append("RATE_LIMIT_WINDOW_SECONDS")
        
        temp_path = os.getenv("TEMP_STORAGE_PATH")
        if temp_path is None:
            missing_vars.append("TEMP_STORAGE_PATH")
        
        retention = os.getenv("OUTPUT_RETENTION_HOURS")
        if retention is None:
            missing_vars.append("OUTPUT_RETENTION_HOURS")
        
        tpm_path = os.getenv("TPM_ATTEST_PATH")
        if tpm_path is None:
            missing_vars.append("TPM_ATTEST_PATH")
        
        allowed_repos = os.getenv("ALLOWED_REPOSITORIES")
        if allowed_repos is None:
            missing_vars.append("ALLOWED_REPOSITORIES")
        
        expected_aud = os.getenv("EXPECTED_AUDIENCE")
        if expected_aud is None:
            missing_vars.append("EXPECTED_AUDIENCE")
        
        container_image = os.getenv("CONTAINER_IMAGE")
        if container_image is None:
            missing_vars.append("CONTAINER_IMAGE")
        
        container_memory_limit = os.getenv("CONTAINER_MEMORY_LIMIT")
        if container_memory_limit is None:
            missing_vars.append("CONTAINER_MEMORY_LIMIT")
        
        container_cpu_limit = os.getenv("CONTAINER_CPU_LIMIT")
        if container_cpu_limit is None:
            missing_vars.append("CONTAINER_CPU_LIMIT")
        
        if missing_vars:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )
        
        # Parse optional ALLOWED_BRANCHES (comma-separated, None if not set)
        allowed_branches_raw = os.getenv("ALLOWED_BRANCHES")

        # Parse optional CONTAINER_IMAGE_DIGEST (SHA-256 digest, None if not set)
        container_image_digest = os.getenv("CONTAINER_IMAGE_DIGEST") or None
        allowed_branches = None
        if allowed_branches_raw is not None:
            allowed_branches = [b.strip() for b in allowed_branches_raw.split(",") if b.strip()]
            if not allowed_branches:
                allowed_branches = None
        
        # Parse optional REQUIRE_PROTECTED_REF (boolean, default False)
        require_protected_ref_raw = os.getenv("REQUIRE_PROTECTED_REF", "false")
        require_protected_ref = parse_strict_bool(require_protected_ref_raw, "REQUIRE_PROTECTED_REF")
        
        # Parse optional MAX_OUTPUT_SIZE_BYTES (default 10MB)
        max_output_size_bytes_raw = os.getenv("MAX_OUTPUT_SIZE_BYTES")
        max_output_size_bytes = 10_485_760  # 10MB default
        if max_output_size_bytes_raw is not None:
            max_output_size_bytes = int(max_output_size_bytes_raw)
        
        # Parse optional NONCE_CACHE_TTL_SECONDS (default 300s = 5 min)
        nonce_cache_ttl_seconds_raw = os.getenv("NONCE_CACHE_TTL_SECONDS")
        nonce_cache_ttl_seconds = 300
        if nonce_cache_ttl_seconds_raw is not None:
            nonce_cache_ttl_seconds = int(nonce_cache_ttl_seconds_raw)
        
        # Parse optional request body size limits
        max_request_body_bytes_raw = os.getenv("MAX_REQUEST_BODY_BYTES")
        max_request_body_bytes = 1_048_576  # 1MB default
        if max_request_body_bytes_raw is not None:
            max_request_body_bytes = int(max_request_body_bytes_raw)
        
        max_encrypted_payload_bytes_raw = os.getenv("MAX_ENCRYPTED_PAYLOAD_BYTES")
        max_encrypted_payload_bytes = 524_288  # 512KB default
        if max_encrypted_payload_bytes_raw is not None:
            max_encrypted_payload_bytes = int(max_encrypted_payload_bytes_raw)
        
        max_decrypted_payload_bytes_raw = os.getenv("MAX_DECRYPTED_PAYLOAD_BYTES")
        max_decrypted_payload_bytes = 262_144  # 256KB default
        if max_decrypted_payload_bytes_raw is not None:
            max_decrypted_payload_bytes = int(max_decrypted_payload_bytes_raw)
        
        # Parse optional SCRIPT_ENV_DENY_LIST (comma-separated, uses default if not set)
        script_env_deny_list_raw = os.getenv("SCRIPT_ENV_DENY_LIST")
        script_env_deny_list = None  # Will use dataclass default
        if script_env_deny_list_raw is not None:
            script_env_deny_list = [e.strip() for e in script_env_deny_list_raw.split(",") if e.strip()]
        
        return cls(
            port=int(port),
            max_concurrent_executions=int(max_concurrent),
            execution_timeout_seconds=int(timeout),
            max_script_size_bytes=int(max_size),
            rate_limit_per_ip=int(rate_limit),
            rate_limit_window_seconds=int(rate_window),
            temp_storage_path=temp_path,
            output_retention_hours=int(retention),
            tpm_attest_path=tpm_path,
            allowed_repositories=[r.strip() for r in allowed_repos.split(",") if r.strip()],
            expected_audience=expected_aud,
            container_image=container_image,
            container_memory_limit=container_memory_limit,
            container_cpu_limit=float(container_cpu_limit),
            container_image_digest=container_image_digest,
            allowed_branches=allowed_branches,
            require_protected_ref=require_protected_ref,
            max_output_size_bytes=max_output_size_bytes,
            nonce_cache_ttl_seconds=nonce_cache_ttl_seconds,
            max_request_body_bytes=max_request_body_bytes,
            max_encrypted_payload_bytes=max_encrypted_payload_bytes,
            max_decrypted_payload_bytes=max_decrypted_payload_bytes,
            **({"script_env_deny_list": script_env_deny_list} if script_env_deny_list is not None else {}),
        )
    
    def validate(self) -> None:
        """Validate configuration values"""
        errors = []
        
        if self.port < 1 or self.port > 65535:
            errors.append(f"Invalid port: {self.port} (must be 1-65535)")
        
        if self.max_concurrent_executions < 1:
            errors.append(
                f"Invalid max_concurrent_executions: {self.max_concurrent_executions} (must be >= 1)"
            )
        
        if self.execution_timeout_seconds < 1:
            errors.append(
                f"Invalid execution_timeout_seconds: {self.execution_timeout_seconds} (must be >= 1)"
            )
        
        if self.max_script_size_bytes < 1:
            errors.append(
                f"Invalid max_script_size_bytes: {self.max_script_size_bytes} (must be >= 1)"
            )
        
        if self.rate_limit_per_ip < 1:
            errors.append(
                f"Invalid rate_limit_per_ip: {self.rate_limit_per_ip} (must be >= 1)"
            )
        
        if self.rate_limit_window_seconds < 1:
            errors.append(
                f"Invalid rate_limit_window_seconds: {self.rate_limit_window_seconds} (must be >= 1)"
            )
        
        if not self.temp_storage_path:
            errors.append("temp_storage_path cannot be empty")
        
        if self.output_retention_hours < 1:
            errors.append(
                f"Invalid output_retention_hours: {self.output_retention_hours} (must be >= 1)"
            )
        
        if not self.tpm_attest_path:
            errors.append("tpm_attest_path cannot be empty")
        
        if not self.allowed_repositories:
            errors.append("allowed_repositories must be a non-empty list")
        
        if not self.expected_audience:
            errors.append("expected_audience cannot be empty")
        
        if not self.container_image:
            errors.append("container_image cannot be empty")
        
        if not self.container_memory_limit:
            errors.append("container_memory_limit cannot be empty")
        
        if self.container_cpu_limit <= 0:
            errors.append(
                f"Invalid container_cpu_limit: {self.container_cpu_limit} (must be > 0)"
            )
        
        if self.max_output_size_bytes < 1:
            errors.append(
                f"Invalid max_output_size_bytes: {self.max_output_size_bytes} (must be >= 1)"
            )
        
        if self.nonce_cache_ttl_seconds < 1:
            errors.append(
                f"Invalid nonce_cache_ttl_seconds: {self.nonce_cache_ttl_seconds} (must be >= 1)"
            )
        
        # Container image digest validation (Requirements 34.7, 34.8)
        # If digest is not explicitly set, try to extract from image reference
        if self.container_image_digest is None and self.container_image and "@sha256:" in self.container_image:
            # Extract digest from image reference (e.g., "ubuntu:24.04@sha256:abc123..." -> "sha256:abc123...")
            digest_part = self.container_image.split("@sha256:", 1)[1]
            self.container_image_digest = f"sha256:{digest_part}"
        
        # After potential extraction, digest must be set
        if self.container_image_digest is None:
            errors.append(
                "container_image_digest is required: set CONTAINER_IMAGE_DIGEST or use a "
                "digest-pinned image reference (e.g., image@sha256:...)"
            )
        
        if errors:
            raise ValueError(f"Configuration validation failed: {'; '.join(errors)}")


def load_config() -> ServerConfig:
    """
    Load and validate server configuration from environment variables.
    
    Returns:
        Validated ServerConfig instance
    
    Raises:
        ConfigurationError: If required configuration is missing or invalid
    """
    try:
        config = ServerConfig.from_env()
        config.validate()
        return config
    except ValueError as e:
        raise ConfigurationError(str(e)) from e
