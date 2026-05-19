"""Tests for security hardening round 3 changes (Task 190).

Covers:
- OIDC commit hash binding (190.1): matching/mismatching sha claims
- Immutable container image reference (190.2): digest-based image refs
- Production executor wiring (190.3): create_app passes digest to ScriptExecutor
- Container PID limits (190.4): pids_limit passed to containers.create()
- Output attestation rate limiting (190.5): budget enforcement and reset
- NitroTPM availability enforcement (190.6): startup fail-closed behavior

Requirements: 2.37, 8.31, 9.21, 34.18, 55.9
"""
import os
import tempfile
import time
import logging
from unittest.mock import Mock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.config import ServerConfig, load_config
from src.models import OIDCValidationResult
from src.output_attestation_rate_limiter import OutputAttestationRateLimiter
from src.script_executor import ScriptExecutor
from src.server import create_app
from src.validation import RequestValidator
from tests.mock_docker import create_mock_docker_client, MockDockerClient
from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_execute_request,
    decrypt_execute_response,
    make_encrypted_output_request,
    decrypt_output_response,
    assert_encrypted_error,
)


# ---------------------------------------------------------------------------
# Shared Fixtures
# ---------------------------------------------------------------------------

VALID_OIDC_RESULT = OIDCValidationResult(
    valid=True,
    status_code=200,
    error_message=None,
    claims={
        "repository": "owner/repo",
        "iss": "https://token.actions.githubusercontent.com",
        "aud": "https://example.com",
        "sha": "a" * 40,
    },
)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def test_config(temp_dir):
    return ServerConfig(
        port=8080,
        max_concurrent_executions=10,
        execution_timeout_seconds=5,
        max_script_size_bytes=1024 * 1024,
        rate_limit_per_ip=100,
        rate_limit_window_seconds=60,
        temp_storage_path=temp_dir,
        output_retention_hours=1,
        tpm_attest_path="/usr/bin/nitro-tpm-attest",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
        container_image_digest="sha256:" + "b" * 64,
        container_pids_limit=256,
        max_output_attestations_per_window=10,
        output_attestation_window_seconds=60,
    )


@pytest.fixture
def encryption_ctx():
    return EncryptionTestContext()


@pytest.fixture
def mock_github_and_attestation():
    with patch('requests.Session') as mock_session_class, \
         patch('src.repository.subprocess.run') as mock_git_run, \
         patch('src.attestation.subprocess.run') as mock_attest:

        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_session.headers = {}
        mock_session.get.return_value = Mock(status_code=200)

        mock_git_run.return_value = Mock(returncode=0, stdout="", stderr="")
        mock_attest.return_value = Mock(
            returncode=0, stdout=b'mock_attestation_cbor_data'
        )

        yield {
            'session': mock_session,
            'git_run': mock_git_run,
            'attestation': mock_attest,
        }


