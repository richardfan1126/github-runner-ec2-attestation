"""
Property-based tests for artifact reference validation.

These tests validate that the artifact_ref argument is validated against
a strict allowlist pattern requiring digest-pinned references before any
shell interpolation occurs.

**Validates: Requirements 15.15, 15.16, 15.17, 15.18, 15.19, 15.20, 15.21**
"""

import re
from pathlib import Path

import pytest
from hypothesis import given, strategies as st, settings, assume

# Import build_ami module using importlib (filename has a hyphen)
import importlib.util
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)

# Pattern for valid digest-pinned artifact references
ARTIFACT_REF_PATTERN = re.compile(
    r'^ghcr\.io/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+(?:/[a-zA-Z0-9._-]+)*'
    r'(?::[a-zA-Z0-9._-]+)?'
    r'@sha256:[0-9a-fA-F]{64}$'
)

SHELL_METACHARACTERS = list(';|&$`(){}[]!<>"\' \t\n\\#~*?')


def hex64_strategy():
    """Generate a valid 64-character hex string."""
    return st.from_regex(r"[0-9a-f]{64}", fullmatch=True)


def valid_artifact_ref_strategy():
    """Generate valid GHCR artifact references with digest pins."""
    return st.builds(
        lambda owner, repo, tag, digest: f"ghcr.io/{owner}/{repo}:{tag}@sha256:{digest}",
        owner=st.from_regex(r"[a-zA-Z0-9._-]+", fullmatch=True).filter(lambda s: 1 <= len(s) <= 40),
        repo=st.from_regex(r"[a-zA-Z0-9._-]+", fullmatch=True).filter(lambda s: 1 <= len(s) <= 40),
        tag=st.from_regex(r"[a-zA-Z0-9._-]+", fullmatch=True).filter(lambda s: 1 <= len(s) <= 40),
        digest=hex64_strategy(),
    )


def valid_artifact_ref_no_tag_strategy():
    """Generate valid GHCR artifact references with digest pins but no tag."""
    return st.builds(
        lambda owner, repo, digest: f"ghcr.io/{owner}/{repo}@sha256:{digest}",
        owner=st.from_regex(r"[a-zA-Z0-9._-]+", fullmatch=True).filter(lambda s: 1 <= len(s) <= 40),
        repo=st.from_regex(r"[a-zA-Z0-9._-]+", fullmatch=True).filter(lambda s: 1 <= len(s) <= 40),
        digest=hex64_strategy(),
    )


def tag_only_artifact_ref_strategy():
    """Generate GHCR artifact references with tag but NO digest (should be rejected)."""
    return st.builds(
        lambda owner, repo, tag: f"ghcr.io/{owner}/{repo}:{tag}",
        owner=st.from_regex(r"[a-zA-Z0-9._-]+", fullmatch=True).filter(lambda s: 1 <= len(s) <= 40),
        repo=st.from_regex(r"[a-zA-Z0-9._-]+", fullmatch=True).filter(lambda s: 1 <= len(s) <= 40),
        tag=st.from_regex(r"[a-zA-Z0-9._-]+", fullmatch=True).filter(lambda s: 1 <= len(s) <= 40),
    )


def shell_metachar_strategy():
    """Generate strings containing shell metacharacters."""
    return st.sampled_from(SHELL_METACHARACTERS)


# Property 157: Artifact Ref Validation (updated for digest pinning)
# The AMI_Converter SHALL validate the artifact_ref argument against a strict
# allowlist pattern requiring @sha256:<64 hex chars> and reject refs without
# digests or with characters outside the allowlist.


