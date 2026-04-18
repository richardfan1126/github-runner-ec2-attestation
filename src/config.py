"""Configuration management for GitHub Actions Remote Executor"""
import os
from dataclasses import dataclass, field
from typing import Optional


class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing"""
    pass


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
        allowed_branches = None
        if allowed_branches_raw is not None:
            allowed_branches = [b.strip() for b in allowed_branches_raw.split(",") if b.strip()]
            if not allowed_branches:
                allowed_branches = None
        
        # Parse optional REQUIRE_PROTECTED_REF (boolean, default False)
        require_protected_ref_raw = os.getenv("REQUIRE_PROTECTED_REF", "false")
        require_protected_ref = require_protected_ref_raw.lower() in ("true", "1", "yes")
        
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
            allowed_branches=allowed_branches,
            require_protected_ref=require_protected_ref,
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
