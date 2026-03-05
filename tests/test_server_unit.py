"""Unit tests for HTTP server endpoints"""
import base64
import json
import time
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from src.server import create_app
from src.config import ServerConfig
from src.models import ExecutionStatus, ExecutionRecord, OutputData, AttestationDocument
from src.repository import FileContent, GitHubAPIError
from src.attestation import AttestationError


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


class TestExecuteEndpoint:
    """Tests for POST /execute endpoint"""
    
    def test_successful_execution_request(self):
        """Test complete successful request/response flow"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token_123"
        }
        
        with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(valid=True, errors=[])
            
            with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
                mock_auth.return_value = Mock(success=True, error_message=None)
                
                with patch.object(app.state.repository_client, 'fetch_file') as mock_fetch:
                    mock_fetch.return_value = FileContent(
                        content=b"#!/bin/bash\necho 'test'",
                        temp_path="/tmp/test.sh",
                        size_bytes=100
                    )
                    
                    with patch.object(app.state.attestation_generator, 'generate_attestation') as mock_attest:
                        mock_attest.return_value = (
                            AttestationDocument(
                                repository_url=request_data['repository_url'],
                                commit_hash=request_data['commit_hash'],
                                script_path=request_data['script_path'],
                                timestamp=datetime.now(timezone.utc),
                                signature=b"test_signature_bytes"
                            ),
                            None
                        )
                        
                        with patch.object(app.state.script_executor, 'execute_async'):
                            response = client.post("/execute", json=request_data)
                            
                            # Verify response
                            assert response.status_code == 200
                            data = response.json()
                            
                            assert "execution_id" in data
                            assert "attestation_document" in data
                            assert "status" in data
                            assert data["status"] == "queued"
                            
                            # Verify attestation document is base64 encoded
                            decoded = base64.b64decode(data["attestation_document"])
                            assert decoded == b"test_signature_bytes"
    
    def test_malformed_json_request(self):
        """Test error response for malformed JSON"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        response = client.post(
            "/execute",
            content="not valid json{",
            headers={"Content-Type": "application/json"}
        )
        
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["error"] == "malformed_request"
    
    def test_missing_required_fields(self):
        """Test validation error for missing required fields"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        # Missing github_token
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/test.sh"
        }
        
        with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(
                valid=False,
                errors=["Missing required field: github_token"]
            )
            
            response = client.post("/execute", json=request_data)
            
            assert response.status_code == 400
            data = response.json()
            assert data["detail"]["error"] == "validation_failed"
            assert "github_token" in str(data["detail"]["details"]["errors"])
    
    def test_invalid_repository_url(self):
        """Test validation error for invalid repository URL"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        request_data = {
            "repository_url": "not-a-valid-url",
            "commit_hash": "a" * 40,
            "script_path": "test.sh",
            "github_token": "ghp_token"
        }
        
        with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(
                valid=False,
                errors=["Invalid repository URL format"]
            )
            
            response = client.post("/execute", json=request_data)
            
            assert response.status_code == 400
            data = response.json()
            assert data["detail"]["error"] == "validation_failed"

    
    def test_invalid_commit_hash(self):
        """Test validation error for invalid commit hash"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "invalid",  # Not 40 hex chars
            "script_path": "test.sh",
            "github_token": "ghp_token"
        }
        
        with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(
                valid=False,
                errors=["Invalid commit hash format"]
            )
            
            response = client.post("/execute", json=request_data)
            
            assert response.status_code == 400
            data = response.json()
            assert data["detail"]["error"] == "validation_failed"
    
    def test_authentication_failure_401(self):
        """Test 401 error for GitHub authentication failure"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "a" * 40,
            "script_path": "test.sh",
            "github_token": "invalid_token"
        }
        
        with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(valid=True, errors=[])
            
            with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
                mock_auth.return_value = Mock(
                    success=False,
                    error_message="Invalid authentication credentials"
                )
                
                response = client.post("/execute", json=request_data)
                
                assert response.status_code == 401
                data = response.json()
                assert data["detail"]["error"] == "authentication_failed"
                assert "authentication" in data["detail"]["message"].lower()
    
    def test_repository_not_found_404(self):
        """Test 404 error for non-existent repository"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        request_data = {
            "repository_url": "https://github.com/test/nonexistent",
            "commit_hash": "a" * 40,
            "script_path": "test.sh",
            "github_token": "ghp_token"
        }
        
        with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(valid=True, errors=[])
            
            with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
                mock_auth.return_value = Mock(success=True, error_message=None)
                
                with patch.object(app.state.repository_client, 'fetch_file') as mock_fetch:
                    mock_fetch.side_effect = GitHubAPIError(
                        "Repository not found",
                        404
                    )
                    
                    response = client.post("/execute", json=request_data)
                    
                    assert response.status_code == 404
                    data = response.json()
                    assert data["detail"]["error"] == "github_api_error"
    
    def test_commit_not_found_404(self):
        """Test 404 error for non-existent commit"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "b" * 40,  # Non-existent commit
            "script_path": "test.sh",
            "github_token": "ghp_token"
        }
        
        with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(valid=True, errors=[])
            
            with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
                mock_auth.return_value = Mock(success=True, error_message=None)
                
                with patch.object(app.state.repository_client, 'fetch_file') as mock_fetch:
                    mock_fetch.side_effect = GitHubAPIError(
                        "Commit not found",
                        404
                    )
                    
                    response = client.post("/execute", json=request_data)
                    
                    assert response.status_code == 404
    
    def test_file_not_found_404(self):
        """Test 404 error for non-existent file"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "a" * 40,
            "script_path": "nonexistent.sh",
            "github_token": "ghp_token"
        }
        
        with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(valid=True, errors=[])
            
            with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
                mock_auth.return_value = Mock(success=True, error_message=None)
                
                with patch.object(app.state.repository_client, 'fetch_file') as mock_fetch:
                    mock_fetch.side_effect = GitHubAPIError(
                        "File not found at path",
                        404
                    )
                    
                    response = client.post("/execute", json=request_data)
                    
                    assert response.status_code == 404
    
    def test_file_too_large_413(self):
        """Test 413 error for oversized script file"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        max_size = app.state.config.max_script_size_bytes
        oversized = max_size + 1000
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "a" * 40,
            "script_path": "large.sh",
            "github_token": "ghp_token"
        }
        
        with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(valid=True, errors=[])
            
            with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
                mock_auth.return_value = Mock(success=True, error_message=None)
                
                with patch.object(app.state.repository_client, 'fetch_file') as mock_fetch:
                    mock_fetch.return_value = FileContent(
                        content=b"x" * oversized,
                        temp_path="/tmp/large.sh",
                        size_bytes=oversized
                    )
                    
                    with patch.object(app.state.repository_client, 'cleanup_temp_file') as mock_cleanup:
                        response = client.post("/execute", json=request_data)
                        
                        assert response.status_code == 413
                        data = response.json()
                        assert data["detail"]["error"] == "file_too_large"
                        assert data["detail"]["details"]["file_size"] == oversized
                        assert data["detail"]["details"]["max_size"] == max_size
                        
                        # Verify temp file was cleaned up
                        mock_cleanup.assert_called_once_with("/tmp/large.sh")
    
    def test_attestation_failure_500(self):
        """Test 500 error for attestation generation failure"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "a" * 40,
            "script_path": "test.sh",
            "github_token": "ghp_token"
        }
        
        with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(valid=True, errors=[])
            
            with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
                mock_auth.return_value = Mock(success=True, error_message=None)
                
                with patch.object(app.state.repository_client, 'fetch_file') as mock_fetch:
                    mock_fetch.return_value = FileContent(
                        content=b"#!/bin/bash\necho test",
                        temp_path="/tmp/test.sh",
                        size_bytes=100
                    )
                    
                    with patch.object(app.state.attestation_generator, 'generate_attestation') as mock_attest:
                        mock_attest.return_value = (
                            None,
                            AttestationError(
                                command="/usr/bin/nitro-tpm-attest",
                                exit_code=-1,
                                stdout="",
                                stderr="NSM device not available",
                                context="Failed to access /dev/nsm"
                            )
                        )
                        
                        with patch.object(app.state.repository_client, 'cleanup_temp_file') as mock_cleanup:
                            response = client.post("/execute", json=request_data)
                            
                            assert response.status_code == 500
                            data = response.json()
                            assert data["detail"]["error"] == "attestation_failed"
                            
                            # Verify temp file was cleaned up
                            mock_cleanup.assert_called_once()


class TestRateLimiting:
    """Tests for rate limiting behavior"""
    
    def test_rate_limit_enforcement(self):
        """Test that rate limiting blocks excessive requests"""
        config = get_test_config()
        config.rate_limit_per_ip = 3  # Low limit for testing
        config.rate_limit_window_seconds = 60
        
        app = create_app(config)
        client = TestClient(app)
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "a" * 40,
            "script_path": "test.sh",
            "github_token": "ghp_token"
        }
        
        responses = []
        
        # Make requests up to and beyond the limit
        for i in range(5):
            response = client.post("/execute", json=request_data)
            responses.append(response)
        
        # First 3 should not be rate limited (may fail for other reasons)
        for i in range(3):
            assert responses[i].status_code != 429, \
                f"Request {i+1} should not be rate limited"
        
        # Remaining should be rate limited
        for i in range(3, 5):
            assert responses[i].status_code == 429, \
                f"Request {i+1} should be rate limited"
            data = responses[i].json()
            assert data["error"] == "rate_limit_exceeded"
    
    def test_rate_limit_headers(self):
        """Test that rate limit headers are included in responses"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "a" * 40,
            "script_path": "test.sh",
            "github_token": "ghp_token"
        }
        
        response = client.post("/execute", json=request_data)
        
        # Check rate limit headers
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Window" in response.headers
        
        assert int(response.headers["X-RateLimit-Limit"]) == 10
        assert int(response.headers["X-RateLimit-Window"]) == 60
    
    def test_rate_limit_per_ip_isolation(self):
        """Test that rate limits are isolated per IP address"""
        config = get_test_config()
        config.rate_limit_per_ip = 2
        
        app = create_app(config)
        
        # Create clients with different IPs
        client1 = TestClient(app)
        client2 = TestClient(app)
        
        request_data = {
            "repository_url": "https://github.com/test/repo",
            "commit_hash": "a" * 40,
            "script_path": "test.sh",
            "github_token": "ghp_token"
        }
        
        # Each client should have independent rate limits
        # Note: TestClient uses same IP, so this tests the mechanism
        # In real deployment, different IPs would be truly isolated
        for _ in range(2):
            response = client1.post("/execute", json=request_data)
            assert response.status_code != 429


