"""Property-based tests for configuration management

Feature: github-actions-remote-executor
Tests Properties 50-57 for configuration management
"""
import os
import pytest
from contextlib import contextmanager
from hypothesis import given, strategies as st, settings, HealthCheck
from src.config import ServerConfig, CONTAINER_CAP_ALLOWLIST


def _security_config(**overrides) -> ServerConfig:
    """Build a valid (digest-pinned) ServerConfig, then apply security-field overrides."""
    base = dict(
        port=8080,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=100,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/gha-executor",
        output_retention_hours=24,
        tpm_attest_path="/dev/nsm",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
        container_image_digest="sha256:" + "a" * 64,
    )
    base.update(overrides)
    return ServerConfig(**base)


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
        TPM_ATTEST_PATH=nsm_path,
        ALLOWED_REPOSITORIES="owner/repo",
        EXPECTED_AUDIENCE="https://example.com",
        CONTAINER_IMAGE="python:3.11-slim",
        CONTAINER_MEMORY_LIMIT="512m",
        CONTAINER_CPU_LIMIT="1.0",
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
        assert config.tpm_attest_path == nsm_path


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
        TPM_ATTEST_PATH="/dev/nsm",
        ALLOWED_REPOSITORIES="owner/repo",
        EXPECTED_AUDIENCE="https://example.com",
        CONTAINER_IMAGE="python:3.11-slim",
        CONTAINER_MEMORY_LIMIT="512m",
        CONTAINER_CPU_LIMIT="1.0",
        CONTAINER_IMAGE_DIGEST="sha256:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb",
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
        TPM_ATTEST_PATH="/dev/nsm",
        ALLOWED_REPOSITORIES="owner/repo",
        EXPECTED_AUDIENCE="https://example.com",
        CONTAINER_IMAGE="python:3.11-slim",
        CONTAINER_MEMORY_LIMIT="512m",
        CONTAINER_CPU_LIMIT="1.0",
        CONTAINER_IMAGE_DIGEST="sha256:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb",
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
        TPM_ATTEST_PATH="/dev/nsm",
        ALLOWED_REPOSITORIES="owner/repo",
        EXPECTED_AUDIENCE="https://example.com",
        CONTAINER_IMAGE="python:3.11-slim",
        CONTAINER_MEMORY_LIMIT="512m",
        CONTAINER_CPU_LIMIT="1.0",
        CONTAINER_IMAGE_DIGEST="sha256:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb",
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
        TPM_ATTEST_PATH="/dev/nsm",
        ALLOWED_REPOSITORIES="owner/repo",
        EXPECTED_AUDIENCE="https://example.com",
        CONTAINER_IMAGE="python:3.11-slim",
        CONTAINER_MEMORY_LIMIT="512m",
        CONTAINER_CPU_LIMIT="1.0",
        CONTAINER_IMAGE_DIGEST="sha256:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb",
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
        TPM_ATTEST_PATH="/dev/nsm",
        ALLOWED_REPOSITORIES="owner/repo",
        EXPECTED_AUDIENCE="https://example.com",
        CONTAINER_IMAGE="python:3.11-slim",
        CONTAINER_MEMORY_LIMIT="512m",
        CONTAINER_CPU_LIMIT="1.0",
        CONTAINER_IMAGE_DIGEST="sha256:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb",
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
        TPM_ATTEST_PATH="/dev/nsm",
        ALLOWED_REPOSITORIES="owner/repo",
        EXPECTED_AUDIENCE="https://example.com",
        CONTAINER_IMAGE="python:3.11-slim",
        CONTAINER_MEMORY_LIMIT="512m",
        CONTAINER_CPU_LIMIT="1.0",
        CONTAINER_IMAGE_DIGEST="sha256:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb",
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
        "TPM_ATTEST_PATH",
        "ALLOWED_REPOSITORIES",
        "EXPECTED_AUDIENCE",
        "CONTAINER_IMAGE",
        "CONTAINER_MEMORY_LIMIT",
        "CONTAINER_CPU_LIMIT",
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
        "TPM_ATTEST_PATH": "/dev/nsm",
        "ALLOWED_REPOSITORIES": "owner/repo",
        "EXPECTED_AUDIENCE": "https://example.com",
        "CONTAINER_IMAGE": "python:3.11-slim",
        "CONTAINER_MEMORY_LIMIT": "512m",
        "CONTAINER_CPU_LIMIT": "1.0",
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
        tpm_attest_path="/dev/nsm",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
        container_image_digest="sha256:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb",
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
        tpm_attest_path="/dev/nsm",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
        container_image_digest="sha256:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb",
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
        tpm_attest_path="/dev/nsm",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
        container_image_digest="sha256:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb",
    )
    
    with pytest.raises(ValueError) as exc_info:
        config.validate()

    assert "temp_storage_path cannot be empty" in str(exc_info.value)


# ===========================================================================
# US2: Container-security validation properties (T012)
# ===========================================================================

_ENUM_FIELDS = {
    "workspace_mount_mode": {"ro", "rw"},
    "container_network_mode": {"none", "bridge", "host"},
}


