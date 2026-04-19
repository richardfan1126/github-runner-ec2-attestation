"""
Property-based tests for build environment pinning.

These tests validate that the build environment is pinned to specific versions
for reproducibility and auditability.
"""

import re
from pathlib import Path

import yaml
from hypothesis import given, settings, strategies as st


# Property 167: Build Environment Pinning
@given(dummy=st.integers(min_value=0, max_value=999))
@settings(max_examples=100)
def test_property_167_github_actions_runner_pinned(dummy: int):
    """
    Property 167: Build Environment Pinning — GitHub Actions runner

    For any check, the GitHub Actions workflow must use a pinned Ubuntu version
    (not ubuntu-latest) for the runs-on field.

    **Validates: Requirements 11.9**
    """
    workflow_path = Path(".github/workflows/build-attestable-image.yml")
    assert workflow_path.exists(), "Workflow file must exist"

    content = yaml.safe_load(workflow_path.read_text())
    jobs = content.get("jobs", {})
    assert jobs, "Workflow must define at least one job"

    for job_name, job_config in jobs.items():
        runs_on = job_config.get("runs-on", "")
        assert runs_on != "ubuntu-latest", (
            f"Job '{job_name}' uses ubuntu-latest; "
            "it must be pinned to a specific version (e.g. ubuntu-24.04)"
        )
        # Verify it looks like a pinned Ubuntu version (e.g. ubuntu-22.04, ubuntu-24.04)
        assert re.match(r"ubuntu-\d+\.\d+", runs_on), (
            f"Job '{job_name}' runs-on value '{runs_on}' does not match "
            "a pinned Ubuntu version pattern (ubuntu-XX.YY)"
        )


@given(dummy=st.integers(min_value=0, max_value=999))
@settings(max_examples=100)
def test_property_167_build_ami_pinned(dummy: int):
    """
    Property 167: Build Environment Pinning — Build Instance AMI

    For any check, the Terraform data source for the Build_Instance AMI must use
    a specific AMI name filter (not a wildcard like al2023-ami-*-x86_64).

    **Validates: Requirements 11.10**
    """
    data_tf_path = Path("terraform/build-ami/data.tf")
    assert data_tf_path.exists(), "Terraform data.tf must exist"

    content = data_tf_path.read_text()

    # Must NOT contain the wildcard AMI filter
    assert "al2023-ami-*-x86_64" not in content, (
        "data.tf must not use wildcard AMI filter 'al2023-ami-*-x86_64'; "
        "pin to a specific AMI release"
    )

    # Must contain a specific pinned AMI name (no glob wildcards in the name value)
    ami_name_match = re.search(
        r'values\s*=\s*\["(al2023-ami-[^"]+)"\]', content
    )
    assert ami_name_match, "data.tf must have an AMI name filter value"
    ami_name = ami_name_match.group(1)
    assert "*" not in ami_name, (
        f"AMI name filter '{ami_name}' contains a wildcard; "
        "it must be pinned to a specific version"
    )


@given(dummy=st.integers(min_value=0, max_value=999))
@settings(max_examples=100)
def test_property_167_dockerfile_dnf_documentation(dummy: int):
    """
    Property 167: Build Environment Pinning — Dockerfile DNF documentation

    The Dockerfile must contain documentation comments about DNF package
    version limitations and why exact NEVRA pinning is not used.

    **Validates: Requirements 11.11, 11.12**
    """
    dockerfile_path = Path(".github/docker/Dockerfile.kiwi-builder")
    assert dockerfile_path.exists(), "Dockerfile must exist"

    content = dockerfile_path.read_text()

    assert "NEVRA" in content, (
        "Dockerfile must document that DNF packages are installed "
        "without explicit NEVRA version pinning"
    )
    assert "releasever" in content, (
        "Dockerfile must suggest using --releasever lock for "
        "full reproducibility"
    )
