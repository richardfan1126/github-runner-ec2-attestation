"""
Unit tests for artifact reference validation.

Tests that the validate_artifact_reference function correctly accepts valid
artifact refs and rejects refs with shell metacharacters or invalid formats.

Requirements: 15.15, 15.16
"""

from pathlib import Path

import pytest

# Import build_ami module using importlib (filename has a hyphen)
import importlib.util
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)


class TestValidArtifactRefs:
    """Test that valid artifact references are accepted."""

    def test_simple_valid_ref(self):
        build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag")

    def test_ref_with_hyphens(self):
        build_ami.validate_artifact_reference("ghcr.io/my-org/my-repo:v1.0")

    def test_ref_with_dots(self):
        build_ami.validate_artifact_reference("ghcr.io/my-org/my.repo:v1.0")

    def test_ref_with_underscores(self):
        build_ami.validate_artifact_reference("ghcr.io/my_org/my_repo:v1_0")

    def test_ref_with_mixed_case(self):
        build_ami.validate_artifact_reference("ghcr.io/MyOrg/MyRepo:Latest")

    def test_ref_with_numeric_tag(self):
        build_ami.validate_artifact_reference("ghcr.io/owner/repo:123")

    def test_ref_with_complex_tag(self):
        build_ami.validate_artifact_reference("ghcr.io/owner/repo:main-20240101-abc123")


class TestShellMetacharacterRejection:
    """Test that refs with shell metacharacters are rejected."""

    def test_semicolon_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag;rm -rf /")

    def test_pipe_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag|cat /etc/passwd")

    def test_ampersand_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag&echo pwned")

    def test_dollar_sign_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag$(whoami)")

    def test_backtick_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag`id`")

    def test_parentheses_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag()")

    def test_curly_braces_rejected(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag{}")


class TestSpacesRejected:
    """Test that refs with spaces are rejected."""

    def test_space_in_tag(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag with space")

    def test_space_in_owner(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/my owner/repo:tag")

    def test_tab_in_ref(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo:tag\there")


class TestMissingComponents:
    """Test that refs missing required components are rejected."""

    def test_missing_tag(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo")

    def test_missing_repo(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/owner:tag")

    def test_missing_owner_and_repo(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("ghcr.io/:tag")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("")

    def test_wrong_registry(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("docker.io/owner/repo:tag")

    def test_no_registry_prefix(self):
        with pytest.raises(ValueError):
            build_ami.validate_artifact_reference("owner/repo:tag")
