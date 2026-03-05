"""Unit tests for health and metrics endpoints"""
import threading
import time
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient

from src.server import create_app
from src.config import ServerConfig
from src.models import ExecutionStatus


def get_test_config():
    """Create test configuration"""
    return ServerConfig(
        port=8000,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=10,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/test",
        output_retention_hours=24,
        nsm_device_path="/usr/bin/nitro-tpm-attest"
    )


def test_health_endpoint_response_structure():
    """
    Test that health endpoint returns correct response structure
    
    Requirements: 10.1, 10.2, 10.3, 10.4
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    # Mock dependencies
    mock_usage = Mock(free=10240 * 1024 * 1024, total=20480 * 1024 * 1024, used=10240 * 1024 * 1024)
    
    with patch('shutil.disk_usage') as mock_disk_usage:
        mock_disk_usage.return_value = mock_usage
        
        with patch.object(app.state.attestation_generator, 'verify_nsm_available') as mock_verify:
            mock_verify.return_value = True
            
            response = client.get("/health")
            
            # Verify status code
            assert response.status_code == 200
            
            # Verify response structure
            data = response.json()
            assert "status" in data
            assert "attestation_available" in data
            assert "disk_space_mb" in data
            assert "active_executions" in data
            
            # Verify data types
            assert isinstance(data["status"], str)
            assert isinstance(data["attestation_available"], bool)
            assert isinstance(data["disk_space_mb"], int)
            assert isinstance(data["active_executions"], int)
            
            # Verify values
            assert data["status"] == "healthy"
            assert data["attestation_available"] is True
            assert data["disk_space_mb"] == 10240
            assert data["active_executions"] == 0


def test_health_endpoint_when_attestation_unavailable():
    """
    Test health endpoint when attestation is not available
    
    Requirements: 10.3
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    mock_usage = Mock(free=5000 * 1024 * 1024, total=20480 * 1024 * 1024, used=15480 * 1024 * 1024)
    
    with patch('shutil.disk_usage') as mock_disk_usage:
        mock_disk_usage.return_value = mock_usage
        
        with patch.object(app.state.attestation_generator, 'verify_nsm_available') as mock_verify:
            mock_verify.return_value = False
            
            response = client.get("/health")
            
            assert response.status_code == 200
            data = response.json()
            
            assert data["status"] == "healthy"
            assert data["attestation_available"] is False


def test_health_endpoint_with_active_executions():
    """
    Test health endpoint includes active executions count
    
    Requirements: 10.4
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    # Create some active executions
    exec_manager = app.state.execution_manager
    for i in range(3):
        record = exec_manager.create_execution(
            f"https://github.com/test/repo{i}",
            "a" * 40,
            "test.sh",
            300
        )
        exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    
    mock_usage = Mock(free=10240 * 1024 * 1024, total=20480 * 1024 * 1024, used=10240 * 1024 * 1024)
    
    with patch('shutil.disk_usage') as mock_disk_usage:
        mock_disk_usage.return_value = mock_usage
        
        with patch.object(app.state.attestation_generator, 'verify_nsm_available') as mock_verify:
            mock_verify.return_value = True
            
            response = client.get("/health")
            
            assert response.status_code == 200
            data = response.json()
            
            assert data["active_executions"] == 3


def test_health_endpoint_handles_errors_gracefully():
    """
    Test health endpoint returns degraded status on errors
    
    Requirements: 10.1, 10.2
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    # Simulate error by not mocking disk_usage (will fail on non-existent path)
    with patch.object(app.state.attestation_generator, 'verify_nsm_available') as mock_verify:
        mock_verify.side_effect = Exception("Test error")
        
        response = client.get("/health")
        
        # Should still return 200 but with degraded status
        assert response.status_code == 200
        data = response.json()
        
        assert data["status"] == "degraded"
        assert data["attestation_available"] is False
        assert data["disk_space_mb"] == 0
        assert data["active_executions"] == 0


