"""Property-based tests for configuration management

Feature: github-actions-remote-executor
Tests Properties 50-57 for configuration management
"""
import os
import pytest
from contextlib import contextmanager
from hypothesis import given, strategies as st, settings, HealthCheck
from src.config import ServerConfig


@contextmanager
def env_vars(**kwargs):
    """Context manager to temporarily set environment variables"""
    old_values = {}
    for key, value in kwargs.items():
        old_values[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)
    
    try:
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


# Strategy for valid port numbers
valid_ports = st.integers(min_value=1, max_value=65535)

# Strategy for positive integers
positive_ints = st.integers(min_value=1, max_value=1_000_000)

# Strategy for valid file paths
valid_paths = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="/-_."
    ),
    min_size=1,
    max_size=100
).filter(lambda x: x.strip() and not x.startswith("-"))


# Feature: github-actions-remote-executor, Property 50: Configuration Loading
@given(
    port=valid_ports,
    max_concurrent=positive_ints,
    timeout=positive_ints,
    max_size=positive_ints,
    rate_limit=positive_ints,
    rate_window=positive_ints,
    temp_path=valid_paths,
    retention=positive_ints,
    nsm_path=valid_paths,
)
@settings(max_examples=20)
def test_property_50_configuration_loading(
    port,
    max_concurrent,
    timeout,
    max_size,
    rate_limit,
    rate_window,
    temp_path,
    retention,
    nsm_path,
):
    """
    Property 50: Configuration Loading
    
    For any server startup, configuration should be loaded from environment 
    variables or a configuration file.
    
    Validates: Requirements 9.1
    """
    # Set all required environment variables
    with env_vars(
        SERVER_PORT=port,
        MAX_CONCURRENT_EXECUTIONS=max_concurrent,
        EXECUTION_TIMEOUT_SECONDS=timeout,
        MAX_SCRIPT_SIZE_BYTES=max_size,
        RATE_LIMIT_PER_IP=rate_limit,
        RATE_LIMIT_WINDOW_SECONDS=rate_window,
        TEMP_STORAGE_PATH=temp_path,
        OUTPUT_RETENTION_HOURS=retention,
        NSM_DEVICE_PATH=nsm_path,
    ):
        # Configuration should load successfully
        config = ServerConfig.from_env()
        
        # Verify all values are loaded correctly
        assert config.port == port
        assert config.max_concurrent_executions == max_concurrent
        assert config.execution_timeout_seconds == timeout
        assert config.max_script_size_bytes == max_size
        assert config.rate_limit_per_ip == rate_limit
        assert config.rate_limit_window_seconds == rate_window
        assert config.temp_storage_path == temp_path
        assert config.output_retention_hours == retention
        assert config.nsm_device_path == nsm_path


# Feature: github-actions-remote-executor, Property 51: Port Configuration
@given(port=valid_ports)
@settings(max_examples=20)
def test_property_51_port_configuration(port):
    """
    Property 51: Port Configuration
    
    For any configured HTTP port value, the server should listen on that port.
    
    Validates: Requirements 9.2
    """
    # Set up minimal valid configuration with the test port
    with env_vars(
        SERVER_PORT=port,
        MAX_CONCURRENT_EXECUTIONS=10,
        EXECUTION_TIMEOUT_SECONDS=300,
        MAX_SCRIPT_SIZE_BYTES=1048576,
        RATE_LIMIT_PER_IP=100,
        RATE_LIMIT_WINDOW_SECONDS=60,
        TEMP_STORAGE_PATH="/tmp/test",
        OUTPUT_RETENTION_HOURS=24,
        NSM_DEVICE_PATH="/dev/nsm",
    ):
        config = ServerConfig.from_env()
        
        # The configured port should be loaded
        assert config.port == port
        
        # Valid ports should pass validation
        config.validate()


# Feature: github-actions-remote-executor, Property 52: Timeout Configuration
@given(timeout=positive_ints)
@settings(max_examples=20)
def test_property_52_timeout_configuration(timeout):
    """
    Property 52: Timeout Configuration
    
    For any configured execution timeout value, that timeout should be applied 
    to script executions.
    
    Validates: Requirements 9.3
    """
    # Set up minimal valid configuration with the test timeout
    with env_vars(
        SERVER_PORT=8080,
        MAX_CONCURRENT_EXECUTIONS=10,
        EXECUTION_TIMEOUT_SECONDS=timeout,
        MAX_SCRIPT_SIZE_BYTES=1048576,
        RATE_LIMIT_PER_IP=100,
        RATE_LIMIT_WINDOW_SECONDS=60,
        TEMP_STORAGE_PATH="/tmp/test",
        OUTPUT_RETENTION_HOURS=24,
        NSM_DEVICE_PATH="/dev/nsm",
    ):
        config = ServerConfig.from_env()
        
        # The configured timeout should be loaded
        assert config.execution_timeout_seconds == timeout
        
        # Valid timeouts should pass validation
        config.validate()


