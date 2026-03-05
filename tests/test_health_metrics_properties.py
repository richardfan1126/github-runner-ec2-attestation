"""Property-based tests for health and metrics endpoints"""
import shutil
import tempfile
from unittest.mock import Mock, patch
from hypothesis import given, strategies as st, settings
from fastapi.testclient import TestClient

from src.server import create_app
from src.config import ServerConfig
from src.models import ExecutionStatus


# Test configuration
def get_test_config():
    """Create test configuration"""
    return ServerConfig(
        port=8000,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,  # 1MB
        rate_limit_per_ip=10,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/test",
        output_retention_hours=24,
        nsm_device_path="/usr/bin/nitro-tpm-attest"
    )


# Feature: github-actions-remote-executor, Property 58: Health Check Attestation Status
@settings(max_examples=100, deadline=None)
@given(st.booleans())
def test_health_check_attestation_status(attestation_available):
    """
    **Validates: Requirements 10.3**
    
    For any health check request, the response should include the 
    attestation capability status.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    # Mock disk usage to avoid file system errors
    mock_usage = Mock(free=10240 * 1024 * 1024, total=20480 * 1024 * 1024, used=10240 * 1024 * 1024)
    
    # Mock attestation availability
    with patch('shutil.disk_usage') as mock_disk_usage:
        mock_disk_usage.return_value = mock_usage
        
        with patch.object(app.state.attestation_generator, 'verify_nsm_available') as mock_verify:
            mock_verify.return_value = attestation_available
            
            response = client.get("/health")
            
            assert response.status_code == 200
            data = response.json()
            
            # Verify response includes attestation status
            assert "attestation_available" in data
            assert isinstance(data["attestation_available"], bool)
            assert data["attestation_available"] == attestation_available


# Feature: github-actions-remote-executor, Property 59: Health Check Disk Space
@settings(max_examples=100, deadline=None)
@given(st.integers(min_value=0, max_value=1000000))
def test_health_check_disk_space(free_space_mb):
    """
    **Validates: Requirements 10.4**
    
    For any health check request, the response should include disk space 
    availability information.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    # Mock disk usage
    free_bytes = free_space_mb * 1024 * 1024
    mock_usage = Mock(free=free_bytes, total=free_bytes * 2, used=free_bytes)
    
    with patch('shutil.disk_usage') as mock_disk_usage:
        mock_disk_usage.return_value = mock_usage
        
        with patch.object(app.state.attestation_generator, 'verify_nsm_available') as mock_verify:
            mock_verify.return_value = True
            
            response = client.get("/health")
            
            assert response.status_code == 200
            data = response.json()
            
            # Verify response includes disk space
            assert "disk_space_mb" in data
            assert isinstance(data["disk_space_mb"], int)
            assert data["disk_space_mb"] == free_space_mb


# Feature: github-actions-remote-executor, Property 60: Execution Metrics Tracking
@settings(max_examples=100, deadline=None)
@given(
    st.integers(min_value=0, max_value=100),  # successful
    st.integers(min_value=0, max_value=100)   # failed
)
def test_execution_metrics_tracking(successful_count, failed_count):
    """
    **Validates: Requirements 10.6**
    
    For any set of script executions, the metrics endpoint should accurately 
    track the count of successful and failed executions.
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    exec_manager = app.state.execution_manager
    
    # Create executions and update their status
    for _ in range(successful_count):
        record = exec_manager.create_execution(
            "https://github.com/test/repo",
            "a" * 40,
            "test.sh",
            300
        )
        exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        exec_manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    
    for _ in range(failed_count):
        record = exec_manager.create_execution(
            "https://github.com/test/repo",
            "b" * 40,
            "test.sh",
            300
        )
        exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        exec_manager.update_status(record.execution_id, ExecutionStatus.FAILED, exit_code=1)
    
    # Get metrics
    response = client.get("/metrics")
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify metrics accuracy
    assert "total_executions" in data
    assert "successful_executions" in data
    assert "failed_executions" in data
    assert "average_duration_ms" in data
    assert "active_executions" in data
    
    assert data["total_executions"] == successful_count + failed_count
    assert data["successful_executions"] == successful_count
    assert data["failed_executions"] == failed_count
    assert isinstance(data["average_duration_ms"], (int, float))
    assert isinstance(data["active_executions"], int)