def test_metrics_endpoint_response_structure():
    """
    Test that metrics endpoint returns correct response structure
    
    Requirements: 10.5, 10.6
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    response = client.get("/metrics")
    
    # Verify status code
    assert response.status_code == 200
    
    # Verify response structure
    data = response.json()
    assert "total_executions" in data
    assert "successful_executions" in data
    assert "failed_executions" in data
    assert "average_duration_ms" in data
    assert "active_executions" in data
    
    # Verify data types
    assert isinstance(data["total_executions"], int)
    assert isinstance(data["successful_executions"], int)
    assert isinstance(data["failed_executions"], int)
    assert isinstance(data["average_duration_ms"], (int, float))
    assert isinstance(data["active_executions"], int)
    
    # Initial values should be zero
    assert data["total_executions"] == 0
    assert data["successful_executions"] == 0
    assert data["failed_executions"] == 0
    assert data["average_duration_ms"] == 0
    assert data["active_executions"] == 0


def test_metrics_accuracy():
    """
    Test that metrics accurately track execution counts
    
    Requirements: 10.6
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    exec_manager = app.state.execution_manager
    
    # Create 5 successful executions
    for i in range(5):
        record = exec_manager.create_execution(
            f"https://github.com/test/repo{i}",
            "a" * 40,
            "test.sh",
            300
        )
        exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        exec_manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    
    # Create 3 failed executions
    for i in range(3):
        record = exec_manager.create_execution(
            f"https://github.com/test/repo{i+5}",
            "b" * 40,
            "test.sh",
            300
        )
        exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        exec_manager.update_status(record.execution_id, ExecutionStatus.FAILED, exit_code=1)
    
    # Create 2 timed out executions
    for i in range(2):
        record = exec_manager.create_execution(
            f"https://github.com/test/repo{i+8}",
            "c" * 40,
            "test.sh",
            300
        )
        exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        exec_manager.update_status(record.execution_id, ExecutionStatus.TIMED_OUT, exit_code=-1)
    
    response = client.get("/metrics")
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify counts
    assert data["total_executions"] == 10
    assert data["successful_executions"] == 5
    assert data["failed_executions"] == 5  # failed + timed_out
    assert data["active_executions"] == 0


def test_metrics_under_concurrent_executions():
    """
    Test metrics accuracy under concurrent execution load
    
    Requirements: 10.5, 10.6
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    exec_manager = app.state.execution_manager
    
    def create_and_complete_execution(success: bool):
        """Helper to create and complete an execution"""
        record = exec_manager.create_execution(
            "https://github.com/test/repo",
            "a" * 40,
            "test.sh",
            300
        )
        exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        
        if success:
            exec_manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
        else:
            exec_manager.update_status(record.execution_id, ExecutionStatus.FAILED, exit_code=1)
    
    # Create executions concurrently
    threads = []
    for i in range(10):
        success = i % 2 == 0  # Alternate between success and failure
        thread = threading.Thread(target=create_and_complete_execution, args=(success,))
        threads.append(thread)
        thread.start()
    
    # Wait for all threads to complete
    for thread in threads:
        thread.join()
    
    response = client.get("/metrics")
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify counts are accurate despite concurrent access
    assert data["total_executions"] == 10
    assert data["successful_executions"] == 5
    assert data["failed_executions"] == 5
    assert data["active_executions"] == 0


def test_metrics_average_duration():
    """
    Test that metrics correctly calculate average execution duration
    
    Requirements: 10.5
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    exec_manager = app.state.execution_manager
    
    # Create executions with known durations
    durations_ms = []
    for i in range(3):
        record = exec_manager.create_execution(
            f"https://github.com/test/repo{i}",
            "a" * 40,
            "test.sh",
            300
        )
        exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        
        # Simulate some execution time
        time.sleep(0.01)  # 10ms
        
        exec_manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
        
        # Calculate actual duration
        execution = exec_manager.get_execution(record.execution_id)
        if execution.started_at and execution.completed_at:
            duration = (execution.completed_at - execution.started_at).total_seconds() * 1000
            durations_ms.append(duration)
    
    response = client.get("/metrics")
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify average duration is calculated
    assert data["average_duration_ms"] > 0
    
    # Should be close to the actual average (within reasonable margin)
    expected_avg = sum(durations_ms) / len(durations_ms)
    assert abs(data["average_duration_ms"] - expected_avg) < 1.0  # Within 1ms


def test_metrics_with_active_executions():
    """
    Test metrics includes count of active executions
    
    Requirements: 10.5
    """
    app = create_app(get_test_config())
    client = TestClient(app)
    
    exec_manager = app.state.execution_manager
    
    # Create some completed executions
    for i in range(2):
        record = exec_manager.create_execution(
            f"https://github.com/test/repo{i}",
            "a" * 40,
            "test.sh",
            300
        )
        exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        exec_manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    
    # Create some active executions
    for i in range(3):
        record = exec_manager.create_execution(
            f"https://github.com/test/repo{i+2}",
            "b" * 40,
            "test.sh",
            300
        )
        exec_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    
    response = client.get("/metrics")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["total_executions"] == 5
    assert data["successful_executions"] == 2
    assert data["active_executions"] == 3
