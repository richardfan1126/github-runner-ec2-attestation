"""Unit tests for rootless Docker socket connection in ScriptExecutor.

Tests verify that ScriptExecutor connects to the rootless Docker socket
at /run/user/{uid}/docker.sock when no docker_client is explicitly provided.

Requirements: 33.2, 48.1
"""
import os
from unittest.mock import patch, MagicMock

import pytest

from src.script_executor import ScriptExecutor, _UNSET


class TestRootlessDockerSocketConnection:
    """Tests for rootless Docker socket initialization."""

    @patch("src.script_executor.docker.DockerClient")
    def test_default_docker_client_uses_rootless_socket(self, mock_docker_client_cls):
        """When docker_client is not provided (default _UNSET), ScriptExecutor
        creates a DockerClient using the rootless socket path."""
        mock_client_instance = MagicMock()
        mock_docker_client_cls.return_value = mock_client_instance

        executor = ScriptExecutor()

        uid = os.getuid()
        expected_base_url = f"unix:///run/user/{uid}/docker.sock"
        mock_docker_client_cls.assert_called_once_with(base_url=expected_base_url)
        assert executor._docker_client is mock_client_instance

    @patch("src.script_executor.docker.DockerClient")
    def test_rootless_socket_path_uses_current_uid(self, mock_docker_client_cls):
        """The rootless socket path is constructed using os.getuid()."""
        mock_docker_client_cls.return_value = MagicMock()

        executor = ScriptExecutor()

        uid = os.getuid()
        call_args = mock_docker_client_cls.call_args
        assert call_args is not None
        assert f"/run/user/{uid}/docker.sock" in call_args.kwargs["base_url"]

    def test_explicit_docker_client_skips_default_creation(self):
        """When docker_client is explicitly provided, no default client is created."""
        custom_client = MagicMock()

        executor = ScriptExecutor(docker_client=custom_client)

        assert executor._docker_client is custom_client

    def test_explicit_none_docker_client_means_unavailable(self):
        """When docker_client=None is passed explicitly, Docker is unavailable."""
        executor = ScriptExecutor(docker_client=None)

        assert executor._docker_client is None

    @patch("src.script_executor.docker.DockerClient")
    def test_rootless_socket_base_url_format(self, mock_docker_client_cls):
        """The base_url follows the unix:///run/user/{uid}/docker.sock format."""
        mock_docker_client_cls.return_value = MagicMock()

        executor = ScriptExecutor()

        uid = os.getuid()
        expected_url = f"unix:///run/user/{uid}/docker.sock"
        mock_docker_client_cls.assert_called_once_with(base_url=expected_url)


class TestMainStartupRootlessSocket:
    """Tests for main.py startup using rootless Docker socket."""

    @patch("src.main.create_app")
    @patch("src.main.docker")
    @patch("src.main.load_config")
    @patch("src.main.setup_logging")
    @patch("src.main.AttestationGenerator")
    @patch("src.main.ScriptExecutor")
    @patch("os.path.exists", return_value=True)
    def test_main_creates_docker_client_with_rootless_socket(
        self,
        mock_exists,
        mock_script_executor_cls,
        mock_attest_gen_cls,
        mock_setup_logging,
        mock_load_config,
        mock_docker_mod,
        mock_create_app,
    ):
        """main() creates DockerClient with rootless socket base_url."""
        import sys
        from unittest.mock import MagicMock as _MagicMock

        # Pre-inject a mock uvicorn module so the local import inside main() succeeds
        mock_uvicorn = _MagicMock()
        sys.modules.setdefault("uvicorn", mock_uvicorn)

        try:
            from src.main import main

            # Minimal config stub
            config = MagicMock()
            config.port = 8080
            config.max_concurrent_executions = 5
            config.execution_timeout_seconds = 60
            config.max_script_size_bytes = 1_000_000
            config.rate_limit_per_ip = 10
            config.rate_limit_window_seconds = 60
            config.temp_storage_path = "/tmp/test"
            config.output_retention_hours = 1
            config.tpm_attest_path = "/usr/bin/nitro-tpm-attest"
            config.container_image = "myorg/myimage:latest"
            config.container_memory_limit = "512m"
            config.container_cpu_limit = 1.0
            config.enable_gpu = False
            mock_load_config.return_value = config

            # Docker client mock
            mock_docker_client = MagicMock()
            mock_docker_mod.DockerClient.return_value = mock_docker_client

            # Attestation generator mock
            mock_attest_gen_cls.return_value.verify_tpm_available.return_value = True

            # ScriptExecutor mock - successful pull
            mock_executor_instance = mock_script_executor_cls.return_value
            mock_executor_instance.pull_container_image.return_value = None
            mock_executor_instance.cleanup_dangling_containers.return_value = None

            exit_code = main()

            # Verify DockerClient was called with rootless socket path
            uid = os.getuid()
            expected_base_url = f"unix:///run/user/{uid}/docker.sock"
            mock_docker_mod.DockerClient.assert_called_once_with(
                base_url=expected_base_url
            )
            assert exit_code == 0
        finally:
            # Clean up injected module only if we added it
            if sys.modules.get("uvicorn") is mock_uvicorn:
                del sys.modules["uvicorn"]
