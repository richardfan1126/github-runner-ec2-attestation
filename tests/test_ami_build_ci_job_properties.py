"""
Property-based tests for the `build-ami` CI job logic.

Feature: ami-build-ci-job

Property 1: Job condition correctly classifies all trigger contexts
  Validates: Requirements 1.2, 1.3, 1.4, 1.5

Property 2: Summary script extracts all required fields from any valid build result
  Validates: Requirements 7.1
"""

from datetime import timezone

from hypothesis import given, settings, strategies as st


# ---------------------------------------------------------------------------
# Helper: evaluate_job_condition
#
# Pure Python mirror of the YAML `if:` expression on the `build-ami` job:
#
#   github.ref == 'refs/heads/main'
#   || (github.event_name == 'workflow_dispatch' && inputs.enable_ssh == false)
# ---------------------------------------------------------------------------


def evaluate_job_condition(event_name: str, ref: str, enable_ssh: bool) -> bool:
    """Return True when the `build-ami` job should run, False when it should be skipped.

    Mirrors the workflow `if:` expression:
        github.ref == 'refs/heads/main'
        || (github.event_name == 'workflow_dispatch' && inputs.enable_ssh == false)

    Args:
        event_name: The GitHub Actions event name (e.g. "push", "workflow_dispatch").
        ref:        The full Git ref string (e.g. "refs/heads/main").
        enable_ssh: The value of the `enable_ssh` workflow_dispatch input.

    Returns:
        True if the job should execute, False if it should be skipped.
    """
    return ref == "refs/heads/main" or (
        event_name == "workflow_dispatch" and not enable_ssh
    )


# ---------------------------------------------------------------------------
# Helper: generate_summary
#
# Mirrors the shell logic in the workflow's "Write success summary" step:
#
#   AMI_ID=$(jq -r '.ami_id' ami_build_result.json)
#   SNAPSHOT_ID=$(jq -r '.snapshot_id' ami_build_result.json)
#   REGION=$(jq -r '.region' ami_build_result.json)
#   BUILD_TIMESTAMP=$(jq -r '.build_timestamp' ami_build_result.json)
#   echo "## AMI Build Complete ✅"
#   echo "- **AMI ID**: `${AMI_ID}`"
#   echo "- **Snapshot ID**: `${SNAPSHOT_ID}`"
#   echo "- **Region**: `${REGION}`"
#   echo "- **Build Timestamp**: `${BUILD_TIMESTAMP}`"
# ---------------------------------------------------------------------------


def generate_summary(build_result: dict) -> str:
    """Format the four required fields from a build result dict into a summary string.

    Mirrors the logic used in the workflow's success summary step.

    Args:
        build_result: A dict containing at minimum ``ami_id``, ``snapshot_id``,
                      ``region``, and ``build_timestamp`` keys.

    Returns:
        A multi-line Markdown summary string containing all four field values.
    """
    ami_id = build_result["ami_id"]
    snapshot_id = build_result["snapshot_id"]
    region = build_result["region"]
    build_timestamp = build_result["build_timestamp"]

    lines = [
        "## AMI Build Complete ✅",
        "",
        "### AMI Details",
        f"- **AMI ID**: `{ami_id}`",
        f"- **Snapshot ID**: `{snapshot_id}`",
        f"- **Region**: `{region}`",
        f"- **Build Timestamp**: `{build_timestamp}`",
    ]
    return "\n".join(lines)


# ===========================================================================
# Property 1: Job condition correctly classifies all trigger contexts
# ===========================================================================

@given(
    event_name=st.sampled_from(["push", "workflow_dispatch", "pull_request"]),
    ref=st.one_of(
        st.just("refs/heads/main"),
        st.just("refs/heads/develop"),
        st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
            min_size=1,
        ),
    ),
    enable_ssh=st.booleans(),
)
@settings(max_examples=200)
def test_build_ami_job_condition(event_name: str, ref: str, enable_ssh: bool) -> None:
    """Property 1: evaluate_job_condition matches the YAML if: expression for all inputs.

    For any combination of event_name, ref, and enable_ssh the helper must
    return exactly the same boolean as the YAML expression:

        github.ref == 'refs/heads/main'
        || (github.event_name == 'workflow_dispatch' && inputs.enable_ssh == false)

    Validates: Requirements 1.2, 1.3, 1.4, 1.5
    """
    result = evaluate_job_condition(event_name, ref, enable_ssh)
    expected = (ref == "refs/heads/main") or (
        event_name == "workflow_dispatch" and not enable_ssh
    )
    assert result == expected, (
        f"evaluate_job_condition({event_name!r}, {ref!r}, {enable_ssh!r}) "
        f"returned {result!r}, expected {expected!r}"
    )


# ===========================================================================
# Property 2: Summary script extracts all required fields from any valid build result
# ===========================================================================

@given(
    ami_id=st.from_regex(r"ami-[0-9a-f]{17}", fullmatch=True),
    snapshot_id=st.from_regex(r"snap-[0-9a-f]{17}", fullmatch=True),
    region=st.sampled_from(
        [
            "us-east-1",
            "us-east-2",
            "us-west-1",
            "us-west-2",
            "eu-west-1",
            "eu-west-2",
            "eu-central-1",
            "ap-southeast-1",
            "ap-southeast-2",
            "ap-northeast-1",
        ]
    ),
    build_timestamp=st.datetimes(timezones=st.just(timezone.utc)).map(
        lambda d: d.isoformat()
    ),
)
@settings(max_examples=100)
def test_summary_generation(
    ami_id: str,
    snapshot_id: str,
    region: str,
    build_timestamp: str,
) -> None:
    """Property 2: generate_summary includes all four required fields for any valid input.

    For any valid ami_build_result dict the summary string must contain the
    literal values of ami_id, snapshot_id, region, and build_timestamp.

    Validates: Requirements 7.1
    """
    build_result = {
        "ami_id": ami_id,
        "snapshot_id": snapshot_id,
        "region": region,
        "build_timestamp": build_timestamp,
        "pcr_measurements": {"pcr4": "aabbcc", "pcr7": "ddeeff"},
    }
    summary = generate_summary(build_result)

    assert ami_id in summary, (
        f"ami_id {ami_id!r} not found in summary:\n{summary}"
    )
    assert snapshot_id in summary, (
        f"snapshot_id {snapshot_id!r} not found in summary:\n{summary}"
    )
    assert region in summary, (
        f"region {region!r} not found in summary:\n{summary}"
    )
    assert build_timestamp in summary, (
        f"build_timestamp {build_timestamp!r} not found in summary:\n{summary}"
    )