@settings(max_examples=100)
@given(artifact_ref=valid_artifact_ref_strategy())
def test_valid_digest_pinned_refs_accepted(artifact_ref: str):
    """
    Property 157: Artifact Ref Validation (digest-pinned)

    For any artifact reference matching the allowlist pattern with a valid
    @sha256:<64 hex chars> digest, validation should succeed without raising.

    **Validates: Requirements 15.15, 15.16, 15.17, 15.18**
    """
    assert ARTIFACT_REF_PATTERN.match(artifact_ref), \
        f"Generated ref should match pattern: {artifact_ref}"
    # Should not raise
    build_ami.validate_artifact_reference(artifact_ref)


@settings(max_examples=50)
@given(artifact_ref=valid_artifact_ref_no_tag_strategy())
def test_valid_digest_only_refs_accepted(artifact_ref: str):
    """
    Property 157: Artifact Ref Validation (digest-only, no tag)

    For any artifact reference with a valid digest but no tag,
    validation should succeed without raising.

    **Validates: Requirements 15.17, 15.18**
    """
    assert ARTIFACT_REF_PATTERN.match(artifact_ref), \
        f"Generated ref should match pattern: {artifact_ref}"
    build_ami.validate_artifact_reference(artifact_ref)


@settings(max_examples=100)
@given(artifact_ref=tag_only_artifact_ref_strategy())
def test_tag_only_refs_rejected(artifact_ref: str):
    """
    Property 157: Artifact Ref Validation (tag-only rejected)

    For any artifact reference that has a tag but no @sha256: digest,
    validation should reject with ValueError indicating digest is required.

    **Validates: Requirements 15.17, 15.18**
    """
    with pytest.raises(ValueError, match="digest-pinned"):
        build_ami.validate_artifact_reference(artifact_ref)


@settings(max_examples=100)
@given(
    base_ref=valid_artifact_ref_strategy(),
    metachar=shell_metachar_strategy(),
    position=st.sampled_from(["prefix", "owner", "repo", "tag", "suffix"]),
)
def test_shell_metacharacters_rejected(base_ref: str, metachar: str, position: str):
    """
    Property 157: Artifact Ref Validation

    For any artifact reference containing shell metacharacters
    (;, |, &, $, `, etc.), validation should reject with ValueError
    before any shell interpolation can occur.

    **Validates: Requirements 15.15, 15.16**
    """
    # Split: ghcr.io/owner/repo:tag@sha256:digest
    # Inject the metacharacter at various positions in the path/tag portion
    at_split = base_ref.split("@", 1)
    base_part = at_split[0]  # ghcr.io/owner/repo:tag
    digest_part = at_split[1]  # sha256:<hex64>

    parts = base_part.split("/", 2)  # ['ghcr.io', 'owner', 'repo:tag']
    repo_tag = parts[2].split(":")

    if position == "prefix":
        bad_ref = metachar + base_ref
    elif position == "owner":
        bad_ref = f"ghcr.io/{parts[1]}{metachar}/{repo_tag[0]}:{repo_tag[1]}@{digest_part}"
    elif position == "repo":
        bad_ref = f"ghcr.io/{parts[1]}/{repo_tag[0]}{metachar}:{repo_tag[1]}@{digest_part}"
    elif position == "tag":
        bad_ref = f"ghcr.io/{parts[1]}/{repo_tag[0]}:{repo_tag[1]}{metachar}@{digest_part}"
    else:  # suffix
        bad_ref = base_ref + metachar

    # Ensure the injected ref doesn't accidentally match the pattern
    assume(not ARTIFACT_REF_PATTERN.match(bad_ref))

    with pytest.raises(ValueError):
        build_ami.validate_artifact_reference(bad_ref)


@settings(max_examples=100)
@given(random_str=st.text(min_size=1, max_size=100))
def test_random_strings_rejected(random_str: str):
    """
    Property 157: Artifact Ref Validation

    For any random string that does not match the strict allowlist pattern,
    validation should reject with ValueError.

    **Validates: Requirements 15.15, 15.16**
    """
    assume(not ARTIFACT_REF_PATTERN.match(random_str))

    with pytest.raises(ValueError):
        build_ami.validate_artifact_reference(random_str)
