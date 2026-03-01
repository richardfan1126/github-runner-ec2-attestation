"""Tests for configuration management"""
import os
import pytest
from src.config import ServerConfig


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
    monkeypatch.setenv("NSM_DEVICE_PATH", "/dev/nsm")
    
    config = ServerConfig.from_env()
    
    assert config.port == 8080
    assert config.max_concurrent_executions == 10
    assert config.execution_timeout_seconds == 300
    assert config.max_script_size_bytes == 1048576
    assert config.rate_limit_per_ip == 100
    assert config.rate_limit_window_seconds == 60
    assert config.temp_storage_path == "/tmp/gha-executor"
    assert config.output_retention_hours == 24
    assert config.nsm_device_path == "/dev/nsm"


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
        "NSM_DEVICE_PATH",
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
        nsm_device_path="/dev/nsm",
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
        nsm_device_path="/dev/nsm",
    )
    
    # Should not raise
    config.validate()
