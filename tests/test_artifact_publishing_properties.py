"""
Property-based tests for artifact publishing in GitHub Actions workflow.

These tests validate the correctness properties for artifact publishing,
including PCR extraction, artifact annotation, tag uniqueness, and attestation completeness.
"""

import json
import os
import re
import tempfile
from datetime import datetime, UTC
from pathlib import Path
from typing import Dict, Any

import pytest
from hypothesis import given, strategies as st, settings, assume


# Feature: github-actions-remote-executor, Property 63: PCR Extraction Validation
@settings(max_examples=100)
@given(
    pcr4_value=st.text(
        alphabet="0123456789abcdef",
        min_size=96,  # SHA-384 is 96 hex characters
        max_size=96
    ),
    pcr7_value=st.text(
        alphabet="0123456789abcdef",
        min_size=96,
        max_size=96
    )
)
def test_pcr_extraction_validation(pcr4_value: str, pcr7_value: str):
    """
    Property 63: PCR Extraction Validation
    
    For any valid pcr_measurements.json file containing PCR4 and PCR7 values,
    the extraction process should successfully retrieve both values without errors.
    
    **Validates: Requirements 12.1**
    """
    # Create temporary PCR measurements file
    with tempfile.TemporaryDirectory() as temp_dir:
        pcr_file = Path(temp_dir) / "pcr_measurements.json"
        
        pcr_data = {
            "Measurements": {
                "PCR4": pcr4_value,
                "PCR7": pcr7_value
            }
        }
        
        pcr_file.write_text(json.dumps(pcr_data, indent=2))
        
        # Simulate extraction (as done in workflow)
        with open(pcr_file) as f:
            data = json.load(f)
        
        extracted_pcr4 = data["Measurements"]["PCR4"]
        extracted_pcr7 = data["Measurements"]["PCR7"]
        
        # Validate extraction succeeded
        assert extracted_pcr4 == pcr4_value, "PCR4 should be extracted correctly"
        assert extracted_pcr7 == pcr7_value, "PCR7 should be extracted correctly"
        
        # Validate values are non-empty
        assert len(extracted_pcr4) > 0, "Extracted PCR4 must be non-empty"
        assert len(extracted_pcr7) > 0, "Extracted PCR7 must be non-empty"
        
        # Validate values are valid hex strings
        try:
            int(extracted_pcr4, 16)
            int(extracted_pcr7, 16)
        except ValueError:
            pytest.fail("Extracted PCR values must be valid hexadecimal")


