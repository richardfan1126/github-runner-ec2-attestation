"""
Property-based tests for artifact reference validation.

These tests validate that the artifact_ref argument is validated against
a strict allowlist pattern before any shell interpolation occurs.

**Validates: Requirements 15.15, 15.16**
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

ARTIFACT_REF_PATTERN = re.compile(
    r'^ghcr\.io/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+:[a-zA-Z0-9._-]+$'
)

SHELL_METACHARACTERS = list(';|&$`(){}[]!<>"\' \t\n\\#~*?')


def valid_artifact_ref_strategy():
    """Generate valid GHCR artifact references matching the allowlist pattern."""
    return st.builds(
        lambda owner, repo, tag: f"ghcr.io/{owner}/{repo}:{tag}",
        owner=st.from_regex(r"[a-zA-Z0-9._-]+", fullmatch=True).filter(lambda s: len(s) <= 40),
        repo=st.from_regex(r"[a-zA-Z0-9._-]+", fullmatch=True).filter(lambda s: len(s) <= 40),
        tag=st.from_regex(r"[a-zA-Z0-9._-]+", fullmatch=True).filter(lambda s: len(s) <= 40),
    )


def shell_metachar_strategy():
    """Generate strings containing shell metacharacters."""
    return st.sampled_from(SHELL_METACHARACTERS)


# Property 157: Artifact Ref Validation
# The AMI_Converter SHALL validate the artifact_ref argument against a strict
# allowlist pattern and reject refs with characters outside the allowlist.


@settings(max_examples=100)
@given(artifact_ref=valid_artifact_ref_strategy())
def test_valid_artifact_refs_accepted(artifact_ref: str):
    """
    Property 157: Artifact Ref Validation

    For any artifact reference matching the allowlist pattern
    ^ghcr\\.io/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+:[a-zA-Z0-9._-]+$,
    validation should succeed without raising an exception.

    **Validates: Requirements 15.15, 15.16**
    """
    assert ARTIFACT_REF_PATTERN.match(artifact_ref), \
        f"Generated ref should match pattern: {artifact_ref}"
    # Should not raise
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
    # Inject the metacharacter at various positions
    parts = base_ref.split("/", 2)  # ['ghcr.io', 'owner', 'repo:tag']
    repo_tag = parts[2].split(":")

    if position == "prefix":
        bad_ref = metachar + base_ref
    elif position == "owner":
        bad_ref = f"ghcr.io/{parts[1]}{metachar}/{repo_tag[0]}:{repo_tag[1]}"
    elif position == "repo":
        bad_ref = f"ghcr.io/{parts[1]}/{repo_tag[0]}{metachar}:{repo_tag[1]}"
    elif position == "tag":
        bad_ref = f"ghcr.io/{parts[1]}/{repo_tag[0]}:{repo_tag[1]}{metachar}"
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