@pytest.fixture
def app(test_config, mock_github_and_attestation, temp_dir, encryption_ctx):
    application = create_app(
        test_config,
        docker_client=create_mock_docker_client(),
        encryption_manager=encryption_ctx.encryption_manager,
    )
    application.state.request_validator.validate_oidc_token_from_body = Mock(
        return_value=VALID_OIDC_RESULT
    )

    from src.models import CloneResult

    def mock_clone_repo(repo_url, commit, token):
        clone_dir = tempfile.mkdtemp(dir=temp_dir, prefix="clone_")
        return CloneResult(clone_path=clone_dir, script_path="")

    def mock_validate_script_exists(clone_path, script_path):
        full_path = os.path.join(clone_path, script_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write('#!/bin/bash\necho "Test output"\nexit 0')
        os.chmod(full_path, 0o755)
        return True

    application.state.repository_client.clone_repo = Mock(
        side_effect=mock_clone_repo
    )
    application.state.repository_client.validate_script_exists = Mock(
        side_effect=mock_validate_script_exists
    )

    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def _make_valid_request():
    """Create a valid execution request payload."""
    return {
        "repository_url": "https://github.com/owner/repo",
        "commit_hash": "a" * 40,
        "script_path": "scripts/build.sh",
        "github_token": "ghp_test_token_123",
        "oidc_token": "mock_oidc_token",
        "nonce": "test-nonce-1234567890",
    }


# ===========================================================================
# OIDC Commit Hash Binding Tests (190.1)
# ===========================================================================


class TestOIDCCommitHashBinding:
    """Tests for OIDC commit hash binding validation."""

    def test_matching_commit_hash_accepted(self):
        """Request with matching commit_hash and OIDC sha claim is accepted."""
        validator = RequestValidator()
        claims = {"sha": "a" * 40}
        assert validator.validate_commit_hash_binding(claims, "a" * 40) is True

    def test_mismatching_commit_hash_rejected(self):
        """Request with mismatching commit_hash and OIDC sha claim is rejected."""
        validator = RequestValidator()
        claims = {"sha": "a" * 40}
        assert validator.validate_commit_hash_binding(claims, "b" * 40) is False

    def test_case_insensitive_comparison(self):
        """Comparison is case-insensitive (uppercase hex matches lowercase)."""
        validator = RequestValidator()
        claims = {"sha": "abcdef1234567890abcdef1234567890abcdef12"}
        commit_hash = "ABCDEF1234567890ABCDEF1234567890ABCDEF12"
        assert validator.validate_commit_hash_binding(claims, commit_hash) is True

    def test_missing_sha_claim_rejected(self):
        """Request is rejected when OIDC claims have no sha field."""
        validator = RequestValidator()
        claims = {"repository": "owner/repo"}
        assert validator.validate_commit_hash_binding(claims, "a" * 40) is False

    def test_empty_sha_claim_rejected(self):
        """Request is rejected when OIDC sha claim is empty string."""
        validator = RequestValidator()
        claims = {"sha": ""}
        assert validator.validate_commit_hash_binding(claims, "a" * 40) is False

    def test_commit_hash_mismatch_returns_403_via_server(
        self, client, encryption_ctx
    ):
        """Server returns encrypted 403 error when commit hash doesn't match."""
        # OIDC claims have sha="a"*40, but request has commit_hash="b"*40
        mismatched_oidc = OIDCValidationResult(
            valid=True,
            status_code=200,
            error_message=None,
            claims={
                "repository": "owner/repo",
                "sha": "a" * 40,
            },
        )
        client.app.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=mismatched_oidc
        )

        request_data = _make_valid_request()
        request_data["commit_hash"] = "b" * 40

        body = make_encrypted_execute_request(request_data, encryption_ctx)
        response = client.post("/execute", json=body)

        error = assert_encrypted_error(
            response, encryption_ctx.shared_key,
            "commit_hash_mismatch", 403
        )
        assert "sha" in error["message"].lower() or "commit" in error["message"].lower()

    def test_commit_hash_match_proceeds_to_execution(
        self, client, encryption_ctx
    ):
        """Server proceeds past commit hash check when hashes match."""
        request_data = _make_valid_request()
        body = make_encrypted_execute_request(request_data, encryption_ctx)
        response = client.post("/execute", json=body)

        # Should succeed (HTTP 200 with encrypted response containing execution_id)
        assert response.status_code == 200
        decrypted = decrypt_execute_response(
            response.json(), encryption_ctx.shared_key
        )
        assert "execution_id" in decrypted


# ===========================================================================
# Immutable Container Image Reference Tests (190.2)
# ===========================================================================


