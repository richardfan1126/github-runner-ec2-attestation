"""
Unit tests for rootless Docker source compilation.

Tests validate that build infrastructure files (build-kiwi-image.sh and
Dockerfile.kiwi-builder) contain the correct compilation steps, pinned
versions, and build dependencies for rootlesskit, slirp4netns, and
fuse-overlayfs.

Validates: Requirements 53.1, 53.2, 53.3, 53.5, 53.6, 53.7, 53.8, 53.9
"""

import re
from pathlib import Path

import pytest


# Paths to source files under test
BUILD_SCRIPT_PATH = Path(__file__).parent.parent / ".github" / "scripts" / "build-kiwi-image.sh"
DOCKERFILE_PATH = Path(__file__).parent.parent / ".github" / "docker" / "Dockerfile.kiwi-builder"


@pytest.fixture
def build_script_content():
    """Read build-kiwi-image.sh content."""
    assert BUILD_SCRIPT_PATH.exists(), f"Build script not found: {BUILD_SCRIPT_PATH}"
    return BUILD_SCRIPT_PATH.read_text()


@pytest.fixture
def dockerfile_content():
    """Read Dockerfile.kiwi-builder content."""
    assert DOCKERFILE_PATH.exists(), f"Dockerfile not found: {DOCKERFILE_PATH}"
    return DOCKERFILE_PATH.read_text()


# =============================================================================
# Git clone commands at pinned tags (Requirement 53.5, 53.6, 53.7)
# =============================================================================

class TestGitCloneCommands:
    """Test that build-kiwi-image.sh clones each tool at a pinned tag."""

    def test_rootlesskit_git_clone_at_pinned_tag(self, build_script_content):
        """rootlesskit is cloned from the official repo at a pinned release tag."""
        # Verify a version variable is defined with a pinned tag
        assert re.search(
            r'ROOTLESSKIT_VERSION="v[\d.]+"',
            build_script_content
        ), "build-kiwi-image.sh must define ROOTLESSKIT_VERSION with a pinned tag"
        # Verify git clone uses the version variable
        assert re.search(
            r"git clone.*--branch.*ROOTLESSKIT_VERSION.*rootless-containers/rootlesskit",
            build_script_content
        ), "build-kiwi-image.sh must clone rootlesskit at the pinned version"

    def test_slirp4netns_git_clone_at_pinned_tag(self, build_script_content):
        """slirp4netns is cloned from the official repo at a pinned release tag."""
        assert re.search(
            r'SLIRP4NETNS_VERSION="v[\d.]+"',
            build_script_content
        ), "build-kiwi-image.sh must define SLIRP4NETNS_VERSION with a pinned tag"
        assert re.search(
            r"git clone.*--branch.*SLIRP4NETNS_VERSION.*rootless-containers/slirp4netns",
            build_script_content
        ), "build-kiwi-image.sh must clone slirp4netns at the pinned version"

    def test_fuse_overlayfs_git_clone_at_pinned_tag(self, build_script_content):
        """fuse-overlayfs is cloned from the official repo at a pinned release tag."""
        assert re.search(
            r'FUSE_OVERLAYFS_VERSION="v[\d.]+"',
            build_script_content
        ), "build-kiwi-image.sh must define FUSE_OVERLAYFS_VERSION with a pinned tag"
        assert re.search(
            r"git clone.*--branch.*FUSE_OVERLAYFS_VERSION.*containers/fuse-overlayfs",
            build_script_content
        ), "build-kiwi-image.sh must clone fuse-overlayfs at the pinned version"


# =============================================================================
# Build commands (Requirement 53.5, 53.6, 53.7)
# =============================================================================

class TestBuildCommands:
    """Test that build-kiwi-image.sh uses the correct build system for each tool."""

    def test_rootlesskit_go_build(self, build_script_content):
        """rootlesskit is built using `go build`."""
        assert "go build" in build_script_content, (
            "build-kiwi-image.sh must use 'go build' for rootlesskit"
        )
        # Verify it builds the rootlesskit binary
        assert re.search(
            r"go build.*rootlesskit",
            build_script_content
        ), "build-kiwi-image.sh must build the rootlesskit binary with go build"

    def test_slirp4netns_autotools_build(self, build_script_content):
        """slirp4netns is built using autotools (autogen.sh, configure, make)."""
        assert "./autogen.sh" in build_script_content, (
            "build-kiwi-image.sh must run ./autogen.sh for slirp4netns"
        )
        assert "./configure" in build_script_content, (
            "build-kiwi-image.sh must run ./configure for slirp4netns"
        )
        assert "make" in build_script_content, (
            "build-kiwi-image.sh must run make for slirp4netns"
        )

    def test_fuse_overlayfs_autotools_build(self, build_script_content):
        """fuse-overlayfs v1.14 is built using autotools (autogen.sh, configure, make)."""
        # fuse-overlayfs v1.14 is the C implementation (the Rust rewrite is unreleased).
        # It uses the same autotools build system as slirp4netns.
        assert re.search(
            r"fuse-overlayfs.*\./autogen\.sh",
            build_script_content,
            re.DOTALL
        ), "build-kiwi-image.sh must run ./autogen.sh for fuse-overlayfs"


