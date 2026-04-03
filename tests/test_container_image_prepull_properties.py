"""
Property-based tests for Container Image pre-pull in the KIWI build process.

These tests validate that the build pipeline correctly pulls a container image,
exports it as a tar archive, copies it into the KIWI build context, and loads
it during config.sh — ensuring the image is available in the local Docker store
at runtime.
"""

import re
from pathlib import Path

import pytest


def test_container_image_prepull_round_trip():
    """
    Property 118: Container Image Pre-Pull Round-Trip

    For any configured Container_Image name, the build process pulls the image,
    exports it as a tar, copies it into the KIWI build context, and loads it
    in config.sh — resulting in the image being available in the local Docker
    store.

    Verifies by parsing build-kiwi-image.sh for `docker pull` and `docker save`
    commands referencing the CONTAINER_IMAGE variable, and parsing config.sh for
    `docker load -i /tmp/kiwi-build/container-image.tar`.

    **Validates: Requirements 34.1, 34.2, 34.3, 34.4, 34.5**
    """
    build_script = Path(".github/scripts/build-kiwi-image.sh")
    config_script = Path("kiwi-descriptions/config.sh")

    assert build_script.exists(), "build-kiwi-image.sh must exist"
    assert config_script.exists(), "config.sh must exist"

    build_content = build_script.read_text()
    config_content = config_script.read_text()

    # 1. build-kiwi-image.sh reads CONTAINER_IMAGE from the env file
    assert "CONTAINER_IMAGE" in build_content, (
        "build-kiwi-image.sh must reference CONTAINER_IMAGE variable"
    )

    # 2. build-kiwi-image.sh pulls the container image
    assert re.search(r'docker\s+pull\s+.*CONTAINER_IMAGE', build_content), (
        "build-kiwi-image.sh must contain a 'docker pull' command "
        "referencing the CONTAINER_IMAGE variable"
    )

    # 3. build-kiwi-image.sh exports the image as a tar archive via docker save
    assert re.search(r'docker\s+save\s+.*CONTAINER_IMAGE', build_content), (
        "build-kiwi-image.sh must contain a 'docker save' command "
        "referencing the CONTAINER_IMAGE variable"
    )

    # 4. The tar is placed at the expected path inside the build context
    assert "container-image.tar" in build_content, (
        "build-kiwi-image.sh must reference container-image.tar"
    )

    # 5. config.sh loads the image from the tar archive
    assert re.search(
        r'docker\s+load\s+-i\s+/tmp/kiwi-build/container-image\.tar',
        config_content,
    ), (
        "config.sh must contain 'docker load -i /tmp/kiwi-build/container-image.tar'"
    )


def test_container_image_pull_failure_halts_build():
    """
    Property 119: Container Image Pull Failure Halts Build

    If `docker pull` fails in build-kiwi-image.sh, the script must exit with
    a non-zero exit code and a descriptive error message.

    Parses build-kiwi-image.sh for error handling around the `docker pull`
    command (e.g., `if ! docker pull` pattern with `exit 1`).

    **Validates: Requirements 34.6**
    """
    build_script = Path(".github/scripts/build-kiwi-image.sh")
    assert build_script.exists(), "build-kiwi-image.sh must exist"

    content = build_script.read_text()

    # The script must use an error-handling pattern around docker pull.
    # Expected pattern: `if ! docker pull ...; then ... exit 1 ... fi`
    assert re.search(
        r'if\s+!\s+docker\s+pull\b', content
    ), (
        "build-kiwi-image.sh must guard 'docker pull' with an "
        "'if ! docker pull' error-handling pattern"
    )

    # There must be an exit 1 associated with the pull failure path
    # Find the block between `if ! docker pull` and the next `fi`
    pull_block = re.search(
        r'if\s+!\s+docker\s+pull\b.*?fi',
        content,
        re.DOTALL,
    )
    assert pull_block is not None, (
        "build-kiwi-image.sh must have a complete if/fi block around docker pull"
    )

    block_text = pull_block.group(0)
    assert "exit 1" in block_text, (
        "The docker pull error-handling block must contain 'exit 1'"
    )

    # A descriptive error message should be present
    assert re.search(r'(echo|::error)', block_text), (
        "The docker pull error-handling block must include a descriptive "
        "error message (echo or ::error)"
    )


def test_container_image_load_failure_halts_build():
    """
    Property 120: Container Image Load Failure Halts Build

    If `docker load` fails in config.sh, the script must exit with a non-zero
    exit code and a descriptive error message.

    Parses config.sh for error handling around the `docker load` command
    (e.g., `if ! docker load` pattern with `exit 1`).

    **Validates: Requirements 34.7**
    """
    config_script = Path("kiwi-descriptions/config.sh")
    assert config_script.exists(), "config.sh must exist"

    content = config_script.read_text()

    # The script must use an error-handling pattern around docker load.
    assert re.search(
        r'if\s+!\s+docker\s+load\b', content
    ), (
        "config.sh must guard 'docker load' with an "
        "'if ! docker load' error-handling pattern"
    )

    # Find the block between `if ! docker load` and the next `fi`
    load_block = re.search(
        r'if\s+!\s+docker\s+load\b.*?fi',
        content,
        re.DOTALL,
    )
    assert load_block is not None, (
        "config.sh must have a complete if/fi block around docker load"
    )

    block_text = load_block.group(0)
    assert "exit 1" in block_text, (
        "The docker load error-handling block must contain 'exit 1'"
    )

    # A descriptive error message should be present
    assert re.search(r'(echo|ERROR)', block_text), (
        "The docker load error-handling block must include a descriptive "
        "error message"
    )
