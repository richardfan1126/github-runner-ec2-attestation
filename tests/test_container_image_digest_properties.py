"""
Property-based tests for Container Image Digest configuration.

The runtime digest *verification* has moved from a registry pull to the offline
baked-sidecar path (see test_baked_image_load.py). What remains relevant here is
that the expected manifest digest (CONTAINER_IMAGE_DIGEST) — the canonical anchor
the baked sidecar is verified against — is still present in the shipped env files.

**Validates: Requirements 34.7**
"""

import os


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
    (even if empty), confirming operators are prompted to configure the
    manifest-digest anchor the baked sidecar is verified against.

    **Validates: Requirements 34.7**
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_example_path = os.path.join(repo_root, ".env.example")

    entries = _parse_env_file(env_example_path)

    assert "CONTAINER_IMAGE_DIGEST" in entries, (
        ".env.example must contain a CONTAINER_IMAGE_DIGEST entry so operators "
        "are prompted to configure the manifest-digest anchor"
    )


def test_kiwi_env_file_contains_container_image_digest_entry():
    """
    Property 170: Container Image Digest Default Configuration

    Parse kiwi-descriptions/root/etc/github-actions-remote-executor/env to
    verify it contains a CONTAINER_IMAGE_DIGEST entry, confirming the baked AMI
    environment file carries the manifest-digest anchor the offline verify step
    compares the baked sidecar against.

    **Validates: Requirements 34.7**
    """
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
        "contain a CONTAINER_IMAGE_DIGEST entry so the baked AMI carries the "
        "manifest-digest anchor"
    )