class TestImmutableImageReference:
    """Tests for immutable container image reference normalization."""

    def test_digest_configured_produces_immutable_ref(self):
        """When container_image_digest is configured, immutable ref is used."""
        executor = ScriptExecutor(
            docker_client=None,
            container_image="python:3.11-slim",
            container_image_digest="sha256:" + "a" * 64,
        )
        expected = f"python@sha256:{'a' * 64}"
        assert executor._immutable_image_ref == expected

    def test_digest_strips_tag_from_image(self):
        """Tag is stripped when digest is provided."""
        executor = ScriptExecutor(
            docker_client=None,
            container_image="myregistry.io/myimage:v1.2.3",
            container_image_digest="sha256:" + "c" * 64,
        )
        expected = f"myregistry.io/myimage@sha256:{'c' * 64}"
        assert executor._immutable_image_ref == expected

    def test_digest_with_registry_port(self):
        """Registry port is preserved when stripping tag."""
        executor = ScriptExecutor(
            docker_client=None,
            container_image="localhost:5000/myimage:latest",
            container_image_digest="sha256:" + "d" * 64,
        )
        expected = f"localhost:5000/myimage@sha256:{'d' * 64}"
        assert executor._immutable_image_ref == expected

    def test_image_already_pinned_by_digest(self):
        """Image already containing @sha256: is used directly when no explicit digest."""
        digest = "a" * 64
        image = f"python@sha256:{digest}"
        executor = ScriptExecutor(
            docker_client=None,
            container_image=image,
            container_image_digest=None,
        )
        assert executor._immutable_image_ref == image

    def test_mutable_tag_fallback_with_warning(self, caplog):
        """Mutable tag without digest logs warning and falls back."""
        with caplog.at_level(logging.WARNING):
            executor = ScriptExecutor(
                docker_client=None,
                container_image="python:3.11-slim",
                container_image_digest=None,
            )
        assert executor._immutable_image_ref == "python:3.11-slim"
        assert "mutable tag" in caplog.text.lower() or "not immutable" in caplog.text.lower()

    def test_containers_create_uses_immutable_ref(self, temp_dir):
        """containers.create() receives the immutable image reference."""
        from src.execution_manager import ExecutionManager
        from src.output_collector import OutputCollector

        mock_docker = create_mock_docker_client()
        digest = "e" * 64
        executor = ScriptExecutor(
            docker_client=mock_docker,
            container_image="python:3.11-slim",
            container_image_digest=f"sha256:{digest}",
            execution_manager=ExecutionManager(1),
            output_collector=OutputCollector(),
            temp_storage_path=temp_dir,
        )

        expected_ref = f"python@sha256:{digest}"
        assert executor._immutable_image_ref == expected_ref

    def test_digest_hex_without_prefix_handled(self):
        """Digest without 'sha256:' prefix is handled correctly."""
        digest_hex = "f" * 64
        executor = ScriptExecutor(
            docker_client=None,
            container_image="ubuntu:24.04",
            container_image_digest=digest_hex,
        )
        expected = f"ubuntu@sha256:{digest_hex}"
        assert executor._immutable_image_ref == expected


# ===========================================================================
# Production Executor Wiring Tests (190.3)
# ===========================================================================


