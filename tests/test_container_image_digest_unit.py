"""Unit tests for container image digest pinning.

Tests cover digest matching, mismatch, no-digest-configured, and
digest-pinned image reference parsing.

Requirements: 34.7, 34.8, 34.9, 34.10
"""

from unittest.mock import MagicMock

import docker.errors
import pytest

from src.script_executor import ScriptExecutor


DIGEST_A = "sha256:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb"
DIGEST_B = "sha256:1122334455667788990011223344556677889900aabbccddeeff001122334455"
HEX_A = "aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb"
HEX_B = "1122334455667788990011223344556677889900aabbccddeeff001122334455"


def _mock_image(digest_hex: str, repo_name: str = "myorg/myimage"):
    """Create a mock image with RepoDigests containing the given digest."""
    img = MagicMock()
    img.attrs = {
        "Size": 100_000_000,
        "RepoDigests": [f"{repo_name}@sha256:{digest_hex}"],
    }
    img.id = f"sha256:{digest_hex}"
    return img


def _make_executor(mock_client, image="myorg/myimage:latest", digest=None):
    return ScriptExecutor(
        docker_client=mock_client,
        container_image=image,
        container_image_digest=digest,
    )


# ---------------------------------------------------------------------------
# Matching digest → startup succeeds
# ---------------------------------------------------------------------------

def test_matching_digest_succeeds():
    """When configured digest matches the pulled image, pull succeeds."""
    mock_client = MagicMock()
    mock_image = _mock_image(HEX_A, "myorg/myimage")

    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = _make_executor(mock_client, digest=DIGEST_A)
    executor.pull_container_image()  # Should not raise

    mock_client.images.pull.assert_called_once()


# ---------------------------------------------------------------------------
# Matching digest when image already present locally
# ---------------------------------------------------------------------------

def test_matching_digest_already_present():
    """When image is already present and digest matches, pull is skipped."""
    mock_client = MagicMock()
    mock_image = _mock_image(HEX_A, "myorg/myimage")
    mock_client.images.get.return_value = mock_image

    executor = _make_executor(mock_client, digest=DIGEST_A)
    executor.pull_container_image()  # Should not raise

    mock_client.images.pull.assert_not_called()


# ---------------------------------------------------------------------------
# Mismatched digest → startup fails with descriptive error
# ---------------------------------------------------------------------------

def test_mismatched_digest_raises_error():
    """When configured digest does not match, RuntimeError is raised."""
    mock_client = MagicMock()
    mock_image = _mock_image(HEX_B, "myorg/myimage")

    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = _make_executor(mock_client, digest=DIGEST_A)

    with pytest.raises(RuntimeError, match="digest mismatch") as exc_info:
        executor.pull_container_image()

    error_msg = str(exc_info.value)
    assert DIGEST_A in error_msg  # expected
    assert DIGEST_B in error_msg  # actual


# ---------------------------------------------------------------------------
# No digest configured → startup fails (Requirements 34.7, 34.8)
# ---------------------------------------------------------------------------

def test_no_digest_configured_raises_error():
    """When no digest is configured and image has no @sha256:, pull raises RuntimeError."""
    mock_client = MagicMock()
    mock_image = _mock_image(HEX_A, "myorg/myimage")

    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = _make_executor(mock_client, digest=None)
    
    with pytest.raises(RuntimeError, match="no digest configured"):
        executor.pull_container_image()


# ---------------------------------------------------------------------------
# Digest-pinned image reference parsing
# ---------------------------------------------------------------------------

def test_digest_pinned_reference_matching():
    """Digest-pinned reference (image@sha256:...) extracts and verifies digest."""
    pinned_ref = f"myorg/myimage@sha256:{HEX_A}"
    mock_client = MagicMock()
    mock_image = _mock_image(HEX_A, "myorg/myimage")

    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = _make_executor(mock_client, image=pinned_ref, digest=None)
    executor.pull_container_image()  # Should not raise


def test_digest_pinned_reference_mismatch():
    """Digest-pinned reference with wrong digest raises RuntimeError."""
    pinned_ref = f"myorg/myimage@sha256:{HEX_A}"
    mock_client = MagicMock()
    mock_image = _mock_image(HEX_B, "myorg/myimage")

    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = _make_executor(mock_client, image=pinned_ref, digest=None)

    with pytest.raises(RuntimeError, match="digest mismatch"):
        executor.pull_container_image()


# ---------------------------------------------------------------------------
# Explicit config digest takes precedence over pinned reference
# ---------------------------------------------------------------------------

def test_explicit_digest_overrides_pinned_reference():
    """When both config digest and pinned ref are present, config digest is used."""
    pinned_ref = f"myorg/myimage@sha256:{HEX_B}"
    mock_client = MagicMock()
    mock_image = _mock_image(HEX_A, "myorg/myimage")

    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    # Config digest matches image, pinned ref does not — config wins
    executor = _make_executor(mock_client, image=pinned_ref, digest=DIGEST_A)
    executor.pull_container_image()  # Should not raise


# ---------------------------------------------------------------------------
# Fallback to image.id when no RepoDigests
# ---------------------------------------------------------------------------

def test_fallback_to_image_id_when_no_repo_digests():
    """When RepoDigests is empty, falls back to image.id for verification."""
    mock_client = MagicMock()
    mock_image = MagicMock()
    mock_image.attrs = {"Size": 100_000_000, "RepoDigests": []}
    mock_image.id = DIGEST_A

    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = _make_executor(mock_client, digest=DIGEST_A)
    executor.pull_container_image()  # Should not raise


# ---------------------------------------------------------------------------
# Additional tests for mandatory digest pinning (Task 176.5)
# ---------------------------------------------------------------------------

def test_startup_succeeds_with_digest_pinned_image_reference():
    """Server startup succeeds when CONTAINER_IMAGE contains @sha256: even if CONTAINER_IMAGE_DIGEST is empty."""
    pinned_ref = f"myorg/myimage@sha256:{HEX_A}"
    mock_client = MagicMock()
    mock_image = _mock_image(HEX_A, "myorg/myimage")

    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    # No explicit digest config, but image reference has @sha256:
    executor = _make_executor(mock_client, image=pinned_ref, digest=None)
    executor.pull_container_image()  # Should not raise


def test_startup_succeeds_with_explicit_digest():
    """Server startup succeeds when CONTAINER_IMAGE_DIGEST is explicitly set."""
    mock_client = MagicMock()
    mock_image = _mock_image(HEX_A, "myorg/myimage")

    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = _make_executor(mock_client, image="myorg/myimage:latest", digest=DIGEST_A)
    executor.pull_container_image()  # Should not raise


def test_pull_always_verifies_digest_no_skip_path():
    """Verify that pull_container_image always verifies digest (no skip path exists)."""
    mock_client = MagicMock()
    mock_image = _mock_image(HEX_A, "myorg/myimage")

    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    # Even with digest=None and no @sha256: in image, verification is attempted
    executor = _make_executor(mock_client, image="myorg/myimage:latest", digest=None)
    
    with pytest.raises(RuntimeError, match="no digest configured"):
        executor.pull_container_image()
    
    # This confirms there's no code path that skips verification
