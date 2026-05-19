"""Unit tests for script_env forwarding

Tests that script_env is correctly extracted, sanitized, and forwarded
from the /execute endpoint through ScriptExecutor to Docker container creation.

Requirements: 52.1-52.6
"""
import os
import tempfile
import time
import base64
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.script_executor import ScriptExecutor, CONTAINER_NAME_PREFIX
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus, AttestationDocument, CloneResult, OIDCValidationResult
from src.server import create_app
from src.config import ServerConfig
from tests.mock_docker import create_mock_docker_client
from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_execute_request,
    decrypt_execute_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_test_script(temp_dir: str, script_content: str = "echo ok\n") -> str:
    """Helper to create a test script file."""
    script_path = os.path.join(temp_dir, "test_script.sh")
    with open(script_path, 'w') as f:
        f.write("#!/bin/bash\n")
        f.write(script_content)
    os.chmod(script_path, 0o755)
    return script_path


def wait_for_completion(manager: ExecutionManager, execution_id: str, max_wait: float = 5.0) -> bool:
    """Helper to wait for execution to reach a terminal state."""
    start = time.time()
    while time.time() - start < max_wait:
        record = manager.get_execution(execution_id)
        if record and record.status in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
        ):
            return True
        time.sleep(0.1)
    return False


def _make_executor(mock_client, manager, collector, temp_dir, **kwargs):
    """Convenience factory for ScriptExecutor with sensible defaults."""
    defaults = dict(
        docker_client=mock_client,
        execution_manager=manager,
        output_collector=collector,
        temp_storage_path=temp_dir,
        container_image="test-image:latest",
        memory_limit="512m",
        cpu_limit=1.0,
    )
    defaults.update(kwargs)
    return ScriptExecutor(**defaults)


VALID_OIDC_RESULT = OIDCValidationResult(
    valid=True,
    status_code=200,
    error_message=None,
    claims={"repository": "owner/repo", "iss": "https://token.actions.githubusercontent.com", "aud": "https://example.com", "sha": "a" * 40},
)


def get_test_config():
    """Create test configuration."""
    return ServerConfig(
        port=8000,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=10,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/test",
        output_retention_hours=24,
        tpm_attest_path="/usr/bin/nitro-tpm-attest",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )


# ===========================================================================
# 1. ScriptExecutor: script_env passed through to Docker container creation
# ===========================================================================

class TestScriptExecutorEnvForwarding:
    """Validates: Requirements 52.3, 52.4"""

    def test_script_env_passed_to_container_create(self):
        """script_env dict is passed as environment parameter to containers.create()."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            record = manager.create_execution(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="test.sh",
                timeout_seconds=5,
            )
            create_test_script(temp_dir)

            env = {"GITHUB_TOKEN": "ghp_abc123", "GITHUB_RUN_ID": "12345"}
            executor.execute_async(record.execution_id, temp_dir, "test_script.sh", script_env=env)
            assert wait_for_completion(manager, record.execution_id)

            calls = mock_client.containers._creation_calls
            assert len(calls) == 1
            assert calls[0]["environment"] == env

    def test_no_script_env_gives_empty_environment(self):
        """When script_env is not provided, container gets empty environment dict."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            record = manager.create_execution(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="test.sh",
                timeout_seconds=5,
            )
            create_test_script(temp_dir)

            executor.execute_async(record.execution_id, temp_dir, "test_script.sh")
            assert wait_for_completion(manager, record.execution_id)

            calls = mock_client.containers._creation_calls
            assert len(calls) == 1
            assert calls[0]["environment"] == {}

    def test_none_script_env_gives_empty_environment(self):
        """When script_env is explicitly None, container gets empty environment dict."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            record = manager.create_execution(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="test.sh",
                timeout_seconds=5,
            )
            create_test_script(temp_dir)

            executor.execute_async(record.execution_id, temp_dir, "test_script.sh", script_env=None)
            assert wait_for_completion(manager, record.execution_id)

            calls = mock_client.containers._creation_calls
            assert len(calls) == 1
            assert calls[0]["environment"] == {}

    def test_empty_script_env_gives_empty_environment(self):
        """When script_env is an empty dict, container gets empty environment dict."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            record = manager.create_execution(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="test.sh",
                timeout_seconds=5,
            )
            create_test_script(temp_dir)

            executor.execute_async(record.execution_id, temp_dir, "test_script.sh", script_env={})
            assert wait_for_completion(manager, record.execution_id)

            calls = mock_client.containers._creation_calls
            assert len(calls) == 1
            assert calls[0]["environment"] == {}