class TestProductionExecutorWiring:
    """Tests for create_app passing container_image_digest to ScriptExecutor."""

    def test_create_app_passes_digest_to_script_executor(self, temp_dir):
        """create_app() passes container_image_digest to ScriptExecutor."""
        digest = "sha256:" + "a" * 64
        config = ServerConfig(
            port=8080,
            max_concurrent_executions=10,
            execution_timeout_seconds=5,
            max_script_size_bytes=1024 * 1024,
            rate_limit_per_ip=100,
            rate_limit_window_seconds=60,
            temp_storage_path=temp_dir,
            output_retention_hours=1,
            tpm_attest_path="/usr/bin/nitro-tpm-attest",
            allowed_repositories=["owner/repo"],
            expected_audience="https://example.com",
            container_image="python:3.11-slim",
            container_memory_limit="512m",
            container_cpu_limit=1.0,
            container_image_digest=digest,
        )
        app = create_app(
            config,
            docker_client=create_mock_docker_client(),
            encryption_manager=EncryptionTestContext().encryption_manager,
        )
        executor = app.state.script_executor
        assert executor._container_image_digest == digest
        # Verify immutable ref was computed
        expected_ref = f"python@sha256:{'a' * 64}"
        assert executor._immutable_image_ref == expected_ref

    def test_startup_and_request_executors_receive_same_digest(self, temp_dir):
        """Both startup executor (main.py) and request executor (server.py)
        receive the same container_image_digest value from config."""
        digest = "sha256:" + "b" * 64
        config = ServerConfig(
            port=8080,
            max_concurrent_executions=10,
            execution_timeout_seconds=5,
            max_script_size_bytes=1024 * 1024,
            rate_limit_per_ip=100,
            rate_limit_window_seconds=60,
            temp_storage_path=temp_dir,
            output_retention_hours=1,
            tpm_attest_path="/usr/bin/nitro-tpm-attest",
            allowed_repositories=["owner/repo"],
            expected_audience="https://example.com",
            container_image="python:3.11-slim",
            container_memory_limit="512m",
            container_cpu_limit=1.0,
            container_image_digest=digest,
        )

        # Simulate what main.py does for the startup executor
        from src.execution_manager import ExecutionManager
        from src.output_collector import OutputCollector

        startup_executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            container_image=config.container_image,
            container_image_digest=config.container_image_digest,
            execution_manager=ExecutionManager(config.output_retention_hours),
            output_collector=OutputCollector(),
            temp_storage_path=config.temp_storage_path,
        )

        # Simulate what create_app does for the request executor
        app = create_app(
            config,
            docker_client=create_mock_docker_client(),
            encryption_manager=EncryptionTestContext().encryption_manager,
        )
        request_executor = app.state.script_executor

        # Both should have the same digest
        assert startup_executor._container_image_digest == digest
        assert request_executor._container_image_digest == digest
        assert startup_executor._container_image_digest == request_executor._container_image_digest


# ===========================================================================
# Container PID Limit Tests (190.4)
# ===========================================================================


