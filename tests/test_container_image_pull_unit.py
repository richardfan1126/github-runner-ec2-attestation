"""Unit tests for pull_container_image method on ScriptExecutor.

Tests cover the full pull lifecycle, skip-when-present, various failure modes,
logging output, post-pull verification failure, and startup wiring in main.py.

Requirements: 34.1, 34.2, 34.3, 34.4, 34.5, 34.6
"""
import logging
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from src.script_executor import ScriptExecutor


def _make_executor(mock_client, image="myorg/myimage:latest"):
    """Helper to build a ScriptExecutor with a mocked Docker client."""
    return ScriptExecutor(docker_client=mock_client, container_image=image)


# ---------------------------------------------------------------------------
# Successful pull flow: not present → pull → verify
# ---------------------------------------------------------------------------

def test_successful_pull_when_image_not_present():
    """Image not in local store → pull from registry → verify available."""
    mock_client = MagicMock()
    mock_image = MagicMock()
    mock_image.attrs = {"Size": 200_000_000}

    # First get raises ImageNotFound, second get succeeds (post-pull verify)
    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = _make_executor(mock_client)
    executor.pull_container_image()

    mock_client.images.pull.assert_called_once_with("myorg/myimage:latest")
    assert mock_client.images.get.call_count == 2


# ---------------------------------------------------------------------------
# Skip pull when image already present locally
# ---------------------------------------------------------------------------

def test_skip_pull_when_image_already_present():
    """If the image is already in the local store, pull should be skipped."""
    mock_client = MagicMock()
    mock_client.images.get.return_value = MagicMock()

    executor = _make_executor(mock_client)
    executor.pull_container_image()

    mock_client.images.get.assert_called_once_with("myorg/myimage:latest")
    mock_client.images.pull.assert_not_called()


# ---------------------------------------------------------------------------
# Pull failure: image not found in registry
# ---------------------------------------------------------------------------

def test_pull_failure_image_not_found():
    """Pull raises ImageNotFound → RuntimeError with image name."""
    mock_client = MagicMock()
    mock_client.images.get.side_effect = docker.errors.ImageNotFound("not found")
    mock_client.images.pull.side_effect = docker.errors.ImageNotFound(
        "pull access denied for myorg/myimage"
    )

    executor = _make_executor(mock_client)

    with pytest.raises(RuntimeError, match="myorg/myimage:latest"):
        executor.pull_container_image()


# ---------------------------------------------------------------------------
# Pull failure: network error during pull
# ---------------------------------------------------------------------------

def test_pull_failure_network_error():
    """Pull raises APIError (network) → RuntimeError with image name."""
    mock_client = MagicMock()
    mock_client.images.get.side_effect = docker.errors.ImageNotFound("not found")
    mock_client.images.pull.side_effect = docker.errors.APIError(
        "connection refused"
    )

    executor = _make_executor(mock_client)

    with pytest.raises(RuntimeError, match="myorg/myimage:latest"):
        executor.pull_container_image()


# ---------------------------------------------------------------------------
# Pull failure: authentication error
# ---------------------------------------------------------------------------

def test_pull_failure_authentication_error():
    """Pull raises APIError (auth) → RuntimeError with image name."""
    mock_client = MagicMock()
    mock_client.images.get.side_effect = docker.errors.ImageNotFound("not found")
    mock_client.images.pull.side_effect = docker.errors.APIError(
        "unauthorized: authentication required"
    )

    executor = _make_executor(mock_client)

    with pytest.raises(RuntimeError, match="myorg/myimage:latest"):
        executor.pull_container_image()


# ---------------------------------------------------------------------------
# Pull logging: image name, duration, and size are logged
# ---------------------------------------------------------------------------