# Feature: github-actions-remote-executor, Property 53: Size Limit Configuration
@given(max_size=positive_ints)
@settings(max_examples=20)
def test_property_53_size_limit_configuration(max_size):
    """
    Property 53: Size Limit Configuration
    
    For any configured maximum script file size, that limit should be enforced 
    during validation.
    
    Validates: Requirements 9.4
    """
    # Set up minimal valid configuration with the test size limit
    with env_vars(
        SERVER_PORT=8080,
        MAX_CONCURRENT_EXECUTIONS=10,
        EXECUTION_TIMEOUT_SECONDS=300,
        MAX_SCRIPT_SIZE_BYTES=max_size,
        RATE_LIMIT_PER_IP=100,
        RATE_LIMIT_WINDOW_SECONDS=60,
        TEMP_STORAGE_PATH="/tmp/test",
        OUTPUT_RETENTION_HOURS=24,
        NSM_DEVICE_PATH="/dev/nsm",
    ):
        config = ServerConfig.from_env()
        
        # The configured size limit should be loaded
        assert config.max_script_size_bytes == max_size
        
        # Valid size limits should pass validation
        config.validate()


# Feature: github-actions-remote-executor, Property 54: Rate Limit Configuration
@given(
    rate_limit=positive_ints,
    rate_window=positive_ints,
)
@settings(max_examples=20)
def test_property_54_rate_limit_configuration(rate_limit, rate_window):
    """
    Property 54: Rate Limit Configuration
    
    For any configured rate limiting parameters, those limits should be enforced 
    for incoming requests.
    
    Validates: Requirements 9.5
    """
    # Set up minimal valid configuration with the test rate limits
    with env_vars(
        SERVER_PORT=8080,
        MAX_CONCURRENT_EXECUTIONS=10,
        EXECUTION_TIMEOUT_SECONDS=300,
        MAX_SCRIPT_SIZE_BYTES=1048576,
        RATE_LIMIT_PER_IP=rate_limit,
        RATE_LIMIT_WINDOW_SECONDS=rate_window,
        TEMP_STORAGE_PATH="/tmp/test",
        OUTPUT_RETENTION_HOURS=24,
        NSM_DEVICE_PATH="/dev/nsm",
    ):
        config = ServerConfig.from_env()
        
        # The configured rate limits should be loaded
        assert config.rate_limit_per_ip == rate_limit
        assert config.rate_limit_window_seconds == rate_window
        
        # Valid rate limits should pass validation
        config.validate()


# Feature: github-actions-remote-executor, Property 55: Storage Path Configuration
@given(storage_path=valid_paths)
@settings(max_examples=20)
def test_property_55_storage_path_configuration(storage_path):
    """
    Property 55: Storage Path Configuration
    
    For any configured temporary file storage location, temporary files should 
    be stored in that location.
    
    Validates: Requirements 9.6
    """
    # Set up minimal valid configuration with the test storage path
    with env_vars(
        SERVER_PORT=8080,
        MAX_CONCURRENT_EXECUTIONS=10,
        EXECUTION_TIMEOUT_SECONDS=300,
        MAX_SCRIPT_SIZE_BYTES=1048576,
        RATE_LIMIT_PER_IP=100,
        RATE_LIMIT_WINDOW_SECONDS=60,
        TEMP_STORAGE_PATH=storage_path,
        OUTPUT_RETENTION_HOURS=24,
        NSM_DEVICE_PATH="/dev/nsm",
    ):
        config = ServerConfig.from_env()
        
        # The configured storage path should be loaded
        assert config.temp_storage_path == storage_path
        
        # Valid storage paths should pass validation
        config.validate()