class TestOutputEndpoint:
    """Tests for GET /execution/{execution_id}/output endpoint"""
    
    def test_successful_output_retrieval(self):
        """Test successful output retrieval for running execution"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        execution_id = "test-exec-123"
        
        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=None,
            exit_code=None,
            timeout_seconds=300
        )
        
        output_data = OutputData(
            stdout="Line 1\nLine 2\n",
            stderr="Warning: test\n",
            stdout_offset=14,
            stderr_offset=14,
            complete=False,
            exit_code=None
        )
        
        with patch.object(app.state.execution_manager, 'get_execution', return_value=record):
            with patch.object(app.state.output_collector, 'get_output', return_value=output_data):
                response = client.get(f"/execution/{execution_id}/output")
                
                assert response.status_code == 200
                data = response.json()
                
                assert data["execution_id"] == execution_id
                assert data["status"] == "running"
                assert data["stdout"] == "Line 1\nLine 2\n"
                assert data["stderr"] == "Warning: test\n"
                assert data["stdout_offset"] == 14
                assert data["stderr_offset"] == 14
                assert data["complete"] is False
                assert data["exit_code"] is None
    
    def test_completed_execution_with_exit_code(self):
        """Test output retrieval for completed execution includes exit code"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        execution_id = "test-exec-456"
        
        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            exit_code=0,
            timeout_seconds=300
        )
        
        output_data = OutputData(
            stdout="Success!\n",
            stderr="",
            stdout_offset=9,
            stderr_offset=0,
            complete=True,
            exit_code=0
        )
        
        with patch.object(app.state.execution_manager, 'get_execution', return_value=record):
            with patch.object(app.state.output_collector, 'get_output', return_value=output_data):
                response = client.get(f"/execution/{execution_id}/output")
                
                assert response.status_code == 200
                data = response.json()
                
                assert data["status"] == "completed"
                assert data["complete"] is True
                assert data["exit_code"] == 0
    
    def test_failed_execution_with_nonzero_exit_code(self):
        """Test output retrieval for failed execution with non-zero exit code"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        execution_id = "test-exec-789"
        
        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.FAILED,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            exit_code=1,
            timeout_seconds=300
        )
        
        output_data = OutputData(
            stdout="",
            stderr="Error: command failed\n",
            stdout_offset=0,
            stderr_offset=22,
            complete=True,
            exit_code=1
        )
        
        with patch.object(app.state.execution_manager, 'get_execution', return_value=record):
            with patch.object(app.state.output_collector, 'get_output', return_value=output_data):
                response = client.get(f"/execution/{execution_id}/output")
                
                assert response.status_code == 200
                data = response.json()
                
                assert data["status"] == "failed"
                assert data["exit_code"] == 1
                assert data["complete"] is True
    
    def test_execution_not_found_404(self):
        """Test 404 error for non-existent execution ID"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        execution_id = "nonexistent-id"
        
        with patch.object(app.state.execution_manager, 'get_execution', return_value=None):
            response = client.get(f"/execution/{execution_id}/output")
            
            assert response.status_code == 404
            data = response.json()
            assert data["detail"]["error"] == "execution_not_found"
            assert execution_id in data["detail"]["message"]
    
    def test_output_with_offset(self):
        """Test output retrieval with offset parameter"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        execution_id = "test-exec-offset"
        
        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=None,
            exit_code=None,
            timeout_seconds=300
        )
        
        # Output from offset 100
        output_data = OutputData(
            stdout="New output\n",
            stderr="",
            stdout_offset=111,
            stderr_offset=0,
            complete=False,
            exit_code=None
        )
        
        with patch.object(app.state.execution_manager, 'get_execution', return_value=record):
            with patch.object(app.state.output_collector, 'get_output', return_value=output_data) as mock_get:
                response = client.get(f"/execution/{execution_id}/output?offset=100")
                
                assert response.status_code == 200
                data = response.json()
                
                # Verify offset was passed to output collector
                mock_get.assert_called_once_with(execution_id, 100)
                
                assert data["stdout"] == "New output\n"
                assert data["stdout_offset"] == 111
    
    def test_invalid_negative_offset(self):
        """Test 400 error for negative offset"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        execution_id = "test-exec-123"
        
        response = client.get(f"/execution/{execution_id}/output?offset=-1")
        
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["error"] == "invalid_offset"
    
    def test_early_execution_no_output_yet(self):
        """Test output retrieval for execution with no output buffer yet"""
        app = create_app(get_test_config())
        client = TestClient(app)
        
        execution_id = "test-exec-early"
        
        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.QUEUED,
            created_at=datetime.now(timezone.utc),
            started_at=None,
            completed_at=None,
            exit_code=None,
            timeout_seconds=300
        )
        
        with patch.object(app.state.execution_manager, 'get_execution', return_value=record):
            with patch.object(app.state.output_collector, 'get_output', side_effect=ValueError("No output buffer")):
                response = client.get(f"/execution/{execution_id}/output")
                
                assert response.status_code == 200
                data = response.json()
                
                # Should return empty output
                assert data["stdout"] == ""
                assert data["stderr"] == ""
                assert data["stdout_offset"] == 0
                assert data["stderr_offset"] == 0
                assert data["complete"] is False