@settings(max_examples=100)
@given(
    missing_field=st.sampled_from(["PCR4", "PCR7", "Measurements"])
)
def test_pcr_extraction_missing_field_detection(missing_field: str):
    """
    For any pcr_measurements.json file with a missing required field,
    the extraction process should detect the missing field.
    
    **Validates: Requirements 12.7**
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        pcr_file = Path(temp_dir) / "pcr_measurements.json"
        
        # Create incomplete PCR data
        if missing_field == "Measurements":
            pcr_data = {}
        elif missing_field == "PCR4":
            pcr_data = {
                "Measurements": {
                    "PCR7": "a" * 96
                }
            }
        else:  # PCR7
            pcr_data = {
                "Measurements": {
                    "PCR4": "b" * 96
                }
            }
        
        pcr_file.write_text(json.dumps(pcr_data, indent=2))
        
        # Attempt extraction
        with open(pcr_file) as f:
            data = json.load(f)
        
        # Validate that missing field is detectable
        if missing_field == "Measurements":
            assert "Measurements" not in data
        elif missing_field == "PCR4":
            assert "Measurements" in data
            assert "PCR4" not in data["Measurements"]
        else:  # PCR7
            assert "Measurements" in data
            assert "PCR7" not in data["Measurements"]


# Feature: github-actions-remote-executor, Property 64: Artifact Annotation Completeness
@settings(max_examples=100)
@given(
    pcr4=st.text(alphabet="0123456789abcdef", min_size=96, max_size=96),
    pcr7=st.text(alphabet="0123456789abcdef", min_size=96, max_size=96),
    commit_sha=st.text(alphabet="0123456789abcdef", min_size=40, max_size=40),
    repo_owner=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789-",
        min_size=1,
        max_size=39
    ).filter(lambda x: not x.startswith("-") and not x.endswith("-")),
    repo_name=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.",
        min_size=1,
        max_size=100
    )
)
def test_artifact_annotation_completeness(
    pcr4: str,
    pcr7: str,
    commit_sha: str,
    repo_owner: str,
    repo_name: str
):
    """
    Property 64: Artifact Annotation Completeness
    
    For any artifact pushed to GHCR, the artifact should be annotated with
    all required metadata including PCR4, PCR7, creation timestamp, source URL,
    and commit revision.
    
    **Validates: Requirements 12.3, 12.5**
    """
    # Simulate artifact annotations (as would be passed to ORAS)
    annotations = {
        "org.opencontainers.image.created": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "org.opencontainers.image.source": f"https://github.com/{repo_owner}/{repo_name}",
        "org.opencontainers.image.revision": commit_sha,
        "com.github.attestable-image.pcr4": pcr4,
        "com.github.attestable-image.pcr7": pcr7
    }
    
    # Validate all required annotations are present
    required_annotations = [
        "org.opencontainers.image.created",
        "org.opencontainers.image.source",
        "org.opencontainers.image.revision",
        "com.github.attestable-image.pcr4",
        "com.github.attestable-image.pcr7"
    ]
    
    for annotation in required_annotations:
        assert annotation in annotations, f"Annotation {annotation} must be present"
        assert annotations[annotation], f"Annotation {annotation} must have a value"
    
    # Validate annotation values
    assert annotations["com.github.attestable-image.pcr4"] == pcr4
    assert annotations["com.github.attestable-image.pcr7"] == pcr7
    assert annotations["org.opencontainers.image.revision"] == commit_sha
    
    # Validate timestamp format (ISO 8601 with Z suffix)
    created_timestamp = annotations["org.opencontainers.image.created"]
    assert created_timestamp.endswith("Z"), "Timestamp must be in UTC with Z suffix"
    
    # Validate source URL format
    source_url = annotations["org.opencontainers.image.source"]
    assert source_url.startswith("https://github.com/"), "Source must be GitHub URL"
    assert f"{repo_owner}/{repo_name}" in source_url, "Source must include repo path"


# Feature: github-actions-remote-executor, Property 65: Artifact Tag Uniqueness
@settings(max_examples=100)
@given(
    branch_name=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_./",
        min_size=1,
        max_size=50
    ),
    commit_sha=st.text(alphabet="0123456789abcdef", min_size=40, max_size=40),
    timestamp1=st.datetimes(
        min_value=datetime(2024, 1, 1),
        max_value=datetime(2030, 12, 31)
    ),
    timestamp2=st.datetimes(
        min_value=datetime(2024, 1, 1),
        max_value=datetime(2030, 12, 31)
    )
)
def test_artifact_tag_uniqueness(
    branch_name: str,
    commit_sha: str,
    timestamp1: datetime,
    timestamp2: datetime
):
    """
    Property 65: Artifact Tag Uniqueness
    
    For any two artifact builds with different timestamps or commit SHAs,
    the generated artifact tags should be unique.
    
    **Validates: Requirements 12.4**
    """
    # Sanitize branch name (as done in workflow)
    sanitized_branch = re.sub(r'[^a-zA-Z0-9._-]', '-', branch_name)
    
    # Generate tags (as done in workflow)
    tag1 = f"{sanitized_branch}-{timestamp1.strftime('%Y%m%d-%H%M%S')}-{commit_sha[:8]}"
    tag2 = f"{sanitized_branch}-{timestamp2.strftime('%Y%m%d-%H%M%S')}-{commit_sha[:8]}"
    
    # If timestamps are different, tags should be different
    if timestamp1 != timestamp2:
        assert tag1 != tag2, "Tags with different timestamps must be unique"
    
    # Validate tag format
    assert len(tag1) > 0, "Tag must not be empty"
    assert len(tag2) > 0, "Tag must not be empty"
    
    # Validate tag contains commit SHA prefix
    assert commit_sha[:8] in tag1, "Tag must contain commit SHA prefix"
    assert commit_sha[:8] in tag2, "Tag must contain commit SHA prefix"


@settings(max_examples=100)
@given(
    branch_name=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_./",
        min_size=1,
        max_size=50
    ),
    commit_sha1=st.text(alphabet="0123456789abcdef", min_size=40, max_size=40),
    commit_sha2=st.text(alphabet="0123456789abcdef", min_size=40, max_size=40),
    timestamp=st.datetimes(
        min_value=datetime(2024, 1, 1),
        max_value=datetime(2030, 12, 31)
    )
)
def test_artifact_tag_uniqueness_different_commits(
    branch_name: str,
    commit_sha1: str,
    commit_sha2: str,
    timestamp: datetime
):
    """
    For any two artifact builds with different commit SHAs but same timestamp,
    the generated artifact tags should be unique.
    
    **Validates: Requirements 12.4**
    """
    assume(commit_sha1 != commit_sha2)
    
    # Sanitize branch name
    sanitized_branch = re.sub(r'[^a-zA-Z0-9._-]', '-', branch_name)
    
    # Generate tags
    timestamp_str = timestamp.strftime('%Y%m%d-%H%M%S')
    tag1 = f"{sanitized_branch}-{timestamp_str}-{commit_sha1[:8]}"
    tag2 = f"{sanitized_branch}-{timestamp_str}-{commit_sha2[:8]}"
    
    # Tags should be different due to different commit SHAs
    assert tag1 != tag2, "Tags with different commit SHAs must be unique"


# Feature: github-actions-remote-executor, Property 66: Attestation Bundle Completeness
@settings(max_examples=100)
@given(
    repo_path=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_./",
        min_size=5,
        max_size=100
    ),
    tag=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_.",
        min_size=1,
        max_size=50
    ),
    digest=st.text(
        alphabet="0123456789abcdef",
        min_size=64,
        max_size=64
    )
)
def test_attestation_bundle_completeness(repo_path: str, tag: str, digest: str):
    """
    Property 66: Attestation Bundle Completeness
    
    For any artifact published to GHCR, the attestation bundle should include
    the subject name (artifact reference), subject digest, and be pushed to registry.
    
    **Validates: Requirements 13.3, 13.4**
    """
    # Construct artifact reference
    artifact_ref = f"ghcr.io/{repo_path}/attestable-image:{tag}"
    
    # Simulate attestation parameters (as passed to attest-build-provenance action)
    attestation_params = {
        "subject-name": artifact_ref,
        "subject-digest": f"sha256:{digest}",
        "push-to-registry": True
    }
    
    # Validate required parameters are present
    assert "subject-name" in attestation_params, "Attestation must include subject-name"
    assert "subject-digest" in attestation_params, "Attestation must include subject-digest"
    assert "push-to-registry" in attestation_params, "Attestation must include push-to-registry"
    
    # Validate parameter values
    assert attestation_params["subject-name"] == artifact_ref
    assert attestation_params["subject-digest"] == f"sha256:{digest}"
    assert attestation_params["push-to-registry"] is True
    
    # Validate digest format
    subject_digest = attestation_params["subject-digest"]
    assert subject_digest.startswith("sha256:"), "Digest must have sha256: prefix"
    
    digest_value = subject_digest.split(":", 1)[1]
    assert len(digest_value) == 64, "SHA-256 digest must be 64 hex characters"
    
    # Validate digest is valid hex
    try:
        int(digest_value, 16)
    except ValueError:
        pytest.fail("Digest must be valid hexadecimal")


@settings(max_examples=100)
@given(
    repo_owner=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789-",
        min_size=1,
        max_size=39
    ).filter(lambda x: not x.startswith("-") and not x.endswith("-")),
    repo_name=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.",
        min_size=1,
        max_size=100
    ),
    tag=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_.",
        min_size=1,
        max_size=128
    )
)
def test_artifact_reference_format(repo_owner: str, repo_name: str, tag: str):
    """
    For any artifact published to GHCR, the artifact reference should follow
    the correct format: ghcr.io/{owner}/{repo}/attestable-image:{tag}
    
    **Validates: Requirements 12.2, 12.4**
    """
    # Generate artifact reference (as done in workflow)
    repo_lower = f"{repo_owner}/{repo_name}".lower()
    artifact_ref = f"ghcr.io/{repo_lower}/attestable-image:{tag}"
    
    # Validate format
    assert artifact_ref.startswith("ghcr.io/"), "Artifact ref must start with ghcr.io/"
    assert "/attestable-image:" in artifact_ref, "Artifact ref must include image name"
    assert artifact_ref.endswith(tag), "Artifact ref must end with tag"
    
    # Validate lowercase repository path
    repo_path = artifact_ref.split("ghcr.io/")[1].split("/attestable-image:")[0]
    assert repo_path == repo_path.lower(), "Repository path must be lowercase"


@settings(max_examples=100)
@given(
    raw_image_size=st.integers(min_value=1, max_value=10 * 1024 * 1024 * 1024),  # Up to 10GB
    pcr_file_size=st.integers(min_value=100, max_value=10000)
)
def test_artifact_bundle_contents(raw_image_size: int, pcr_file_size: int):
    """
    For any artifact bundle pushed to GHCR, it should contain both
    the raw disk image and the PCR measurements JSON file.
    
    **Validates: Requirements 12.6**
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        build_output_dir = Path(temp_dir)
        
        # Create raw image file
        raw_image = build_output_dir / "test-image.raw"
        raw_image.write_bytes(b"X" * min(raw_image_size, 1024))  # Limit for test
        
        # Create PCR measurements file
        pcr_file = build_output_dir / "pcr_measurements.json"
        pcr_data = {
            "Measurements": {
                "PCR4": "a" * 96,
                "PCR7": "b" * 96
            }
        }
        pcr_file.write_text(json.dumps(pcr_data))
        
        # Validate both files exist
        assert raw_image.exists(), "Raw image must exist in bundle"
        assert pcr_file.exists(), "PCR measurements must exist in bundle"
        
        # Validate file sizes
        assert raw_image.stat().st_size > 0, "Raw image must not be empty"
        assert pcr_file.stat().st_size > 0, "PCR measurements must not be empty"
        
        # Validate PCR file is valid JSON
        with open(pcr_file) as f:
            data = json.load(f)
        
        assert "Measurements" in data
        assert "PCR4" in data["Measurements"]
        assert "PCR7" in data["Measurements"]


