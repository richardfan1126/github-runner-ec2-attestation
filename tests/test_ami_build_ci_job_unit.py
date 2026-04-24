"""
Unit tests for the `build-ami` job structure in the GitHub Actions workflow.

Parses `.github/workflows/build-attestable-image.yml` with PyYAML and asserts
that the `build-ami` job is correctly configured.

Requirements: 1.1, 2.1, 2.2, 3.1, 3.2, 3.4, 4.2, 5.3, 5.4, 5.5,
              6.1, 6.2, 7.2, 8.1, 8.2, 8.3, 8.4, 9.1, 9.2
"""

import re
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
WORKFLOW_FILE = REPO_ROOT / ".github" / "workflows" / "build-attestable-image.yml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict:
    """Load and parse the build-attestable-image.yml workflow."""
    assert WORKFLOW_FILE.exists(), f"Workflow file not found: {WORKFLOW_FILE}"
    with open(WORKFLOW_FILE) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def build_ami_job(workflow) -> dict:
    """Return the `build-ami` job dict from the workflow."""
    jobs = workflow.get("jobs", {})
    assert "build-ami" in jobs, (
        "The workflow must contain a 'build-ami' job"
    )
    return jobs["build-ami"]


@pytest.fixture(scope="module")
def build_ami_steps(build_ami_job) -> list:
    """Return the steps list from the `build-ami` job."""
    steps = build_ami_job.get("steps", [])
    assert steps, "The 'build-ami' job must have at least one step"
    return steps


def _find_step(steps: list, *, uses_prefix: str | None = None, name_contains: str | None = None) -> dict | None:
    """Return the first step matching the given criteria, or None."""
    for step in steps:
        if uses_prefix and step.get("uses", "").startswith(uses_prefix):
            return step
        if name_contains and name_contains.lower() in step.get("name", "").lower():
            return step
    return None


def _all_step_text(steps: list) -> str:
    """Concatenate all 'run' scripts and 'with' values from all steps into one string."""
    parts = []
    for step in steps:
        if "run" in step:
            parts.append(step["run"])
        if "with" in step:
            for v in step["with"].values():
                parts.append(str(v))
    return "\n".join(parts)


# ===========================================================================
# Requirement 1.1 — job dependency
# ===========================================================================


class TestJobDependency:
    """Requirement 1.1: build-ami must declare needs: build-and-publish."""

    def test_needs_build_and_publish(self, build_ami_job):
        """build-ami job must declare needs: build-and-publish."""
        needs = build_ami_job.get("needs")
        # `needs` may be a string or a list
        if isinstance(needs, list):
            assert "build-and-publish" in needs, (
                f"build-ami.needs must include 'build-and-publish', got {needs!r}"
            )
        else:
            assert needs == "build-and-publish", (
                f"build-ami.needs must be 'build-and-publish', got {needs!r}"
            )


# ===========================================================================
# Requirement 2.1 — runner
# ===========================================================================


class TestRunner:
    """Requirement 2.1: build-ami must run on ubuntu-24.04."""

    def test_runs_on_ubuntu_24_04(self, build_ami_job):
        """build-ami job must use runs-on: ubuntu-24.04."""
        runs_on = build_ami_job.get("runs-on")
        assert runs_on == "ubuntu-24.04", (
            f"build-ami.runs-on must be 'ubuntu-24.04', got {runs_on!r}"
        )


# ===========================================================================
# Requirement 2.2 — checkout step
# ===========================================================================


class TestCheckoutStep:
    """Requirement 2.2: first step must be actions/checkout@v4 with submodules: recursive."""

    def test_first_step_is_checkout(self, build_ami_steps):
        """The first step must use actions/checkout@v4."""
        first = build_ami_steps[0]
        uses = first.get("uses", "")
        assert uses.startswith("actions/checkout@v4"), (
            f"First step must use actions/checkout@v4, got {uses!r}"
        )

    def test_checkout_has_submodules_recursive(self, build_ami_steps):
        """The checkout step must set submodules: recursive."""
        first = build_ami_steps[0]
        with_block = first.get("with", {})
        assert with_block.get("submodules") == "recursive", (
            f"Checkout step must have submodules: recursive, got {with_block!r}"
        )


# ===========================================================================
# Requirements 3.1, 3.2, 3.4 — AWS credentials via OIDC
# ===========================================================================