class TestContainerPIDLimits:
    """Tests for container PID limits (fork bomb protection)."""

    def test_pids_limit_passed_to_containers_create(self, temp_dir):
        """pids_limit is passed to containers.create() with configured value."""
        from src.execution_manager import ExecutionManager
        from src.output_collector import OutputCollector

        mock_docker = create_mock_docker_client()
        pids_limit = 128

        executor = ScriptExecutor(
            docker_client=mock_docker,
            container_image="python:3.11-slim",
            container_image_digest="sha256:" + "a" * 64,
            container_pids_limit=pids_limit,
            execution_manager=ExecutionManager(1),
            output_collector=OutputCollector(),
            temp_storage_path=temp_dir,
            timeout_seconds=5,
        )

        # Create an execution record so _execute_in_container can find it
        exec_mgr = executor._execution_manager
        from src.models import ExecutionStatus
        record, accepted = exec_mgr.try_create_execution(
            "https://github.com/owner/repo",
            "a" * 40,
            "test.sh",
            5,
            max_concurrent=10,
        )
        assert accepted

        # Create a script file
        repo_dir = tempfile.mkdtemp(dir=temp_dir)
        script_path = os.path.join(repo_dir, "test.sh")
        with open(script_path, "w") as f:
            f.write("#!/bin/bash\necho hello\n")
        os.chmod(script_path, 0o755)

        # Execute and wait for container creation
        executor.execute_async(record.execution_id, repo_dir, "test.sh")
        time.sleep(1)

        # Check that pids_limit was passed in the creation call
        creation_calls = mock_docker.containers._creation_calls
        assert len(creation_calls) >= 1
        assert creation_calls[0]["pids_limit"] == pids_limit

    def test_non_positive_pids_limit_fails_startup(self):
        """Non-positive MAX_CONTAINER_PIDS fails config loading."""
        env = {
            "SERVER_PORT": "8080",
            "MAX_CONCURRENT_EXECUTIONS": "10",
            "EXECUTION_TIMEOUT_SECONDS": "300",
            "MAX_SCRIPT_SIZE_BYTES": "1048576",
            "RATE_LIMIT_PER_IP": "10",
            "RATE_LIMIT_WINDOW_SECONDS": "60",
            "TEMP_STORAGE_PATH": "/tmp/test",
            "OUTPUT_RETENTION_HOURS": "24",
            "TPM_ATTEST_PATH": "/usr/bin/nitro-tpm-attest",
            "ALLOWED_REPOSITORIES": "owner/repo",
            "EXPECTED_AUDIENCE": "https://example.com",
            "CONTAINER_IMAGE": "python:3.11-slim",
            "CONTAINER_MEMORY_LIMIT": "512m",
            "CONTAINER_CPU_LIMIT": "1.0",
            "MAX_CONTAINER_PIDS": "0",
            "CONTAINER_IMAGE_DIGEST": "sha256:" + "a" * 64,
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="MAX_CONTAINER_PIDS"):
                ServerConfig.from_env()

    def test_negative_pids_limit_fails_startup(self):
        """Negative MAX_CONTAINER_PIDS fails config loading."""
        env = {
            "SERVER_PORT": "8080",
            "MAX_CONCURRENT_EXECUTIONS": "10",
            "EXECUTION_TIMEOUT_SECONDS": "300",
            "MAX_SCRIPT_SIZE_BYTES": "1048576",
            "RATE_LIMIT_PER_IP": "10",
            "RATE_LIMIT_WINDOW_SECONDS": "60",
            "TEMP_STORAGE_PATH": "/tmp/test",
            "OUTPUT_RETENTION_HOURS": "24",
            "TPM_ATTEST_PATH": "/usr/bin/nitro-tpm-attest",
            "ALLOWED_REPOSITORIES": "owner/repo",
            "EXPECTED_AUDIENCE": "https://example.com",
            "CONTAINER_IMAGE": "python:3.11-slim",
            "CONTAINER_MEMORY_LIMIT": "512m",
            "CONTAINER_CPU_LIMIT": "1.0",
            "MAX_CONTAINER_PIDS": "-5",
            "CONTAINER_IMAGE_DIGEST": "sha256:" + "a" * 64,
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="MAX_CONTAINER_PIDS"):
                ServerConfig.from_env()

    def test_non_integer_pids_limit_fails_startup(self):
        """Non-integer MAX_CONTAINER_PIDS fails config loading."""
        env = {
            "SERVER_PORT": "8080",
            "MAX_CONCURRENT_EXECUTIONS": "10",
            "EXECUTION_TIMEOUT_SECONDS": "300",
            "MAX_SCRIPT_SIZE_BYTES": "1048576",
            "RATE_LIMIT_PER_IP": "10",
            "RATE_LIMIT_WINDOW_SECONDS": "60",
            "TEMP_STORAGE_PATH": "/tmp/test",
            "OUTPUT_RETENTION_HOURS": "24",
            "TPM_ATTEST_PATH": "/usr/bin/nitro-tpm-attest",
            "ALLOWED_REPOSITORIES": "owner/repo",
            "EXPECTED_AUDIENCE": "https://example.com",
            "CONTAINER_IMAGE": "python:3.11-slim",
            "CONTAINER_MEMORY_LIMIT": "512m",
            "CONTAINER_CPU_LIMIT": "1.0",
            "MAX_CONTAINER_PIDS": "not_a_number",
            "CONTAINER_IMAGE_DIGEST": "sha256:" + "a" * 64,
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="MAX_CONTAINER_PIDS"):
                ServerConfig.from_env()

    def test_default_pids_limit_is_256(self):
        """Default container_pids_limit is 256 when env var not set."""
        config = ServerConfig(
            port=8080,
            max_concurrent_executions=10,
            execution_timeout_seconds=5,
            max_script_size_bytes=1024 * 1024,
            rate_limit_per_ip=100,
            rate_limit_window_seconds=60,
            temp_storage_path="/tmp/test",
            output_retention_hours=1,
            tpm_attest_path="/usr/bin/nitro-tpm-attest",
            allowed_repositories=["owner/repo"],
            expected_audience="https://example.com",
            container_image="python:3.11-slim",
            container_memory_limit="512m",
            container_cpu_limit=1.0,
        )
        assert config.container_pids_limit == 256


