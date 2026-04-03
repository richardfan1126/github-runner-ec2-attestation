"""
Property-based tests for Container Image pull at server startup.

These tests validate that the GHA_Server pulls the configured Container_Image
from the container registry at startup, verifies it is available in the local
Docker image store, and handles failure cases correctly.
"""

from unittest.mock import MagicMock, PropertyMock, call

import docker.errors
import pytest
from hypothesis import given, strategies as st, settings

from src.script_executor import ScriptExecutor


# Strategy for generating valid Docker image names
image_name_st = st.from_regex(r"[a-z][a-z0-9_\-]{2,30}(/[a-z][a-z0-9_\-]{2,30})?(:[a-z0-9][a-z0-9.\-]{0,20})?", fullmatch=True)


@given(image_name=image_name_st)
@settings(max_examples=50)
def test_container_image_pull_at_server_startup(image_name: str):
    """
    Property 118: Container Image Pull at Server Startup

    For any configured Container_Image name, verify the GHA_Server pulls the
    image from the container registry at startup and verifies it is available
    in the local Docker image store before accepting requests.

    Mock the Docker SDK client: images.get() raises ImageNotFound (image not
    present), then images.pull() succeeds, then images.get() succeeds.

    **Validates: Requirements 34.1, 34.2, 34.3**
    """
    mock_client = MagicMock()

    # First images.get() raises ImageNotFound (not present locally)
    # Second images.get() succeeds (available after pull)
    mock_image = MagicMock()
    mock_image.attrs = {"Size": 150_000_000}
    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound(f"Image {image_name} not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = ScriptExecutor(
        docker_client=mock_client,
        container_image=image_name,
    )

    executor.pull_container_image()

    # Verify pull was called with the image name
    mock_client.images.pull.assert_called_once_with(image_name)

    # Verify images.get was called twice: once to check, once to verify after pull
    assert mock_client.images.get.call_count == 2
    mock_client.images.get.assert_any_call(image_name)


@given(image_name=image_name_st)
@settings(max_examples=50)
def test_container_image_pull_failure_halts_startup(image_name: str):
    """
    Property 119: Container Image Pull Failure Halts Startup

    For any Container_Image name that cannot be pulled (network error, image
    not found, authentication failure), verify the GHA_Server fails to start
    with a descriptive error message indicating the image name and failure
    reason.

    Mock the Docker SDK client: images.get() raises ImageNotFound, then
    images.pull() raises an exception.

    **Validates: Requirements 34.4**
    """
    mock_client = MagicMock()

    # images.get() raises ImageNotFound (not present locally)
    mock_client.images.get.side_effect = docker.errors.ImageNotFound(
        f"Image {image_name} not found"
    )

    # images.pull() raises an error (network, auth, not found, etc.)
    mock_client.images.pull.side_effect = docker.errors.ImageNotFound(
        f"pull access denied for {image_name}"
    )

    executor = ScriptExecutor(
        docker_client=mock_client,
        container_image=image_name,
    )

    with pytest.raises(RuntimeError) as exc_info:
        executor.pull_container_image()

    # Error message must include the image name
    assert image_name in str(exc_info.value)


@given(image_name=image_name_st)
@settings(max_examples=50)
def test_container_image_skip_pull_when_already_present(image_name: str):
    """
    Property 120: Container Image Skip Pull When Already Present

    For any Container_Image that is already present in the local Docker image
    store, verify the GHA_Server skips pulling from the registry and uses the
    existing image.

    Mock the Docker SDK client: images.get() succeeds (image already present).
    Verify images.pull() is NOT called.

    **Validates: Requirements 34.5**
    """
    mock_client = MagicMock()

    # images.get() succeeds — image is already present
    mock_image = MagicMock()
    mock_client.images.get.return_value = mock_image

    executor = ScriptExecutor(
        docker_client=mock_client,
        container_image=image_name,
    )

    executor.pull_container_image()

    # Verify images.get was called to check
    mock_client.images.get.assert_called_once_with(image_name)

    # Verify pull was NOT called
    mock_client.images.pull.assert_not_called()