class TestAwsCredentialsStep:
    """Requirements 3.1, 3.2, 3.4: AWS credentials via OIDC, no long-lived keys."""

    def test_configure_aws_credentials_step_present(self, build_ami_steps):
        """A step using aws-actions/configure-aws-credentials must be present."""
        step = _find_step(build_ami_steps, uses_prefix="aws-actions/configure-aws-credentials")
        assert step is not None, (
            "A step using aws-actions/configure-aws-credentials must be present"
        )

    def test_role_to_assume_uses_vars_aws_role_arn(self, build_ami_steps):
        """configure-aws-credentials must use role-to-assume: ${{ vars.AWS_ROLE_ARN }}."""
        step = _find_step(build_ami_steps, uses_prefix="aws-actions/configure-aws-credentials")
        assert step is not None, "aws-actions/configure-aws-credentials step not found"
        role = step.get("with", {}).get("role-to-assume", "")
        assert "vars.AWS_ROLE_ARN" in role, (
            f"role-to-assume must reference vars.AWS_ROLE_ARN, got {role!r}"
        )

    def test_no_aws_access_key_id_anywhere(self, build_ami_steps):
        """aws-access-key-id must not appear in any step."""
        full_text = _all_step_text(build_ami_steps)
        assert "aws-access-key-id" not in full_text, (
            "aws-access-key-id must not appear in any step of the build-ami job"
        )

    def test_no_aws_secret_access_key_anywhere(self, build_ami_steps):
        """aws-secret-access-key must not appear in any step."""
        full_text = _all_step_text(build_ami_steps)
        assert "aws-secret-access-key" not in full_text, (
            "aws-secret-access-key must not appear in any step of the build-ami job"
        )


# ===========================================================================
# Requirements 9.1, 9.2 — Terraform setup
# ===========================================================================


class TestTerraformSetupStep:
    """Requirements 9.1, 9.2: hashicorp/setup-terraform with a pinned version."""

    def test_setup_terraform_step_present(self, build_ami_steps):
        """A step using hashicorp/setup-terraform must be present."""
        step = _find_step(build_ami_steps, uses_prefix="hashicorp/setup-terraform")
        assert step is not None, (
            "A step using hashicorp/setup-terraform must be present"
        )

    def test_terraform_version_is_pinned(self, build_ami_steps):
        """hashicorp/setup-terraform must specify a pinned terraform_version."""
        step = _find_step(build_ami_steps, uses_prefix="hashicorp/setup-terraform")
        assert step is not None, "hashicorp/setup-terraform step not found"
        version = step.get("with", {}).get("terraform_version", "")
        assert version, (
            "hashicorp/setup-terraform must specify terraform_version"
        )
        # A pinned version looks like X.Y.Z (no wildcards, no ranges)
        assert re.fullmatch(r"\d+\.\d+\.\d+", str(version).strip('"').strip("'")), (
            f"terraform_version must be a pinned semver (e.g. '1.12.2'), got {version!r}"
        )


# ===========================================================================
# Requirements 4.2, 5.3, 5.4, 5.5 — script invocation
# ===========================================================================


class TestScriptInvocationStep:
    """Requirements 4.2, 5.3, 5.4, 5.5: correct script invocation flags."""

    def _script_step(self, steps: list) -> dict:
        """Return the step that invokes build-ami.py."""
        for step in steps:
            run = step.get("run", "")
            if "build-ami.py" in run:
                return step
        pytest.fail("No step invoking scripts/build-ami.py found in build-ami job")

    def test_script_invoked_via_uv_run(self, build_ami_steps):
        """The script must be invoked via uv run python scripts/build-ami.py."""
        step = self._script_step(build_ami_steps)
        assert "uv run python scripts/build-ami.py" in step["run"], (
            "Script must be invoked as 'uv run python scripts/build-ami.py'"
        )

    def test_output_file_flag_present(self, build_ami_steps):
        """The script invocation must include --output-file ami_build_result.json."""
        step = self._script_step(build_ami_steps)
        assert "--output-file ami_build_result.json" in step["run"], (
            "Script invocation must include '--output-file ami_build_result.json'"
        )

    def test_expected_workflow_flag_present(self, build_ami_steps):
        """The script invocation must include --expected-workflow pointing to the workflow file."""
        step = self._script_step(build_ami_steps)
        assert "--expected-workflow .github/workflows/build-attestable-image.yml" in step["run"], (
            "Script invocation must include "
            "'--expected-workflow .github/workflows/build-attestable-image.yml'"
        )

    def test_allow_debug_flag_absent(self, build_ami_steps):
        """The script invocation must NOT include --allow-debug."""
        step = self._script_step(build_ami_steps)
        assert "--allow-debug" not in step["run"], (
            "Script invocation must NOT include '--allow-debug' on production builds"
        )


