"""
Property-based tests for Container Image Digest Verification.

These tests validate that the GHA_Server verifies the pulled image digest
matches the expected digest at startup, and handles digest-pinned references.

**Validates: Requirements 34.7, 34.8, 34.9, 34.10**
"""

from unittest.mock import MagicMock

import docker.errors
import pytest
from hypothesis import given, strategies as st, settings

from src.script_executor import ScriptExecutor


# Strategy for generating valid SHA-256 hex digest strings (64 hex chars)
sha256_hex_st = st.from_regex(r"[0-9a-f]{64}", fullmatch=True)

# Strategy for generating valid Docker image names
image_name_st = st.from_regex(
    r"[a-z][a-z0-9_\-]{2,30}(/[a-z][a-z0-9_\-]{2,30})?(:[a-z0-9][a-z0-9.\-]{0,20})?",
    fullmatch=True,
)


def _make_mock_image(digest_hex: str, image_name: str = "myorg/myimage") -> MagicMock:
    """Create a mock Docker image with the given digest in RepoDigests."""
    mock_image = MagicMock()
    mock_image.attrs = {
        "Size": 150_000_000,
        "RepoDigests": [f"{image_name}@sha256:{digest_hex}"],
    }
    mock_image.id = f"sha256:{digest_hex}"
    return mock_image


@given(digest_hex=sha256_hex_st, image_name=image_name_st)
@settings(max_examples=50)
def test_digest_match_allows_pull(digest_hex: str, image_name: str):
    """
    Property 156: Container Image Digest Verification — matching digest

    For any configured CONTAINER_IMAGE_DIGEST that matches the pulled image's
    digest, pull_container_image should succeed without raising.

    **Validates: Requirements 34.7, 34.8**
    """
    expected_digest = f"sha256:{digest_hex}"
    mock_image = _make_mock_image(digest_hex, image_name)

    mock_client = MagicMock()
    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = ScriptExecutor(
        docker_client=mock_client,
        container_image=image_name,
        container_image_digest=expected_digest,
    )

    # Should not raise
    executor.pull_container_image()

    mock_client.images.pull.assert_called_once_with(image_name)


@given(
    digest_hex=sha256_hex_st,
    other_digest_hex=sha256_hex_st,
    image_name=image_name_st,
)
@settings(max_examples=50)
def test_digest_mismatch_raises_error(
    digest_hex: str, other_digest_hex: str, image_name: str
):
    """
    Property 156: Container Image Digest Verification — mismatched digest

    For any configured CONTAINER_IMAGE_DIGEST that does NOT match the pulled
    image's digest, pull_container_image should raise RuntimeError.

    **Validates: Requirements 34.8, 34.9**
    """
    # Ensure digests are actually different
    if digest_hex == other_digest_hex:
        return

    expected_digest = f"sha256:{digest_hex}"
    mock_image = _make_mock_image(other_digest_hex, image_name)

    mock_client = MagicMock()
    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = ScriptExecutor(
        docker_client=mock_client,
        container_image=image_name,
        container_image_digest=expected_digest,
    )

    with pytest.raises(RuntimeError, match="digest mismatch"):
        executor.pull_container_image()


@given(digest_hex=sha256_hex_st, image_name=image_name_st)
@settings(max_examples=50)
def test_no_digest_configured_raises_error(
    digest_hex: str, image_name: str
):
    """
    Property 156: Container Image Digest Verification — no digest configured

    When no CONTAINER_IMAGE_DIGEST is configured (None) and the image reference
    does not contain @sha256:, pull_container_image should raise RuntimeError
    because digest verification is now mandatory.

    **Validates: Requirements 34.7, 34.8**
    """
    mock_image = _make_mock_image(digest_hex, image_name)

    mock_client = MagicMock()
    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = ScriptExecutor(
        docker_client=mock_client,
        container_image=image_name,
        container_image_digest=None,
    )

    # Should raise because digest is mandatory
    with pytest.raises(RuntimeError, match="no digest configured"):
        executor.pull_container_image()


@given(digest_hex=sha256_hex_st, image_name=image_name_st)
@settings(max_examples=50)
def test_digest_pinned_reference_extracts_and_verifies(
    digest_hex: str, image_name: str
):
    """
    Property 156: Container Image Digest Verification — digest-pinned reference

    When the image reference contains @sha256:..., the expected digest should
    be extracted from the reference and verified against the pulled image.

    **Validates: Requirements 34.10**
    """
    pinned_ref = f"{image_name}@sha256:{digest_hex}"
    mock_image = _make_mock_image(digest_hex, image_name)

    mock_client = MagicMock()
    mock_client.images.get.side_effect = [
        docker.errors.ImageNotFound("not found"),
        mock_image,
    ]
    mock_client.images.pull.return_value = mock_image

    executor = ScriptExecutor(
        docker_client=mock_client,
        container_image=pinned_ref,
        container_image_digest=None,  # No explicit config — extracted from ref
    )

    # Should not raise because digest matches
    executor.pull_container_image()


# ---------------------------------------------------------------------------
# Property 170: Container Image Digest Default Configuration
# ---------------------------------------------------------------------------

def _parse_env_file(path: str) -> dict:
    """Parse a dotenv-style file and return a dict of key -> value (or None for empty)."""
    entries = {}
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            # Skip blank lines and comment-only lines
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key, _, value = stripped.partition("=")
                entries[key.strip()] = value.strip()
    return entries


def test_env_example_contains_container_image_digest_entry():
    """
    Property 170: Container Image Digest Default Configuration

    Parse .env.example to verify it contains a CONTAINER_IMAGE_DIGEST entry
    (even if empty), confirming operators are prompted to configure digest
    pinning.

    **Validates: Requirements 34.7**
    """
    import os

    # Locate .env.example relative to this test file (repo root)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_example_path = os.path.join(repo_root, ".env.example")

    entries = _parse_env_file(env_example_path)

    assert "CONTAINER_IMAGE_DIGEST" in entries, (
        ".env.example must contain a CONTAINER_IMAGE_DIGEST entry so operators "
        "are prompted to configure digest pinning"
    )


def test_kiwi_env_file_contains_container_image_digest_entry():
    """
    Property 170: Container Image Digest Default Configuration

    Parse kiwi-descriptions/root/etc/github-actions-remote-executor/env to
    verify it contains a CONTAINER_IMAGE_DIGEST entry (even if empty),
    confirming operators are prompted to configure digest pinning in the
    baked AMI environment file.

    **Validates: Requirements 34.7**
    """
    import os

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    kiwi_env_path = os.path.join(
        repo_root,
        "kiwi-descriptions",
        "root",
        "etc",
        "github-actions-remote-executor",
        "env",
    )

    entries = _parse_env_file(kiwi_env_path)

    assert "CONTAINER_IMAGE_DIGEST" in entries, (
        "kiwi-descriptions/root/etc/github-actions-remote-executor/env must "
        "contain a CONTAINER_IMAGE_DIGEST entry so operators are prompted to "
        "configure digest pinning in the AMI image"
    )