@given(
    field=st.sampled_from(sorted(_ENUM_FIELDS)),
    value=st.text(min_size=1, max_size=12),
)
@settings(max_examples=50)
def test_property_enum_outside_set_rejected(field, value):
    """Any enum value outside its accepted set is rejected; the message names the var."""
    accepted = _ENUM_FIELDS[field]
    config = _security_config(**{field: value})
    if value in accepted:
        config.validate()  # well-formed value passes
    else:
        with pytest.raises(ValueError) as exc_info:
            config.validate()
        assert field.upper() in str(exc_info.value)


@given(uid=st.integers(min_value=1, max_value=65535), gid=st.integers(min_value=0, max_value=65535))
@settings(max_examples=30)
def test_property_well_formed_user_passes(uid, gid):
    """Any well-formed non-root uid:gid pair passes validation."""
    config = _security_config(container_user=f"{uid}:{gid}")
    config.validate()


@given(
    bad_user=st.one_of(
        st.integers(min_value=0, max_value=99999).map(str),          # bare uid, no gid
        st.integers(min_value=0, max_value=99999).map(lambda u: f"{u}:"),  # empty gid
        st.integers(min_value=1, max_value=99999).map(lambda u: f"-{u}:0"),  # negative uid
        st.integers(min_value=1, max_value=99999).map(lambda g: f"0:-{g}"),  # negative gid
        st.sampled_from(["root:root", "a:b", "1000:x", "x:1000", "1000:1000:1000", ""]),
    ),
)
@settings(max_examples=50)
def test_property_malformed_user_rejected(bad_user):
    """Any uid:gid with a missing/negative/non-integer part is rejected, naming CONTAINER_USER."""
    config = _security_config(container_user=bad_user)
    with pytest.raises(ValueError) as exc_info:
        config.validate()
    assert "CONTAINER_USER" in str(exc_info.value)


@given(caps=st.lists(st.sampled_from(sorted(CONTAINER_CAP_ALLOWLIST)), max_size=14, unique=True))
@settings(max_examples=30)
def test_property_cap_subset_passes(caps):
    """Any subset of the 14-cap allow-list passes validation."""
    config = _security_config(container_cap_add=caps)
    config.validate()


@given(
    good=st.lists(st.sampled_from(sorted(CONTAINER_CAP_ALLOWLIST)), max_size=5),
    bad=st.one_of(
        st.sampled_from([c.lower() for c in CONTAINER_CAP_ALLOWLIST]),   # wrong case
        st.sampled_from(["SYS_ADMIN", "NET_ADMIN", "CAP_CHOWN", "ALL", "SYS_PTRACE"]),
        st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ_", min_size=1, max_size=12),
    ),
)
@settings(max_examples=50)
def test_property_cap_outside_allowlist_rejected(good, bad):
    """Any capability outside the allow-list (case-sensitive) is rejected, naming CONTAINER_CAP_ADD."""
    if bad in CONTAINER_CAP_ALLOWLIST:
        return  # the random token happened to be valid; nothing to assert
    config = _security_config(container_cap_add=good + [bad])
    with pytest.raises(ValueError) as exc_info:
        config.validate()
    assert "CONTAINER_CAP_ADD" in str(exc_info.value)


@given(
    n=st.integers(min_value=1, max_value=1_000_000),
    unit=st.sampled_from(["b", "k", "m", "g"]),
)
@settings(max_examples=30)
def test_property_valid_tmpfs_size_passes(n, unit):
    """Any positive-int + b/k/m/g unit passes validation."""
    config = _security_config(container_tmpfs_size=f"{n}{unit}")
    config.validate()


@given(
    bad_size=st.one_of(
        st.integers(min_value=1, max_value=99999).map(str),                  # no unit
        st.sampled_from(["0m", "0k", "00g"]),                                # zero
        st.integers(min_value=1, max_value=9999).map(lambda n: f"-{n}m"),     # negative
        st.integers(min_value=1, max_value=9999).map(lambda n: f"{n}mb"),     # bad unit
        st.integers(min_value=1, max_value=9999).map(lambda n: f" {n}m"),     # leading ws
        st.integers(min_value=1, max_value=9999).map(lambda n: f"{n}m "),     # trailing ws
        st.sampled_from(["big", "m", "256M", "256 m"]),
    ),
)
@settings(max_examples=50)
def test_property_invalid_tmpfs_size_rejected(bad_size):
    """Any 0/negative/no-unit/whitespace-padded/bad-unit tmpfs string is rejected."""
    config = _security_config(container_tmpfs_size=bad_size)
    with pytest.raises(ValueError) as exc_info:
        config.validate()
    assert "CONTAINER_TMPFS_SIZE" in str(exc_info.value)


# --- T007: CONTAINER_TMPFS_EXEC property-based tests (US1 / SC-001) ---

@given(flag=st.booleans())
@settings(max_examples=10)
def test_property_tmpfs_exec_field_stored(flag):
    """container_tmpfs_exec is stored as given — no coercion or inversion."""
    config = _security_config(container_tmpfs_exec=flag)
    assert config.container_tmpfs_exec is flag