# =============================================================================
# Binary placement in KIWI image overlay (Requirement 53.9)
# =============================================================================

class TestBinaryPlacement:
    """Test that compiled binaries are copied to the KIWI image overlay at /usr/local/bin/."""

    def test_output_directory_created(self, build_script_content):
        """The /usr/local/bin output directory is created in the image overlay."""
        assert re.search(
            r"mkdir.*root/usr/local/bin",
            build_script_content
        ), "build-kiwi-image.sh must create root/usr/local/bin in the image overlay"

    def test_rootlesskit_binary_output(self, build_script_content):
        """rootlesskit binary is output to /output/ or /usr/local/bin."""
        assert re.search(
            r"go build -o /output/rootlesskit",
            build_script_content
        ), "rootlesskit binary must be built to the output directory"

    def test_rootlesskit_docker_proxy_binary_output(self, build_script_content):
        """rootlesskit-docker-proxy binary is also built."""
        assert "rootlesskit-docker-proxy" in build_script_content, (
            "build-kiwi-image.sh must also build rootlesskit-docker-proxy"
        )

    def test_slirp4netns_binary_copied_to_output(self, build_script_content):
        """slirp4netns binary is copied to the output directory."""
        assert re.search(
            r"cp.*slirp4netns.*/output/slirp4netns",
            build_script_content
        ), "slirp4netns binary must be copied to the output directory"

    def test_fuse_overlayfs_binary_copied_to_output(self, build_script_content):
        """fuse-overlayfs binary is copied to the output directory."""
        assert re.search(
            r"cp.*fuse-overlayfs.*/output/fuse-overlayfs",
            build_script_content
        ), "fuse-overlayfs binary must be copied to the output directory"

    def test_output_volume_maps_to_usr_local_bin(self, build_script_content):
        """Docker volume maps the output to the image overlay's /usr/local/bin."""
        assert re.search(
            r"-v.*root/usr/local/bin:/output",
            build_script_content
        ), "Docker run must mount root/usr/local/bin as /output"


# =============================================================================
# Dockerfile build dependencies (Requirement 53.1, 53.2, 53.3)
# =============================================================================

class TestDockerfileBuildDependencies:
    """Test that Dockerfile.kiwi-builder includes required build dependencies."""

    def test_golang_installed(self, dockerfile_content):
        """golang package is installed for compiling rootlesskit."""
        assert re.search(
            r"^\s*golang\s*\\?\s*$",
            dockerfile_content,
            re.MULTILINE
        ), "Dockerfile must install golang for rootlesskit compilation"

    def test_glib2_devel_installed(self, dockerfile_content):
        """glib2-devel is installed for slirp4netns compilation."""
        assert re.search(
            r"^\s*glib2-devel\s*\\?\s*$",
            dockerfile_content,
            re.MULTILINE
        ), "Dockerfile must install glib2-devel for slirp4netns"

    def test_libslirp_devel_installed(self, dockerfile_content):
        """libslirp-devel is installed for slirp4netns compilation."""
        assert re.search(
            r"^\s*libslirp-devel\s*\\?\s*$",
            dockerfile_content,
            re.MULTILINE
        ), "Dockerfile must install libslirp-devel for slirp4netns"

    def test_libcap_devel_installed(self, dockerfile_content):
        """libcap-devel is installed for slirp4netns compilation."""
        assert re.search(
            r"^\s*libcap-devel\s*\\?\s*$",
            dockerfile_content,
            re.MULTILINE
        ), "Dockerfile must install libcap-devel for slirp4netns"

    def test_libseccomp_devel_installed(self, dockerfile_content):
        """libseccomp-devel is installed for slirp4netns compilation."""
        assert re.search(
            r"^\s*libseccomp-devel\s*\\?\s*$",
            dockerfile_content,
            re.MULTILINE
        ), "Dockerfile must install libseccomp-devel for slirp4netns"

    def test_fuse3_devel_installed(self, dockerfile_content):
        """fuse3-devel is installed for fuse-overlayfs compilation."""
        assert re.search(
            r"^\s*fuse3-devel\s*\\?\s*$",
            dockerfile_content,
            re.MULTILINE
        ), "Dockerfile must install fuse3-devel for fuse-overlayfs"