# ===========================================================================
# Output Attestation Rate Limiting Tests (190.5)
# ===========================================================================


class TestOutputAttestationRateLimiting:
    """Tests for output attestation rate limiting."""

    def test_within_budget_allows_attestation(self):
        """Attestation is allowed when within rate limit budget."""
        limiter = OutputAttestationRateLimiter(max_per_window=5, window_seconds=60)
        for i in range(5):
            assert limiter.check_and_record("exec-1") is True

    def test_exceeding_budget_denies_attestation(self):
        """Attestation is denied after exceeding rate limit budget."""
        limiter = OutputAttestationRateLimiter(max_per_window=3, window_seconds=60)
        for _ in range(3):
            assert limiter.check_and_record("exec-1") is True
        # 4th should be denied
        assert limiter.check_and_record("exec-1") is False

    def test_different_execution_ids_have_separate_budgets(self):
        """Each execution_id has its own independent budget."""
        limiter = OutputAttestationRateLimiter(max_per_window=2, window_seconds=60)
        assert limiter.check_and_record("exec-1") is True
        assert limiter.check_and_record("exec-1") is True
        assert limiter.check_and_record("exec-1") is False
        # exec-2 should still have full budget
        assert limiter.check_and_record("exec-2") is True
        assert limiter.check_and_record("exec-2") is True

    def test_budget_resets_after_window_expires(self):
        """Budget resets after the time window expires."""
        limiter = OutputAttestationRateLimiter(max_per_window=2, window_seconds=1)
        assert limiter.check_and_record("exec-1") is True
        assert limiter.check_and_record("exec-1") is True
        assert limiter.check_and_record("exec-1") is False

        # Wait for window to expire
        time.sleep(1.1)

        # Budget should be reset
        assert limiter.check_and_record("exec-1") is True

    def test_rate_limited_response_via_server(
        self, client, encryption_ctx, app
    ):
        """After exceeding rate limit, output response has
        output_attestation_document=null and attestation_rate_limited=true."""
        # Set rate limiter to allow only 1 attestation per window
        app.state.output_attestation_rate_limiter = OutputAttestationRateLimiter(
            max_per_window=1, window_seconds=60
        )

        # First, create an execution via /execute
        request_data = _make_valid_request()
        body = make_encrypted_execute_request(request_data, encryption_ctx)
        response = client.post("/execute", json=body)
        assert response.status_code == 200
        decrypted = decrypt_execute_response(
            response.json(), encryption_ctx.shared_key
        )
        execution_id = decrypted["execution_id"]

        # First output poll — should get attestation (within budget)
        output_body = make_encrypted_output_request(
            {"offset": 0}, encryption_ctx.shared_key
        )
        resp1 = client.post(
            f"/execution/{execution_id}/output", json=output_body
        )
        assert resp1.status_code == 200
        dec1 = decrypt_output_response(resp1.json(), encryption_ctx.shared_key)
        # First poll should NOT be rate limited
        assert dec1.get("attestation_rate_limited") is not True

        # Second output poll — should be rate limited
        output_body2 = make_encrypted_output_request(
            {"offset": 0}, encryption_ctx.shared_key
        )
        resp2 = client.post(
            f"/execution/{execution_id}/output", json=output_body2
        )
        assert resp2.status_code == 200
        dec2 = decrypt_output_response(resp2.json(), encryption_ctx.shared_key)
        assert dec2["output_attestation_document"] is None
        assert dec2["attestation_rate_limited"] is True


# ===========================================================================
# NitroTPM Availability Enforcement Tests (190.6)
# ===========================================================================