# ===========================================================================
# 2. Server endpoint: script_env extraction and sanitization
# ===========================================================================

class TestServerScriptEnvForwarding:
    """Validates: Requirements 52.1, 52.2, 52.5, 52.6"""

    def _make_request(self, request_data):
        """Helper to make an encrypted /execute request and return (response, execute_async_mock)."""
        ctx = EncryptionTestContext()
        app = create_app(get_test_config(), encryption_manager=ctx.encryption_manager)
        client = TestClient(app)

        with patch.object(app.state.request_validator, 'validate_oidc_token_from_body', return_value=VALID_OIDC_RESULT), \
             patch.object(app.state.request_validator, 'validate_execution_request') as mock_validate:
            mock_validate.return_value = Mock(valid=True, errors=[])

            with patch.object(app.state.repository_client, 'authenticate') as mock_auth:
                mock_auth.return_value = Mock(success=True, error_message=None)

                with patch.object(app.state.repository_client, 'clone_repo') as mock_clone:
                    mock_clone.return_value = CloneResult(
                        clone_path="/tmp/test_clone",
                        script_path=""
                    )

                    with patch.object(app.state.repository_client, 'validate_script_exists', return_value=True):
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

                            with patch('os.path.getsize', return_value=100):
                                with patch.object(app.state.script_executor, 'execute_async') as mock_exec:
                                    body = make_encrypted_execute_request(request_data, ctx)
                                    response = client.post("/execute", json=body)
                                    return response, mock_exec, ctx

    def test_script_env_with_valid_string_pairs(self):
        """script_env with valid string key-value pairs is forwarded to executor."""
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
            "script_env": {
                "GITHUB_TOKEN": "ghp_abc123",
                "GITHUB_RUN_ID": "12345",
                "MY_VAR": "hello world",
            },
        }

        response, mock_exec, ctx = self._make_request(request_data)
        assert response.status_code == 200

        # Verify execute_async was called with script_env
        mock_exec.assert_called_once()
        call_kwargs = mock_exec.call_args
        assert call_kwargs.kwargs.get("script_env") == {
            "GITHUB_TOKEN": "ghp_abc123",
            "GITHUB_RUN_ID": "12345",
            "MY_VAR": "hello world",
        }

    def test_script_env_absent_does_not_fail(self):
        """Omitting script_env does not cause request validation to fail."""
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
        }

        response, mock_exec, ctx = self._make_request(request_data)
        assert response.status_code == 200

        # Verify execute_async was called with empty script_env
        mock_exec.assert_called_once()
        call_kwargs = mock_exec.call_args
        assert call_kwargs.kwargs.get("script_env") == {}

    def test_script_env_with_non_string_values_sanitized(self):
        """Non-string values in script_env are dropped during sanitization."""
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
            "script_env": {
                "VALID_KEY": "valid_value",
                "INT_VALUE": 42,
                "BOOL_VALUE": True,
                "LIST_VALUE": [1, 2, 3],
                "NONE_VALUE": None,
            },
        }

        response, mock_exec, ctx = self._make_request(request_data)
        assert response.status_code == 200

        # Only the entry with both string key and string value should survive
        mock_exec.assert_called_once()
        call_kwargs = mock_exec.call_args
        sanitized_env = call_kwargs.kwargs.get("script_env")
        assert sanitized_env == {"VALID_KEY": "valid_value"}

    def test_script_env_empty_dict(self):
        """Empty script_env dict results in empty environment."""
        request_data = {
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "a" * 40,
            "script_path": "scripts/test.sh",
            "github_token": "ghp_test_token_123",
            "oidc_token": "valid.oidc.token",
            "script_env": {},
        }

        response, mock_exec, ctx = self._make_request(request_data)
        assert response.status_code == 200

        mock_exec.assert_called_once()
        call_kwargs = mock_exec.call_args
        assert call_kwargs.kwargs.get("script_env") == {}
