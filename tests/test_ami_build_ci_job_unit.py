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
    """Requirement 1.1: build-ami must declare needs: build-flavor-image."""

    def test_needs_build_flavor_image(self, build_ami_job):
        """build-ami job must declare needs on the single producer build-flavor-image."""
        needs = build_ami_job.get("needs")
        # `needs` may be a string or a list
        if isinstance(needs, list):
            assert "build-flavor-image" in needs, (
                f"build-ami.needs must include 'build-flavor-image', got {needs!r}"
            )
        else:
            assert needs == "build-flavor-image", (
                f"build-ami.needs must be 'build-flavor-image', got {needs!r}"
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
    """Requirement 2.2: first step must be actions/checkout (SHA-pinned) with submodules: recursive."""

    def test_first_step_is_checkout(self, build_ami_steps):
        """The first step must use actions/checkout pinned to a commit SHA."""
        first = build_ami_steps[0]
        uses = first.get("uses", "")
        assert uses.startswith("actions/checkout@"), (
            f"First step must use actions/checkout, got {uses!r}"
        )
        # After 184.7, actions must be pinned to a 40-char hex SHA
        ref = uses.split("@", 1)[1]
        import re
        assert re.match(r"^[0-9a-f]{40}$", ref), (
            f"actions/checkout must be pinned to a full commit SHA, got {ref!r}"
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


class TestJobCondition:
    """Requirement 1.1, 1.5: build-ami job if condition allows main and workflow_dispatch."""

    def test_if_condition_enforces_develop_skip(self, build_ami_job):
        """The build-ami job if: condition must enforce the develop-skip rule (main or workflow_dispatch)."""
        condition = str(build_ami_job.get("if", ""))
        assert "github.ref == 'refs/heads/main'" in condition, (
            f"build-ami if: condition must check for main branch, got {condition!r}"
        )
        assert "github.event_name == 'workflow_dispatch'" in condition, (
            f"build-ami if: condition must allow workflow_dispatch, got {condition!r}"
        )

    def test_if_condition_does_not_use_always(self, build_ami_job):
        """With a single producer job, build-ami resolves through ordinary `needs`.

        It must NOT use always() (nor hand-written upstream-result booleans): there is
        no conditionally-skipped sibling producer to tolerate, so always() would only
        reintroduce the transitive-skip contagion this change removes.
        """
        condition = str(build_ami_job.get("if", ""))
        assert "always()" not in condition, (
            f"build-ami if: condition must NOT include always() (single producer → ordinary needs), got {condition!r}"
        )
        assert ".result" not in condition, (
            f"build-ami if: condition must NOT hand-check upstream .result, got {condition!r}"
        )

    def test_if_condition_checks_has_ami_builds(self, build_ami_job):
        """The if: condition must gate on has_ami_builds to skip when no flavors need AMI registration."""
        condition = str(build_ami_job.get("if", ""))
        assert "has_ami_builds" in condition, (
            f"build-ami if: condition must check has_ami_builds, got {condition!r}"
        )


class TestScriptInvocationStep:
    """Requirements 4.2, 5.3, 5.4, 5.5, 5.6: correct script invocation flags."""

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

    def test_single_instance_invocation_flags(self, build_ami_steps):
        """The single-instance invocation must pass host, ssh key, run-id, manifest,
        and output-dir — and must NOT use the removed single-flavor --output-file /
        --artifact-ref flags."""
        step = self._script_step(build_ami_steps)
        run = step["run"]
        for flag in ("--host", "--ssh-key-path", "--run-id",
                     "--flavors-manifest", "--output-dir"):
            assert flag in run, f"Script invocation must include '{flag}'"
        assert "--output-file" not in run, (
            "Single-instance invocation must not use the removed --output-file flag"
        )
        assert "--artifact-ref" not in run, (
            "Single-instance invocation must not use the removed --artifact-ref flag"
        )

    def test_expected_workflow_flag_present(self, build_ami_steps):
        """The script invocation must include --expected-workflow pointing to the workflow file."""
        step = self._script_step(build_ami_steps)
        assert "--expected-workflow .github/workflows/build-attestable-image.yml" in step["run"], (
            "Script invocation must include "
            "'--expected-workflow .github/workflows/build-attestable-image.yml'"
        )

    def test_allow_debug_flag_conditional_logic(self, build_ami_steps):
        """The script step must contain conditional shell logic for ALLOW_DEBUG_FLAG."""
        step = self._script_step(build_ami_steps)
        run_script = step["run"]
        assert "ALLOW_DEBUG_FLAG" in run_script, (
            "Script step must contain ALLOW_DEBUG_FLAG shell variable logic"
        )
        assert 'if [ "${{ github.event_name }}" = "workflow_dispatch" ] && [ "${{ inputs.enable_ssh }}" = "true" ]' in run_script, (
            "Script step must contain the conditional check for workflow_dispatch and enable_ssh"
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

    def test_upload_step_runs_always(self, build_ami_steps):
        """The upload step must run with if: always().

        On the shared single instance a flavor can fail in isolation while others
        succeed (D8); uploading results only on overall success() would drop the
        successful flavors' results whenever any flavor failed. always() preserves
        per-flavor failure isolation into update-flavors-lock's carry-forward.
        """
        step = self._upload_step(build_ami_steps)
        condition = step.get("if", "")
        assert "always()" in str(condition), (
            f"Upload step must have 'if: always()', got {condition!r}"
        )

    def test_upload_artifact_name_is_ami_build_result(self, build_ami_steps):
        """The artifact name must start with 'ami-build-result' (a single combined
        artifact whose internal layout is ami-build-result-<flavor>/...)."""
        step = self._upload_step(build_ami_steps)
        name = step.get("with", {}).get("name", "")
        assert name.startswith("ami-build-result"), (
            f"Artifact name must start with 'ami-build-result', got {name!r}"
        )

    def test_upload_retention_days_is_90(self, build_ami_steps):
        """The artifact retention-days must be 90."""
        step = self._upload_step(build_ami_steps)
        retention = step.get("with", {}).get("retention-days")
        assert int(retention) == 90, (
            f"retention-days must be 90, got {retention!r}"
        )


# ===========================================================================
# Requirement 7.2 — build summary step (single-job shape)
# ===========================================================================


def _summary_step(steps: list) -> dict | None:
    """Return the always() summary step that writes GITHUB_STEP_SUMMARY."""
    for step in steps:
        if "always()" in str(step.get("if", "")) and "GITHUB_STEP_SUMMARY" in step.get("run", ""):
            return step
    return None


class TestBuildSummaryStep:
    """Requirement 7.2: an always() summary reports per-flavor results.

    The old per-flavor matrix had separate success/failure summary steps. The
    single job builds all flavors and reports them in one always() summary that
    also handles the no-results case, so a failed flavor does not hide the others.
    """

    def test_summary_step_present_and_always(self, build_ami_steps):
        """An always() step writing to $GITHUB_STEP_SUMMARY must be present."""
        step = _summary_step(build_ami_steps)
        assert step is not None, (
            "An always() summary step writing $GITHUB_STEP_SUMMARY must exist"
        )

    def test_summary_handles_no_results(self, build_ami_steps):
        """The summary must handle the case where no flavor produced a result."""
        step = _summary_step(build_ami_steps)
        assert step is not None, "No summary step found"
        run = step.get("run", "")
        # Reads the per-flavor result files the driver writes.
        assert "ami_build_result-" in run, (
            "Summary must reference the per-flavor ami_build_result-<flavor>.json files"
        )


# ===========================================================================
# Requirement 7.3 — debug warning (folded into the always() summary)
# ===========================================================================


class TestDebugWarning:
    """Requirement 7.3: a debug warning is emitted for workflow_dispatch enable_ssh.

    In the single job this is shell-gated inside the always() summary rather than a
    separate step-level `if:`.
    """

    def test_debug_warning_present_in_summary(self, build_ami_steps):
        """The summary must contain shell logic warning about debug (enable_ssh) builds."""
        step = _summary_step(build_ami_steps)
        assert step is not None, "No summary step found"
        run = step.get("run", "")
        assert "inputs.enable_ssh" in run, (
            "Summary must gate the debug warning on inputs.enable_ssh"
        )
        assert "WARNING" in run and "debug" in run.lower(), (
            "Summary must emit a debug-build WARNING"
        )


# ===========================================================================
# Single-instance lifecycle: timeout, provision (apply), teardown (destroy)
# ===========================================================================


class TestSingleInstanceLifecycle:
    """The single job owns the Terraform apply/destroy lifecycle (D1/D2/D3)."""

    def test_no_matrix(self, build_ami_job):
        """build-ami must NOT be a per-flavor matrix any more (single shared instance)."""
        strategy = build_ami_job.get("strategy", {})
        assert "matrix" not in strategy, (
            "build-ami must not use a per-flavor matrix (it is one shared instance now)"
        )

    def test_explicit_timeout_minutes(self, build_ami_job):
        """The job must set an explicit timeout-minutes well under GitHub's 6 h default."""
        tmo = build_ami_job.get("timeout-minutes")
        assert tmo is not None, "build-ami must set an explicit timeout-minutes"
        assert int(tmo) < 360, f"timeout-minutes must be well under 360, got {tmo!r}"

    def _provision_step(self, steps: list) -> dict | None:
        for step in steps:
            run = step.get("run", "")
            if "terraform apply" in run:
                return step
        return None

    def _destroy_step(self, steps: list) -> dict | None:
        for step in steps:
            run = step.get("run", "")
            if "terraform destroy" in run:
                return step
        return None

    def test_provision_step_passes_run_id_and_right_sized_instance(self, build_ami_steps):
        """The apply step must pass run_id and instance_type=c5.4xlarge (D9/D11)."""
        step = self._provision_step(build_ami_steps)
        assert step is not None, "A terraform apply provisioning step must be present"
        run = step["run"]
        assert "run_id=" in run, "terraform apply must pass the run_id var"
        assert "c5.4xlarge" in run, "terraform apply must right-size to c5.4xlarge"

    def test_destroy_step_is_always_and_retries(self, build_ami_steps):
        """The teardown must be an always() terraform destroy that retries and passes run_id."""
        step = self._destroy_step(build_ami_steps)
        assert step is not None, "An always() terraform destroy step must be present"
        assert "always()" in str(step.get("if", "")), (
            "terraform destroy step must run with if: always()"
        )
        run = step["run"]
        assert "run_id=" in run, "terraform destroy must pass the identical run_id var"
        # Retry-with-backoff + fail-loud: a loop and a non-zero exit on non-convergence.
        assert "for attempt" in run and "exit 1" in run, (
            "destroy must retry with backoff and fail loud if it cannot converge"
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