# Feature: github-actions-remote-executor, Property 56: Retention Period Configuration
@given(retention_hours=positive_ints)
@settings(max_examples=20)
def test_property_56_retention_period_configuration(retention_hours):
    """
    Property 56: Retention Period Configuration
    
    For any configured output retention period, execution output should be 
    retained for that duration.
    
    Validates: Requirements 9.7
    """
    # Set up minimal valid configuration with the test retention period
    with env_vars(
        SERVER_PORT=8080,
        MAX_CONCURRENT_EXECUTIONS=10,
        EXECUTION_TIMEOUT_SECONDS=300,
        MAX_SCRIPT_SIZE_BYTES=1048576,
        RATE_LIMIT_PER_IP=100,
        RATE_LIMIT_WINDOW_SECONDS=60,
        TEMP_STORAGE_PATH="/tmp/test",
        OUTPUT_RETENTION_HOURS=retention_hours,
        NSM_DEVICE_PATH="/dev/nsm",
    ):
        config = ServerConfig.from_env()
        
        # The configured retention period should be loaded
        assert config.output_retention_hours == retention_hours
        
        # Valid retention periods should pass validation
        config.validate()


# Feature: github-actions-remote-executor, Property 57: Missing Configuration Failure
@given(
    missing_var=st.sampled_from([
        "SERVER_PORT",
        "MAX_CONCURRENT_EXECUTIONS",
        "EXECUTION_TIMEOUT_SECONDS",
        "MAX_SCRIPT_SIZE_BYTES",
        "RATE_LIMIT_PER_IP",
        "RATE_LIMIT_WINDOW_SECONDS",
        "TEMP_STORAGE_PATH",
        "OUTPUT_RETENTION_HOURS",
        "NSM_DEVICE_PATH",
    ])
)
@settings(max_examples=20)
def test_property_57_missing_configuration_failure(missing_var):
    """
    Property 57: Missing Configuration Failure
    
    For any required configuration parameter that is missing, the server should 
    fail to start with a descriptive error message.
    
    Validates: Requirements 9.8
    """
    # Set up all environment variables
    env_config = {
        "SERVER_PORT": "8080",
        "MAX_CONCURRENT_EXECUTIONS": "10",
        "EXECUTION_TIMEOUT_SECONDS": "300",
        "MAX_SCRIPT_SIZE_BYTES": "1048576",
        "RATE_LIMIT_PER_IP": "100",
        "RATE_LIMIT_WINDOW_SECONDS": "60",
        "TEMP_STORAGE_PATH": "/tmp/test",
        "OUTPUT_RETENTION_HOURS": "24",
        "NSM_DEVICE_PATH": "/dev/nsm",
    }
    
    # Remove the selected variable
    del env_config[missing_var]
    
    # Set remaining variables and ensure missing one is not set
    env_config[missing_var] = None
    
    with env_vars(**env_config):
        # Configuration loading should fail with descriptive error
        with pytest.raises(ValueError) as exc_info:
            ServerConfig.from_env()
        
        # Error message should mention missing variables
        error_msg = str(exc_info.value)
        assert "Missing required environment variables" in error_msg
        assert missing_var in error_msg


# Additional property tests for validation edge cases
@given(port=st.integers(max_value=0) | st.integers(min_value=65536))
@settings(max_examples=20)
def test_property_invalid_port_validation(port):
    """
    Property: Invalid port values should fail validation
    
    For any port value outside the valid range (1-65535), validation should fail.
    """
    config = ServerConfig(
        port=port,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=100,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/test",
        output_retention_hours=24,
        nsm_device_path="/dev/nsm",
    )
    
    with pytest.raises(ValueError) as exc_info:
        config.validate()
    
    assert "Invalid port" in str(exc_info.value)


@given(value=st.integers(max_value=0))
@settings(max_examples=20)
def test_property_invalid_positive_int_validation(value):
    """
    Property: Non-positive integer configuration values should fail validation
    
    For any configuration parameter that requires a positive integer, 
    non-positive values should fail validation.
    """
    # Test with max_concurrent_executions
    config = ServerConfig(
        port=8080,
        max_concurrent_executions=value,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=100,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/test",
        output_retention_hours=24,
        nsm_device_path="/dev/nsm",
    )
    
    with pytest.raises(ValueError) as exc_info:
        config.validate()
    
    error_msg = str(exc_info.value)
    assert "Invalid" in error_msg or "must be" in error_msg


@given(empty_path=st.just("") | st.text(max_size=0))
@settings(max_examples=20)
def test_property_empty_path_validation(empty_path):
    """
    Property: Empty path configuration values should fail validation
    
    For any configuration parameter that requires a path, empty values should 
    fail validation.
    """
    # Test with temp_storage_path
    config = ServerConfig(
        port=8080,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=100,
        rate_limit_window_seconds=60,
        temp_storage_path=empty_path,
        output_retention_hours=24,
        nsm_device_path="/dev/nsm",
    )
    
    with pytest.raises(ValueError) as exc_info:
        config.validate()
    
    assert "temp_storage_path cannot be empty" in str(exc_info.value)