# ===========================================================================
# Requirements 6.1, 6.2 — artifact upload
# ===========================================================================


class TestArtifactUploadStep:
    """Requirements 6.1, 6.2: upload ami_build_result.json on success, 90-day retention."""

    def _upload_step(self, steps: list) -> dict:
        """Return the upload-artifact step."""
        step = _find_step(steps, uses_prefix="actions/upload-artifact")
        assert step is not None, (
            "A step using actions/upload-artifact must be present in the build-ami job"
        )
        return step

    def test_upload_step_has_if_success(self, build_ami_steps):
        """The upload step must have if: success()."""
        step = self._upload_step(build_ami_steps)
        condition = step.get("if", "")
        assert "success()" in str(condition), (
            f"Upload step must have 'if: success()', got {condition!r}"
        )

    def test_upload_artifact_name_is_ami_build_result(self, build_ami_steps):
        """The artifact name must be 'ami-build-result'."""
        step = self._upload_step(build_ami_steps)
        name = step.get("with", {}).get("name", "")
        assert name == "ami-build-result", (
            f"Artifact name must be 'ami-build-result', got {name!r}"
        )

    def test_upload_retention_days_is_90(self, build_ami_steps):
        """The artifact retention-days must be 90."""
        step = self._upload_step(build_ami_steps)
        retention = step.get("with", {}).get("retention-days")
        assert int(retention) == 90, (
            f"retention-days must be 90, got {retention!r}"
        )


# ===========================================================================
# Requirement 7.2 — failure summary step
# ===========================================================================


class TestFailureSummaryStep:
    """Requirement 7.2: a failure summary step must exist with if: failure()."""

    def test_failure_summary_step_present(self, build_ami_steps):
        """A step with if: failure() must be present."""
        failure_steps = [
            s for s in build_ami_steps
            if "failure()" in str(s.get("if", ""))
        ]
        assert failure_steps, (
            "At least one step with 'if: failure()' must be present in the build-ami job"
        )

    def test_failure_summary_writes_to_github_step_summary(self, build_ami_steps):
        """The failure step must write to $GITHUB_STEP_SUMMARY."""
        failure_steps = [
            s for s in build_ami_steps
            if "failure()" in str(s.get("if", ""))
        ]
        assert failure_steps, "No failure step found"
        failure_step = failure_steps[0]
        run_script = failure_step.get("run", "")
        assert "GITHUB_STEP_SUMMARY" in run_script, (
            "The failure summary step must write to $GITHUB_STEP_SUMMARY"
        )


# ===========================================================================
# Requirements 8.1, 8.2, 8.3, 8.4 — job-level permissions
# ===========================================================================


class TestJobPermissions:
    """Requirements 8.1–8.4: minimum required permissions, no excess permissions."""

    def test_id_token_write_present(self, build_ami_job):
        """Job permissions must include id-token: write."""
        perms = build_ami_job.get("permissions", {})
        assert perms.get("id-token") == "write", (
            f"build-ami permissions must include 'id-token: write', got {perms!r}"
        )

    def test_contents_read_present(self, build_ami_job):
        """Job permissions must include contents: read."""
        perms = build_ami_job.get("permissions", {})
        assert perms.get("contents") == "read", (
            f"build-ami permissions must include 'contents: read', got {perms!r}"
        )

    def test_packages_read_present(self, build_ami_job):
        """Job permissions must include packages: read."""
        perms = build_ami_job.get("permissions", {})
        assert perms.get("packages") == "read", (
            f"build-ami permissions must include 'packages: read', got {perms!r}"
        )

    def test_attestations_write_absent(self, build_ami_job):
        """Job permissions must NOT include attestations: write."""
        perms = build_ami_job.get("permissions", {})
        assert "attestations" not in perms, (
            f"build-ami permissions must NOT include 'attestations', got {perms!r}"
        )

    def test_packages_write_absent(self, build_ami_job):
        """Job permissions must NOT include packages: write."""
        perms = build_ami_job.get("permissions", {})
        assert perms.get("packages") != "write", (
            f"build-ami permissions must NOT include 'packages: write', got {perms!r}"
        )