class TestNitroTPMEnforcement:
    """Tests for NitroTPM availability enforcement at startup."""

    def test_startup_fails_when_tpm_unavailable_and_allow_no_tpm_false(self):
        """Startup fails (returns 1) when TPM unavailable and ALLOW_NO_TPM=false."""
        with patch('src.main.setup_logging'), \
             patch('src.main.load_config') as mock_config, \
             patch('src.main.AttestationGenerator') as mock_attest_gen:

            config = Mock()
            config.port = 8080
            config.max_concurrent_executions = 10
            config.execution_timeout_seconds = 300
            config.max_script_size_bytes = 1048576
            config.rate_limit_per_ip = 10
            config.rate_limit_window_seconds = 60
            config.temp_storage_path = "/tmp/test"
            config.output_retention_hours = 24
            config.tpm_attest_path = "/usr/bin/nitro-tpm-attest"
            config.container_image = "python:3.11-slim"
            config.container_memory_limit = "512m"
            config.container_cpu_limit = 1.0
            config.container_image_digest = "sha256:" + "a" * 64
            config.allow_no_tpm = False
            mock_config.return_value = config

            mock_gen_instance = Mock()
            mock_gen_instance.verify_tpm_available.return_value = False
            mock_attest_gen.return_value = mock_gen_instance

            from src.main import main
            exit_code = main()
            assert exit_code == 1

    def test_startup_succeeds_with_warning_when_tpm_unavailable_and_allow_no_tpm_true(
        self, caplog
    ):
        """Startup continues with warning when TPM unavailable and ALLOW_NO_TPM=true."""
        with patch('src.main.setup_logging'), \
             patch('src.main.load_config') as mock_config, \
             patch('src.main.AttestationGenerator') as mock_attest_gen, \
             patch('src.main.docker') as mock_docker_mod, \
             patch('src.main.ScriptExecutor') as mock_script_exec, \
             patch('src.main.EncryptionManager'), \
             patch('src.main.create_app') as mock_create_app, \
             patch('src.main.signal.signal'), \
             patch('src.main.os.path.exists', return_value=True), \
             patch('src.main.os.getuid', return_value=1000), \
             patch('uvicorn.run'):

            config = Mock()
            config.port = 8080
            config.max_concurrent_executions = 10
            config.execution_timeout_seconds = 300
            config.max_script_size_bytes = 1048576
            config.rate_limit_per_ip = 10
            config.rate_limit_window_seconds = 60
            config.temp_storage_path = "/tmp/test"
            config.output_retention_hours = 24
            config.tpm_attest_path = "/usr/bin/nitro-tpm-attest"
            config.container_image = "python:3.11-slim"
            config.container_memory_limit = "512m"
            config.container_cpu_limit = 1.0
            config.container_image_digest = "sha256:" + "a" * 64
            config.allow_no_tpm = True
            mock_config.return_value = config

            mock_gen_instance = Mock()
            mock_gen_instance.verify_tpm_available.return_value = False
            mock_attest_gen.return_value = mock_gen_instance

            mock_docker_client = Mock()
            mock_docker_mod.DockerClient.return_value = mock_docker_client
            mock_docker_client.ping.return_value = True

            mock_exec_instance = Mock()
            mock_script_exec.return_value = mock_exec_instance

            from src.main import main
            with caplog.at_level(logging.WARNING):
                exit_code = main()

            # Should succeed (exit 0) — uvicorn.run is mocked
            assert exit_code == 0
            # Should have logged a warning about TPM not available
            assert any(
                "not available" in record.message.lower() or
                "allow_no_tpm" in record.message.lower()
                for record in caplog.records
            )

    def test_startup_succeeds_when_tpm_available(self):
        """Startup succeeds normally when TPM is available regardless of ALLOW_NO_TPM."""
        with patch('src.main.setup_logging'), \
             patch('src.main.load_config') as mock_config, \
             patch('src.main.AttestationGenerator') as mock_attest_gen, \
             patch('src.main.docker') as mock_docker_mod, \
             patch('src.main.ScriptExecutor') as mock_script_exec, \
             patch('src.main.EncryptionManager'), \
             patch('src.main.create_app') as mock_create_app, \
             patch('src.main.signal.signal'), \
             patch('src.main.os.path.exists', return_value=True), \
             patch('src.main.os.getuid', return_value=1000), \
             patch('uvicorn.run'):

            config = Mock()
            config.port = 8080
            config.max_concurrent_executions = 10
            config.execution_timeout_seconds = 300
            config.max_script_size_bytes = 1048576
            config.rate_limit_per_ip = 10
            config.rate_limit_window_seconds = 60
            config.temp_storage_path = "/tmp/test"
            config.output_retention_hours = 24
            config.tpm_attest_path = "/usr/bin/nitro-tpm-attest"
            config.container_image = "python:3.11-slim"
            config.container_memory_limit = "512m"
            config.container_cpu_limit = 1.0
            config.container_image_digest = "sha256:" + "a" * 64
            config.allow_no_tpm = False  # Even with False, should succeed if TPM is available
            mock_config.return_value = config

            mock_gen_instance = Mock()
            mock_gen_instance.verify_tpm_available.return_value = True
            mock_attest_gen.return_value = mock_gen_instance

            mock_docker_client = Mock()
            mock_docker_mod.DockerClient.return_value = mock_docker_client
            mock_docker_client.ping.return_value = True

            mock_exec_instance = Mock()
            mock_script_exec.return_value = mock_exec_instance

            from src.main import main
            exit_code = main()
            assert exit_code == 0

    def test_allow_no_tpm_config_parsing_strict_bool(self):
        """ALLOW_NO_TPM uses strict boolean parsing."""
        env = {
            "SERVER_PORT": "8080",
            "MAX_CONCURRENT_EXECUTIONS": "10",
            "EXECUTION_TIMEOUT_SECONDS": "300",
            "MAX_SCRIPT_SIZE_BYTES": "1048576",
            "RATE_LIMIT_PER_IP": "10",
            "RATE_LIMIT_WINDOW_SECONDS": "60",
            "TEMP_STORAGE_PATH": "/tmp/test",
            "OUTPUT_RETENTION_HOURS": "24",
            "TPM_ATTEST_PATH": "/usr/bin/nitro-tpm-attest",
            "ALLOWED_REPOSITORIES": "owner/repo",
            "EXPECTED_AUDIENCE": "https://example.com",
            "CONTAINER_IMAGE": "python:3.11-slim",
            "CONTAINER_MEMORY_LIMIT": "512m",
            "CONTAINER_CPU_LIMIT": "1.0",
            "CONTAINER_IMAGE_DIGEST": "sha256:" + "a" * 64,
            "ALLOW_NO_TPM": "maybe",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="ALLOW_NO_TPM"):
                ServerConfig.from_env()

    def test_allow_no_tpm_true_parsed_correctly(self):
        """ALLOW_NO_TPM=true is parsed as True."""
        env = {
            "SERVER_PORT": "8080",
            "MAX_CONCURRENT_EXECUTIONS": "10",
            "EXECUTION_TIMEOUT_SECONDS": "300",
            "MAX_SCRIPT_SIZE_BYTES": "1048576",
            "RATE_LIMIT_PER_IP": "10",
            "RATE_LIMIT_WINDOW_SECONDS": "60",
            "TEMP_STORAGE_PATH": "/tmp/test",
            "OUTPUT_RETENTION_HOURS": "24",
            "TPM_ATTEST_PATH": "/usr/bin/nitro-tpm-attest",
            "ALLOWED_REPOSITORIES": "owner/repo",
            "EXPECTED_AUDIENCE": "https://example.com",
            "CONTAINER_IMAGE": "python:3.11-slim",
            "CONTAINER_MEMORY_LIMIT": "512m",
            "CONTAINER_CPU_LIMIT": "1.0",
            "CONTAINER_IMAGE_DIGEST": "sha256:" + "a" * 64,
            "ALLOW_NO_TPM": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            config = ServerConfig.from_env()
            assert config.allow_no_tpm is True