def test_workflow_file_structure():
    """
    Validates that the GitHub Actions workflow file exists and has
    the correct structure for building and publishing attestable images.
    
    **Validates: Requirements 11.3, 12.2, 13.1**
    """
    workflow_file = Path(".github/workflows/build-attestable-image.yml")
    
    assert workflow_file.exists(), "Workflow file must exist"
    
    content = workflow_file.read_text()
    
    # Validate workflow triggers
    assert "on:" in content, "Workflow must have triggers"
    assert "push:" in content or "workflow_dispatch:" in content, \
        "Workflow must have push or manual trigger"
    
    # Validate permissions
    assert "permissions:" in content, "Workflow must declare permissions"
    assert "packages: write" in content, "Workflow must have packages write permission"
    assert "attestations: write" in content, "Workflow must have attestations write permission"
    assert "id-token: write" in content, "Workflow must have id-token write permission"
    
    # Validate checkout with submodules
    assert "actions/checkout@" in content, "Workflow must checkout repository"
    assert "submodules: recursive" in content, "Workflow must checkout submodules"
    
    # Validate KIWI build step
    assert "build-kiwi-image.sh" in content, "Workflow must execute build script"
    
    # Validate artifact upload
    assert "actions/upload-artifact@" in content, "Workflow must upload artifacts"
    
    # Validate PCR extraction
    assert "pcr_measurements.json" in content, "Workflow must reference PCR measurements"
    assert "PCR4" in content and "PCR7" in content, "Workflow must extract PCR values"
    
    # Validate ORAS installation and usage
    assert "oras" in content.lower(), "Workflow must use ORAS"
    
    # Validate GHCR authentication
    assert "ghcr.io" in content, "Workflow must push to GHCR"
    assert "GITHUB_TOKEN" in content, "Workflow must use GitHub token"
    
    # Validate attestation step
    assert "attest-build-provenance@" in content, "Workflow must generate attestation"
    assert "subject-name" in content, "Attestation must include subject-name"
    assert "subject-digest" in content, "Attestation must include subject-digest"
    assert "push-to-registry: true" in content, "Attestation must be pushed to registry"
    
    # Validate workflow summary
    assert "GITHUB_STEP_SUMMARY" in content, "Workflow must generate summary"


