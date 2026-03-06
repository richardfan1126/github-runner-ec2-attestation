"""Configuration management for GitHub Actions Remote Executor"""
import os
from dataclasses import dataclass
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
    
    # AWS Nitro Configuration
    nsm_device_path: str
    
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
        
        nsm_path = os.getenv("NSM_DEVICE_PATH")
        if nsm_path is None:
            missing_vars.append("NSM_DEVICE_PATH")
        
        if missing_vars:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )
        
        return cls(
            port=int(port),
            max_concurrent_executions=int(max_concurrent),
            execution_timeout_seconds=int(timeout),
            max_script_size_bytes=int(max_size),
            rate_limit_per_ip=int(rate_limit),
            rate_limit_window_seconds=int(rate_window),
            temp_storage_path=temp_path,
            output_retention_hours=int(retention),
            nsm_device_path=nsm_path,
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
        
        if not self.nsm_device_path:
            errors.append("nsm_device_path cannot be empty")
        
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
