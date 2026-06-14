"""Tests for configuration management"""
import os
import pytest
from src.config import ServerConfig, load_config, ConfigurationError, CONTAINER_DEFAULT_CAP_ADD


def _set_base_env(monkeypatch):
    """Set all required env vars to valid values (digest-pinned so validate() passes)."""
    monkeypatch.setenv("SERVER_PORT", "8080")
    monkeypatch.setenv("MAX_CONCURRENT_EXECUTIONS", "10")
    monkeypatch.setenv("EXECUTION_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("MAX_SCRIPT_SIZE_BYTES", "1048576")
    monkeypatch.setenv("RATE_LIMIT_PER_IP", "100")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("TEMP_STORAGE_PATH", "/tmp/gha-executor")
    monkeypatch.setenv("OUTPUT_RETENTION_HOURS", "24")
    monkeypatch.setenv("TPM_ATTEST_PATH", "/dev/nsm")
    monkeypatch.setenv("ALLOWED_REPOSITORIES", "owner/repo")
    monkeypatch.setenv("EXPECTED_AUDIENCE", "https://example.com")
    monkeypatch.setenv("CONTAINER_IMAGE", "python:3.11-slim")
    monkeypatch.setenv("CONTAINER_MEMORY_LIMIT", "512m")
    monkeypatch.setenv("CONTAINER_CPU_LIMIT", "1.0")
    monkeypatch.setenv("CONTAINER_IMAGE_DIGEST", "sha256:" + "a" * 64)
    # Ensure no security vars leak in from the ambient environment.
    for var in (
        "CONTAINER_USER", "CONTAINER_ALLOW_ROOT", "CONTAINER_CAP_ADD",
        "NO_NEW_PRIVILEGES", "CONTAINER_READ_ONLY_ROOTFS", "CONTAINER_TMPFS_SIZE",
        "WORKSPACE_MOUNT_MODE", "CONTAINER_NETWORK_MODE",
    ):
        monkeypatch.delenv(var, raising=False)


def test_config_from_env_success(monkeypatch):
    """Test successful configuration loading from environment variables"""
    monkeypatch.setenv("SERVER_PORT", "8080")
    monkeypatch.setenv("MAX_CONCURRENT_EXECUTIONS", "10")
    monkeypatch.setenv("EXECUTION_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("MAX_SCRIPT_SIZE_BYTES", "1048576")
    monkeypatch.setenv("RATE_LIMIT_PER_IP", "100")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("TEMP_STORAGE_PATH", "/tmp/gha-executor")
    monkeypatch.setenv("OUTPUT_RETENTION_HOURS", "24")
    monkeypatch.setenv("TPM_ATTEST_PATH", "/dev/nsm")
    monkeypatch.setenv("ALLOWED_REPOSITORIES", "owner/repo1,owner/repo2")
    monkeypatch.setenv("EXPECTED_AUDIENCE", "https://example.com")
    monkeypatch.setenv("CONTAINER_IMAGE", "python:3.11-slim")
    monkeypatch.setenv("CONTAINER_MEMORY_LIMIT", "512m")
    monkeypatch.setenv("CONTAINER_CPU_LIMIT", "1.0")
    
    config = ServerConfig.from_env()
    
    assert config.port == 8080
    assert config.max_concurrent_executions == 10
    assert config.execution_timeout_seconds == 300
    assert config.max_script_size_bytes == 1048576
    assert config.rate_limit_per_ip == 100
    assert config.rate_limit_window_seconds == 60
    assert config.temp_storage_path == "/tmp/gha-executor"
    assert config.output_retention_hours == 24
    assert config.tpm_attest_path == "/dev/nsm"
    assert config.allowed_repositories == ["owner/repo1", "owner/repo2"]
    assert config.expected_audience == "https://example.com"
    assert config.container_image == "python:3.11-slim"
    assert config.container_memory_limit == "512m"
    assert config.container_cpu_limit == 1.0


def test_config_missing_required_vars(monkeypatch):
    """Test that missing required environment variables raise ValueError"""
    # Clear all environment variables
    for var in [
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
    ]:
        monkeypatch.delenv(var, raising=False)
    
    with pytest.raises(ValueError) as exc_info:
        ServerConfig.from_env()
    
    assert "Missing required environment variables" in str(exc_info.value)


def test_config_validation_invalid_port():
    """Test configuration validation rejects invalid port"""
    config = ServerConfig(
        port=99999,
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
    )
    
    with pytest.raises(ValueError) as exc_info:
        config.validate()
    
    assert "Invalid port" in str(exc_info.value)


def test_config_validation_success():
    """Test configuration validation passes for valid config"""
    config = ServerConfig(
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
        container_image_digest="sha256:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb",
    )

    # Should not raise
    config.validate()


# ===========================================================================
# US2: Validated relaxation of container-security defaults (T011)
# ===========================================================================

def test_security_defaults_when_unset(monkeypatch):
    """With none of the eight vars set, the hardened defaults resolve."""
    _set_base_env(monkeypatch)
    config = load_config()
    assert config.container_user == "65534:65534"
    assert config.container_allow_root is False
    assert config.container_cap_add is None  # unset -> default set applied downstream
    assert config.no_new_privileges is True
    assert config.container_read_only_rootfs is True
    assert config.container_tmpfs_size == "256m"
    assert config.workspace_mount_mode == "ro"
    assert config.container_network_mode == "none"


def test_valid_network_mode_override(monkeypatch):
    _set_base_env(monkeypatch)
    monkeypatch.setenv("CONTAINER_NETWORK_MODE", "bridge")
    assert load_config().container_network_mode == "bridge"


def test_valid_workspace_mount_mode_override(monkeypatch):
    _set_base_env(monkeypatch)
    monkeypatch.setenv("WORKSPACE_MOUNT_MODE", "rw")
    assert load_config().workspace_mount_mode == "rw"


def test_valid_root_user_with_allow_root(monkeypatch):
    _set_base_env(monkeypatch)
    monkeypatch.setenv("CONTAINER_USER", "0:0")
    monkeypatch.setenv("CONTAINER_ALLOW_ROOT", "true")
    config = load_config()
    assert config.container_user == "0:0"
    assert config.container_allow_root is True


def test_valid_cap_add_subset(monkeypatch):
    _set_base_env(monkeypatch)
    monkeypatch.setenv("CONTAINER_CAP_ADD", "CHOWN,KILL")
    assert load_config().container_cap_add == ["CHOWN", "KILL"]


def test_empty_cap_add_resolves_to_empty_list(monkeypatch):
    _set_base_env(monkeypatch)
    monkeypatch.setenv("CONTAINER_CAP_ADD", "")
    assert load_config().container_cap_add == []


def test_empty_tmpfs_size_means_no_tmpfs(monkeypatch):
    _set_base_env(monkeypatch)
    monkeypatch.setenv("CONTAINER_TMPFS_SIZE", "")
    assert load_config().container_tmpfs_size == ""


@pytest.mark.parametrize(
    "var,value",
    [
        ("CONTAINER_ALLOW_ROOT", "maybe"),
        ("NO_NEW_PRIVILEGES", "on"),
        ("CONTAINER_READ_ONLY_ROOTFS", "readonly"),
        ("WORKSPACE_MOUNT_MODE", "readwrite"),
        ("WORKSPACE_MOUNT_MODE", "RW"),
        ("CONTAINER_NETWORK_MODE", "nat"),
        ("CONTAINER_NETWORK_MODE", "None"),
        ("CONTAINER_USER", "1000"),
        ("CONTAINER_USER", "1000:"),
        ("CONTAINER_USER", "-1:0"),
        ("CONTAINER_USER", "root:root"),
        ("CONTAINER_CAP_ADD", "SYS_ADMIN"),
        ("CONTAINER_CAP_ADD", "chown"),
        ("CONTAINER_TMPFS_SIZE", "256"),
        ("CONTAINER_TMPFS_SIZE", "0m"),
        ("CONTAINER_TMPFS_SIZE", "-5m"),
        ("CONTAINER_TMPFS_SIZE", "256mb"),
        ("CONTAINER_TMPFS_SIZE", "big"),
    ],
)
def test_invalid_security_value_fails_fast_naming_variable(monkeypatch, var, value):
    """Each invalid value raises ConfigurationError whose message names the variable."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv(var, value)
    with pytest.raises(ConfigurationError) as exc_info:
        load_config()
    assert var in str(exc_info.value), (
        f"Error for {var}={value!r} should name the variable, got: {exc_info.value}"
    )


def test_root_gate_error_names_both_variables(monkeypatch):
    """Root user while disallowed names BOTH CONTAINER_USER and CONTAINER_ALLOW_ROOT."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("CONTAINER_USER", "0:0")
    monkeypatch.setenv("CONTAINER_ALLOW_ROOT", "false")
    with pytest.raises(ConfigurationError) as exc_info:
        load_config()
    message = str(exc_info.value)
    assert "CONTAINER_USER" in message
    assert "CONTAINER_ALLOW_ROOT" in message


def test_invalid_network_mode_message_lists_accepted_values(monkeypatch):
    """The enum error states the accepted values (FR-017)."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("CONTAINER_NETWORK_MODE", "nat")
    with pytest.raises(ConfigurationError) as exc_info:
        load_config()
    message = str(exc_info.value)
    assert "none, bridge, host" in message