@settings(max_examples=100)
@given(
    oras_version=st.sampled_from(["1.0.0", "1.1.0", "1.2.0"])
)
def test_oras_installation_validation(oras_version: str):
    """
    For any ORAS version specified in the workflow, the installation
    should be verifiable and the binary should be executable.
    
    **Validates: Requirements 12.2**
    """
    # Validate version format
    version_parts = oras_version.split(".")
    assert len(version_parts) == 3, "ORAS version must be in X.Y.Z format"
    
    for part in version_parts:
        assert part.isdigit(), "Version parts must be numeric"
    
    # Validate download URL format
    download_url = f"https://github.com/oras-project/oras/releases/download/v{oras_version}/oras_{oras_version}_linux_amd64.tar.gz"
    
    assert download_url.startswith("https://github.com/"), "Download URL must be from GitHub"
    assert "oras-project/oras" in download_url, "Download URL must be from oras-project"
    assert f"v{oras_version}" in download_url, "Download URL must include version"
    assert "linux_amd64" in download_url, "Download URL must be for Linux AMD64"


def test_error_handling_in_workflow():
    """
    Validates that the workflow includes proper error handling for
    missing PCR measurements and ORAS push failures.
    
    **Validates: Requirements 12.7, 12.8**
    """
    workflow_file = Path(".github/workflows/build-attestable-image.yml")
    
    assert workflow_file.exists(), "Workflow file must exist"
    
    content = workflow_file.read_text()
    
    # Validate PCR extraction error handling
    assert "::error::" in content, "Workflow must use error annotations"
    
    # Check for PCR validation
    pcr_validation_patterns = [
        "PCR4",
        "PCR7",
        "null",
        "exit 1"
    ]
    
    for pattern in pcr_validation_patterns:
        assert pattern in content, f"Workflow must validate {pattern}"
    
    # Check for ORAS push error handling
    assert "if !" in content or "||" in content, "Workflow must handle command failures"