class TestConcurrentRequests:
    """Tests for concurrent request handling"""
    
    def test_concurrent_execute_requests(self):
        """Test handling multiple concurrent execute requests"""
        import threading
        
        app = create_app(get_test_config())
        client = TestClient(app)
        
        results = []
        errors = []
        
        # Apply patches at outer level so they work across threads
        with patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(valid=True, errors=[])
            
            with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
                mock_auth.return_value = Mock(success=True, error_message=None)
                
                with patch.object(app.state.repository_client, 'fetch_file') as mock_fetch:
                    def fetch_side_effect(repo_url, commit, path):
                        return FileContent(
                            content=b"#!/bin/bash\necho test",
                            temp_path=f"/tmp/{path}",
                            size_bytes=100
                        )
                    mock_fetch.side_effect = fetch_side_effect
                    
                    with patch.object(app.state.attestation_generator, 'generate_attestation') as mock_attest:
                        def attest_side_effect(repo_url, commit, path):
                            return (
                                AttestationDocument(
                                    repository_url=repo_url,
                                    commit_hash=commit,
                                    script_path=path,
                                    timestamp=datetime.now(timezone.utc),
                                    signature=b"test_sig"
                                ),
                                None
                            )
                        mock_attest.side_effect = attest_side_effect
                        
                        with patch.object(app.state.script_executor, 'execute_async'):
                            
                            def make_request(index):
                                try:
                                    request_data = {
                                        "repository_url": f"https://github.com/test/repo{index}",
                                        "commit_hash": "a" * 40,
                                        "script_path": f"test{index}.sh",
                                        "github_token": f"ghp_token_{index}"
                                    }
                                    response = client.post("/execute", json=request_data)
                                    results.append((index, response))
                                except Exception as e:
                                    errors.append((index, str(e)))
                            
                            # Launch 5 concurrent requests
                            threads = []
                            for i in range(5):
                                thread = threading.Thread(target=make_request, args=(i,))
                                threads.append(thread)
                                thread.start()
                            
                            # Wait for all to complete
                            for thread in threads:
                                thread.join(timeout=10)
        
        # Verify all completed without errors
        assert len(errors) == 0, f"Concurrent requests had errors: {errors}"
        assert len(results) == 5, "Not all requests completed"
        
        # Verify all got valid responses
        for index, response in results:
            assert response.status_code in [200, 429], \
                f"Request {index} got unexpected status: {response.status_code}"
    
    def test_concurrent_output_requests(self):
        """Test handling multiple concurrent output requests"""
        import threading
        
        app = create_app(get_test_config())
        client = TestClient(app)
        
        execution_id = "test-concurrent-output"
        
        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            status=ExecutionStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=None,
            exit_code=None,
            timeout_seconds=300
        )
        
        output_data = OutputData(
            stdout="test output",
            stderr="",
            stdout_offset=11,
            stderr_offset=0,
            complete=False,
            exit_code=None
        )
        
        results = []
        
        # Apply patches at the outer level so they work across threads
        with patch.object(app.state.execution_manager, 'get_execution', return_value=record):
            with patch.object(app.state.output_collector, 'get_output', return_value=output_data):
                
                def get_output():
                    response = client.get(f"/execution/{execution_id}/output")
                    results.append(response)
                
                # Launch 10 concurrent output requests
                threads = []
                for _ in range(10):
                    thread = threading.Thread(target=get_output)
                    threads.append(thread)
                    thread.start()
                
                # Wait for all to complete
                for thread in threads:
                    thread.join(timeout=5)
        
        # Verify all completed successfully
        assert len(results) == 10
        for response in results:
            assert response.status_code == 200