def test_pull_logs_image_name_duration_and_size(caplog):
    """After a successful pull the log should contain image name, duration, and size."""
    mock_client = MagicMock()
    mock_image = MagicMock()
    mock_image.attrs = {"Size": 150_000_000}  # ~143 MB

    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = _make_executor(mock_client, image="registry.io/app:v2")

    with caplog.at_level(logging.INFO):
        executor.pull_container_image()

    log_text = caplog.text
    assert "registry.io/app:v2" in log_text
    # Duration is logged as e.g. "0.0s"
    assert "s" in log_text
    assert "MB" in log_text


def test_skip_pull_logs_already_present(caplog):
    """When the image is already present, a skip message should be logged."""
    mock_client = MagicMock()
    mock_client.images.get.return_value = MagicMock()

    executor = _make_executor(mock_client, image="myimg:1")

    with caplog.at_level(logging.INFO):
        executor.pull_container_image()

    assert "already present" in caplog.text.lower()
    assert "myimg:1" in caplog.text


# ---------------------------------------------------------------------------
# Verify failure: image pulled but not available after pull
# ---------------------------------------------------------------------------

def test_verify_failure_after_pull():
    """Pull succeeds but post-pull images.get raises ImageNotFound → RuntimeError."""
    mock_client = MagicMock()
    mock_image = MagicMock()
    mock_image.attrs = {"Size": 100_000_000}

    # First get: not found (triggers pull). Second get: still not found.
    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        docker.errors.ImageNotFound("still not found"),
    ]
    mock_client.images.pull.return_value = mock_image

    executor = _make_executor(mock_client)

    with pytest.raises(RuntimeError, match="not available after pull"):
        executor.pull_container_image()


# ---------------------------------------------------------------------------
# Docker client is None → RuntimeError
# ---------------------------------------------------------------------------

def test_pull_raises_when_docker_client_is_none():
    """If Docker client is None, pull_container_image should raise RuntimeError."""
    executor = ScriptExecutor(docker_client=None, container_image="img:1")

    with pytest.raises(RuntimeError, match="Docker client is not available"):
        executor.pull_container_image()


# ---------------------------------------------------------------------------
# API error during initial image check falls through to pull
# ---------------------------------------------------------------------------

def test_api_error_on_initial_check_still_attempts_pull():
    """If images.get raises APIError (not ImageNotFound), pull is still attempted."""
    mock_client = MagicMock()
    mock_image = MagicMock()
    mock_image.attrs = {"Size": 50_000_000}

    mock_client.images.get.side_effect = [
        docker.errors.APIError("daemon error"),
        mock_image,  # post-pull verify succeeds
    ]
    mock_client.images.pull.return_value = mock_image

    executor = _make_executor(mock_client)
    executor.pull_container_image()

    mock_client.images.pull.assert_called_once()


# ---------------------------------------------------------------------------
# Startup wiring: ConfigurationError raised in main.py when pull fails
# ---------------------------------------------------------------------------

@patch("src.main.create_app")
@patch("src.main.docker")
@patch("src.main.load_config")
@patch("src.main.setup_logging")
@patch("src.main.AttestationGenerator")
@patch("src.main.ScriptExecutor")
@patch("os.path.exists", return_value=True)
def test_startup_raises_configuration_error_on_pull_failure(
    mock_exists,
    mock_script_executor_cls,
    mock_attest_gen_cls,
    mock_setup_logging,
    mock_load_config,
    mock_docker_mod,
    mock_create_app,
):
    """main() should return 1 (ConfigurationError) when pull_container_image fails."""
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
    mock_load_config.return_value = config

    # Docker client mock
    mock_docker_client = MagicMock()
    mock_docker_mod.from_env.return_value = mock_docker_client

    # Attestation generator mock
    mock_attest_gen_cls.return_value.verify_tpm_available.return_value = True

    # ScriptExecutor instance whose pull_container_image raises
    mock_executor_instance = mock_script_executor_cls.return_value
    mock_executor_instance.pull_container_image.side_effect = RuntimeError(
        "Container image 'myorg/myimage:latest' not found in registry"
    )

    exit_code = main()

    assert exit_code == 1
    mock_executor_instance.pull_container_image.assert_called_once()
