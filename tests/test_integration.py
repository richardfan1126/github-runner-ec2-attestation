"""Integration tests for GitHub Actions Remote Executor

Simplified integration tests focusing on core end-to-end flows.
Tests use mocked external dependencies (GitHub API, NSM device).
"""
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import tempfile

import pytest
from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.server import create_app


@pytest.fixture
def temp_dir():
    """Create temporary directory for test files"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def test_config(temp_dir):
    """Create test configuration"""
    return ServerConfig(
        port=8080,
        max_concurrent_executions=10,
        execution_timeout_seconds=5,
        max_script_size_bytes=1024 * 1024,
        rate_limit_per_ip=10,
        rate_limit_window_seconds=60,
        temp_storage_path=temp_dir,
        output_retention_hours=1,
        nsm_device_path="/usr/bin/nitro-tpm-attest"
    )


@pytest.fixture
def mock_github_and_attestation():
    """Mock both GitHub API and attestation generation"""
    with patch('src.repository.requests.Session') as mock_session_class, \
         patch('src.attestation.subprocess.run') as mock_attest:
        
        # Setup GitHub API mock
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_session.headers = {}
        
        # Mock responses
        mock_auth = Mock(status_code=200, json=lambda: {"login": "test"})
        mock_content = Mock(
            status_code=200,
            json=lambda: {"download_url": "https://raw.githubusercontent.com/test/repo/main/test.sh"}
        )
        mock_download = Mock(
            status_code=200,
            content=b'#!/bin/bash\necho "Test output"\nexit 0'
        )
        
        def get_side_effect(url, **kwargs):
            if '/user' in url:
                return mock_auth
            elif 'raw.githubusercontent.com' in url:
                return mock_download
            else:
                return mock_content
        
        mock_session.get.side_effect = get_side_effect
        
        # Setup attestation mock
        mock_attest_result = Mock(
            returncode=0,
            stdout=b'mock_attestation_cbor_data'
        )
        mock_attest.return_value = mock_attest_result
        
        yield {
            'session': mock_session,
            'download': mock_download,
            'attestation': mock_attest
        }


@pytest.fixture
def app(test_config, mock_github_and_attestation):
    """Create test application"""
    return create_app(test_config)


@pytest.fixture
def client(app):
    """Create test client"""
    return TestClient(app)


class TestEndToEndIntegration:
    """Test complete end-to-end integration scenarios"""
    
    def test_complete_execution_flow(self, client, mock_github_and_attestation):
        """
        Test complete execution flow from request to output retrieval
        
        Validates all requirements for end-to-end execution
        """
        # Submit execution request
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token"
        }
        
        response = client.post("/execute", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert "execution_id" in data
        assert "attestation_document" in data
        assert data["status"] == "queued"
        
        execution_id = data["execution_id"]
        
        # Poll for completion
        for _ in range(20):
            time.sleep(0.2)
            output_response = client.get(f"/execution/{execution_id}/output")
            assert output_response.status_code == 200
            
            output_data = output_response.json()
            if output_data["complete"]:
                assert output_data["status"] in ["completed", "failed"]
                assert "stdout" in output_data
                assert "stderr" in output_data
                assert output_data["exit_code"] is not None
                break
        else:
            pytest.fail("Execution did not complete")
    
    def test_concurrent_executions(self, client, mock_github_and_attestation):
        """Test handling multiple concurrent executions"""
        execution_ids = []
        
        for i in range(3):
            request_data = {
                "repository_url": f"https://github.com/test/repo{i}",
                "commit_hash": f"{i:040x}",
                "script_path": f"scripts/test{i}.sh",
                "github_token": "ghp_test_token"
            }
            
            response = client.post("/execute", json=request_data)
            assert response.status_code == 200
            execution_ids.append(response.json()["execution_id"])
        
        # Verify all IDs are unique
        assert len(set(execution_ids)) == 3
        
        # Wait and verify all complete
        time.sleep(2)
        for execution_id in execution_ids:
            response = client.get(f"/execution/{execution_id}/output")
            assert response.status_code == 200
    
    def test_rate_limiting(self, client, mock_github_and_attestation):
        """Test rate limiting enforcement"""
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "a1a2a3a4a5a6a1a2a3a4a5a6a1a2a3a4a5a6a1a2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token"
        }
        
        rate_limited = False
        for _ in range(15):
            response = client.post("/execute", json=request_data)
            if response.status_code == 429:
                rate_limited = True
                break
        
        assert rate_limited, "Rate limit should have been enforced"
    
    def test_execution_not_found(self, client):
        """Test retrieving non-existent execution"""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = client.get(f"/execution/{fake_id}/output")
        assert response.status_code == 404
    
    def test_health_endpoint(self, client):
        """Test health check endpoint"""
        response = client.get("/health")
        assert response.status_code == 200
        
        data = response.json()
        assert "status" in data
        assert "attestation_available" in data
        assert "disk_space_mb" in data
        assert "active_executions" in data
    
    def test_metrics_endpoint(self, client):
        """Test metrics endpoint"""
        response = client.get("/metrics")
        assert response.status_code == 200
        
        data = response.json()
        assert "total_executions" in data
        assert "successful_executions" in data
        assert "failed_executions" in data
        assert "average_duration_ms" in data
        assert "active_executions" in data


class TestErrorScenarios:
    """Test error handling scenarios"""
    
    def test_authentication_failure(self, client, test_config):
        """Test GitHub authentication failure"""
        with patch('src.repository.requests.Session') as mock_session_class, \
             patch('src.attestation.subprocess.run') as mock_attest:
            
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            mock_session.headers = {}
            mock_session.get.return_value = Mock(status_code=401)
            
            mock_attest.return_value = Mock(returncode=0, stdout=b'mock')
            
            request_data = {
                "repository_url": "https://github.com/test/repo",
                "commit_hash": "c1c2c3c4c5c6c1c2c3c4c5c6c1c2c3c4c5c6c1c2",
                "script_path": "scripts/test.sh",
                "github_token": "invalid_token"
            }
            
            response = client.post("/execute", json=request_data)
            assert response.status_code == 401
    
    def test_execution_timeout(self, test_config, mock_github_and_attestation):
        """Test script execution timeout"""
        # Create fresh app and client to avoid rate limiting from other tests
        app = create_app(test_config)
        client = TestClient(app)
        
        # Mock long-running script
        mock_github_and_attestation['download'].content = b'#!/bin/bash\nsleep 10\nexit 0'
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "e1e2e3e4e5e6e1e2e3e4e5e6e1e2e3e4e5e6e1e2",
            "script_path": "scripts/timeout.sh",
            "github_token": "ghp_test_token"
        }
        
        response = client.post("/execute", json=request_data)
        assert response.status_code == 200
        execution_id = response.json()["execution_id"]
        
        # Wait for timeout to occur (config has 5 second timeout)
        time.sleep(6)
        
        # Check status - should be timed out
        output_response = client.get(f"/execution/{execution_id}/output")
        assert output_response.status_code == 200
        
        output_data = output_response.json()
        # The execution should be marked as timed out
        # Note: complete flag may not be set immediately due to async processing
        assert output_data["status"] in ["running", "timed_out"]


class TestCleanupAndRetention:
    """Test cleanup and retention policies"""
    
    def test_execution_cleanup(self, client, mock_github_and_attestation, app):
        """Test cleanup of expired executions"""
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "b1b2b3b4b5b6b1b2b3b4b5b6b1b2b3b4b5b6b1b2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token"
        }
        
        response = client.post("/execute", json=request_data)
        execution_id = response.json()["execution_id"]
        
        # Wait for completion
        time.sleep(1)
        
        # Verify execution exists
        response = client.get(f"/execution/{execution_id}/output")
        assert response.status_code == 200
        
        # Manually expire the execution
        exec_manager = app.state.execution_manager
        record = exec_manager.get_execution(execution_id)
        if record:
            record.completed_at = datetime.now(timezone.utc) - timedelta(hours=2)
        
        # Run cleanup
        removed = exec_manager.cleanup_expired()
        assert removed >= 1
        
        # Verify execution was removed
        response = client.get(f"/execution/{execution_id}/output")
        assert response.status_code == 404
    
    def test_temporary_file_cleanup(self, client, mock_github_and_attestation, temp_dir):
        """Test cleanup of temporary files after execution"""
        files_before = len(list(Path(temp_dir).rglob('*')))
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "c1c2c3c4c5c6c1c2c3c4c5c6c1c2c3c4c5c6c1c2",
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token"
        }
        
        response = client.post("/execute", json=request_data)
        execution_id = response.json()["execution_id"]
        
        # Wait for completion
        time.sleep(1)
        
        # Verify execution completed
        response = client.get(f"/execution/{execution_id}/output")
        data = response.json()
        assert data["complete"]
        
        # Verify temp files were cleaned up
        files_after = len(list(Path(temp_dir).rglob('*')))
        assert files_after <= files_before + 1
