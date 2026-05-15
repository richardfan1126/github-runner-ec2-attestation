"""
Unit tests for artifact reference validation.

Tests that the validate_artifact_reference function correctly accepts valid
digest-pinned artifact refs and rejects refs without digests, with shell
metacharacters, or with invalid formats.

Requirements: 15.15, 15.16, 15.17, 15.18, 15.19, 15.20, 15.21
"""

from pathlib import Path

import pytest

# Import build_ami module using importlib (filename has a hyphen)
import importlib.util
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)

# A valid 64-char hex digest for test references
VALID_DIGEST = "a" * 64


class TestValidArtifactRefs:
    """Test that valid digest-pinned artifact references are accepted."""

    def test_tag_and_digest(self):
        build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag@sha256:{VALID_DIGEST}")

    def test_digest_only_no_tag(self):
        build_ami.validate_artifact_reference(f"ghcr.io/owner/repo@sha256:{VALID_DIGEST}")

    def test_ref_with_hyphens(self):
        build_ami.validate_artifact_reference(f"ghcr.io/my-org/my-repo:v1.0@sha256:{VALID_DIGEST}")

    def test_ref_with_dots(self):
        build_ami.validate_artifact_reference(f"ghcr.io/my-org/my.repo:v1.0@sha256:{VALID_DIGEST}")

    def test_ref_with_underscores(self):
        build_ami.validate_artifact_reference(f"ghcr.io/my_org/my_repo:v1_0@sha256:{VALID_DIGEST}")

    def test_ref_with_mixed_case(self):
        build_ami.validate_artifact_reference(f"ghcr.io/MyOrg/MyRepo:Latest@sha256:{VALID_DIGEST}")

    def test_ref_with_numeric_tag(self):
        build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:123@sha256:{VALID_DIGEST}")

    def test_ref_with_complex_tag(self):
        build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:main-20240101-abc123@sha256:{VALID_DIGEST}")

    def test_ref_with_package_segment(self):
        build_ami.validate_artifact_reference(f"ghcr.io/owner/repo/package:v1@sha256:{VALID_DIGEST}")

    def test_ref_with_uppercase_hex(self):
        build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag@sha256:{'A' * 64}")

    def test_ref_with_mixed_hex(self):
        build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag@sha256:{'aB1c2D3e' * 8}")


class TestDigestRequired:
    """Test that refs without @sha256: digest are rejected."""

    def test_tag_only_rejected(self):
        with pytest.raises(ValueError, match="digest-pinned"):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag")

    def test_tag_only_with_version_rejected(self):
        with pytest.raises(ValueError, match="digest-pinned"):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:v1.0")

    def test_tag_only_with_package_rejected(self):
        with pytest.raises(ValueError, match="digest-pinned"):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo/package:latest")

    def test_short_digest_rejected(self):
        """Digest must be exactly 64 hex chars."""
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag@sha256:abcdef")

    def test_non_hex_digest_rejected(self):
        """Digest must contain only hex characters."""
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag@sha256:{'g' * 64}")


class TestShellMetacharacterRejection:
    """Test that refs with shell metacharacters are rejected."""

    def test_semicolon_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag;rm -rf /@sha256:{VALID_DIGEST}")

    def test_pipe_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag|cat@sha256:{VALID_DIGEST}")

    def test_ampersand_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag&echo@sha256:{VALID_DIGEST}")

    def test_dollar_sign_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag$(whoami)@sha256:{VALID_DIGEST}")

    def test_backtick_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag`id`@sha256:{VALID_DIGEST}")

    def test_parentheses_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag()@sha256:{VALID_DIGEST}")

    def test_curly_braces_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag{}@sha256:" + VALID_DIGEST)


class TestSpacesRejected:
    """Test that refs with spaces are rejected."""

    def test_space_in_tag(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag with space@sha256:{VALID_DIGEST}")

    def test_space_in_owner(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/my owner/repo:tag@sha256:{VALID_DIGEST}")

    def test_tab_in_ref(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/owner/repo:tag\there@sha256:{VALID_DIGEST}")


class TestMissingComponents:
    """Test that refs missing required components are rejected."""

    def test_missing_tag_and_digest(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo")

    def test_missing_repo(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/owner@sha256:{VALID_DIGEST}")

    def test_missing_owner_and_repo(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"ghcr.io/@sha256:{VALID_DIGEST}")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("")

    def test_wrong_registry(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"docker.io/owner/repo:tag@sha256:{VALID_DIGEST}")

    def test_no_registry_prefix(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference(f"owner/repo:tag@sha256:{VALID_DIGEST}")


class TestHelperFunctions:
    """Test extract_digest_from_artifact_ref and get_digest_pinned_ref."""

    def test_extract_digest_with_tag(self):
        ref = f"ghcr.io/owner/repo:v1@sha256:{VALID_DIGEST}"
        assert build_ami.extract_digest_from_artifact_ref(ref) == f"sha256:{VALID_DIGEST}"

    def test_extract_digest_without_tag(self):
        ref = f"ghcr.io/owner/repo@sha256:{VALID_DIGEST}"
        assert build_ami.extract_digest_from_artifact_ref(ref) == f"sha256:{VALID_DIGEST}"

    def test_extract_digest_raises_without_digest(self):
        with pytest.raises(ValueError):
            build_ami.extract_digest_from_artifact_ref("ghcr.io/owner/repo:v1")

    def test_get_digest_pinned_ref_strips_tag(self):
        ref = f"ghcr.io/owner/repo:v1@sha256:{VALID_DIGEST}"
        assert build_ami.get_digest_pinned_ref(ref) == f"ghcr.io/owner/repo@sha256:{VALID_DIGEST}"

    def test_get_digest_pinned_ref_no_tag(self):
        ref = f"ghcr.io/owner/repo@sha256:{VALID_DIGEST}"
        assert build_ami.get_digest_pinned_ref(ref) == f"ghcr.io/owner/repo@sha256:{VALID_DIGEST}"

    def test_get_digest_pinned_ref_with_package(self):
        ref = f"ghcr.io/owner/repo/pkg:v1@sha256:{VALID_DIGEST}"
        assert build_ami.get_digest_pinned_ref(ref) == f"ghcr.io/owner/repo/pkg@sha256:{VALID_DIGEST}"
