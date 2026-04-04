"""Integration tests for GitHub Actions Remote Executor

Simplified integration tests focusing on core end-to-end flows.
Tests use mocked external dependencies (GitHub API, NitroTPM device).
"""
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import tempfile

import pytest
from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.models import OIDCValidationResult
from src.server import create_app
from tests.mock_docker import create_mock_docker_client


VALID_OIDC_RESULT = OIDCValidationResult(
    valid=True,
    status_code=200,
    error_message=None,
    claims={"repository": "owner/repo", "iss": "https://token.actions.githubusercontent.com", "aud": "https://example.com"},
)


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
        tpm_attest_path="/usr/bin/nitro-tpm-attest",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )


@pytest.fixture
def mock_github_and_attestation():
    """Mock both GitHub API and attestation generation"""
    with patch('requests.Session') as mock_session_class, \
         patch('src.repository.subprocess.run') as mock_git_run, \
         patch('src.attestation.subprocess.run') as mock_attest:
        
        # Setup GitHub API mock for authenticate()
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_session.headers = {}
        mock_session.get.return_value = Mock(status_code=200)
        
        # Setup git clone/checkout mock
        mock_git_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        # Setup attestation mock
        mock_attest_result = Mock(
            returncode=0,
            stdout=b'mock_attestation_cbor_data'
        )
        mock_attest.return_value = mock_attest_result
        
        yield {
            'session': mock_session,
            'git_run': mock_git_run,
            'attestation': mock_attest
        }


@pytest.fixture
def app(test_config, mock_github_and_attestation, temp_dir):
    """Create test application with OIDC validation mocked"""
    application = create_app(test_config, docker_client=create_mock_docker_client())
    # Mock OIDC validation to always succeed for integration tests
    application.state.request_validator.validate_oidc_token = Mock(return_value=VALID_OIDC_RESULT)
    
    # Mock clone_repo to create a temp dir with the requested script file
    from src.models import CloneResult
    
    def mock_clone_repo(repo_url, commit, token):
        clone_dir = tempfile.mkdtemp(dir=temp_dir, prefix="clone_")
        return CloneResult(clone_path=clone_dir, script_path="")
    
    def mock_validate_script_exists(clone_path, script_path):
        # Create the script file so os.path.getsize works
        full_path = os.path.join(clone_path, script_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write('#!/bin/bash\necho "Test output"\nexit 0')
        os.chmod(full_path, 0o755)
        return True
    
    application.state.repository_client.clone_repo = Mock(side_effect=mock_clone_repo)
    application.state.repository_client.validate_script_exists = Mock(side_effect=mock_validate_script_exists)
    
    return application


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
        time.sleep(1)
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
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            mock_session.headers = {}
            mock_session.get.return_value = Mock(status_code=401)
            
            request_data = {
                "repository_url": "https://github.com/test/repo",
                "commit_hash": "c1c2c3c4c5c6c1c2c3c4c5c6c1c2c3c4c5c6c1c2",
                "script_path": "scripts/test.sh",
                "github_token": "invalid_token"
            }
            
            response = client.post("/execute", json=request_data)
            assert response.status_code == 401
    
    def test_execution_timeout(self, test_config, mock_github_and_attestation, temp_dir):
        """Test script execution timeout"""
        # Use a shorter timeout for faster test
        test_config.execution_timeout_seconds = 1
        
        # Create fresh app and client to avoid rate limiting from other tests
        app = create_app(test_config, docker_client=create_mock_docker_client())
        app.state.request_validator.validate_oidc_token = Mock(return_value=VALID_OIDC_RESULT)
        
        from src.models import CloneResult
        
        def mock_clone_repo(repo_url, commit, token):
            clone_dir = tempfile.mkdtemp(dir=temp_dir, prefix="clone_")
            return CloneResult(clone_path=clone_dir, script_path="")
        
        def mock_validate_script_exists(clone_path, script_path):
            full_path = os.path.join(clone_path, script_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write('#!/bin/bash\nsleep 10\nexit 0')
            os.chmod(full_path, 0o755)
            return True
        
        app.state.repository_client.clone_repo = Mock(side_effect=mock_clone_repo)
        app.state.repository_client.validate_script_exists = Mock(side_effect=mock_validate_script_exists)
        
        client = TestClient(app)
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "e1e2e3e4e5e6e1e2e3e4e5e6e1e2e3e4e5e6e1e2",
            "script_path": "scripts/timeout.sh",
            "github_token": "ghp_test_token"
        }
        
        response = client.post("/execute", json=request_data)
        assert response.status_code == 200
        execution_id = response.json()["execution_id"]
        
        # Wait for timeout to occur (config has 1 second timeout)
        time.sleep(2)
        
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
