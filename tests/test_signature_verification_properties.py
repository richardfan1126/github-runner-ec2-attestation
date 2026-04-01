"""
Property-based tests for artifact signature verification.

These tests validate the correctness properties for signature verification,
ensuring artifacts are verified before download and untrusted artifacts are rejected.
"""

import sys
from pathlib import Path
from unittest.mock import Mock, patch, call

import pytest
from hypothesis import given, strategies as st, settings, assume

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

# Import with the actual module name (Python converts hyphens to underscores)
import importlib.util
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)


# Strategy for generating valid artifact references in ghcr.io/owner/repo:tag format
def artifact_ref_strategy():
    """Generate valid GHCR artifact references."""
    return st.builds(
        lambda owner, repo, tag: f"ghcr.io/{owner}/{repo}:{tag}",
        owner=st.from_regex(r"[a-z][a-z0-9\-]{0,19}", fullmatch=True),
        repo=st.from_regex(r"[a-z][a-z0-9\-]{0,19}", fullmatch=True),
        tag=st.from_regex(r"[a-z0-9][a-z0-9.\-]{0,19}", fullmatch=True),
    )


# Property 67: Signature Verification Requirement
# For any AMI conversion attempt, the process should verify artifact signatures
# before downloading artifacts.
# **Validates: Requirements 17.9, 17.10, 17.12**


@settings(max_examples=5)
@given(artifact_ref=artifact_ref_strategy())
def test_signature_verification_called_before_artifact_download(artifact_ref: str):
    """
    Property 67: Signature Verification Requirement

    For any valid artifact reference, verify_artifact_signature must be called
    before pull_artifact_from_ghcr in the main flow. This ensures that only
    verified artifacts are downloaded.

    **Validates: Requirements 17.9, 17.10, 17.12**
    """
    call_order = []

    with patch.object(build_ami, 'parse_arguments') as mock_args, \
         patch.object(build_ami, 'validate_artifact_reference'), \
         patch.object(build_ami, 'validate_aws_region'), \
         patch.object(build_ami, 'validate_output_file_path'), \
         patch.object(build_ami, 'boto3') as mock_boto3, \
         patch.object(build_ami, 'get_user_public_ip', return_value="1.2.3.4"), \
         patch.object(build_ami, 'provision_ami_build_instance', return_value=("i-123", "1.2.3.4", "key")), \
         patch.object(build_ami, 'save_ssh_private_key', return_value="/tmp/key"), \
         patch.object(build_ami, 'wait_for_instance_ready'), \
         patch.object(build_ami, 'verify_ssh_connectivity', return_value=Mock()), \
         patch.object(build_ami, 'install_all_tools'), \
         patch.object(build_ami, 'verify_artifact_signature') as mock_verify, \
         patch.object(build_ami, 'pull_artifact_from_ghcr') as mock_pull, \
         patch.object(build_ami, 'upload_snapshot', return_value="snap-123"), \
         patch.object(build_ami, 'register_ami', return_value="ami-123"), \
         patch.object(build_ami, 'cleanup_infrastructure'), \
         patch.object(build_ami, 'os') as mock_os:

        mock_args.return_value = Mock(
            artifact_ref=artifact_ref,
            region="us-east-1",
            instance_type="m5.large",
            output_file="/tmp/output.json",
        )

        # Track call order
        def track_verify(*args, **kwargs):
            call_order.append("verify_artifact_signature")
            return True
        mock_verify.side_effect = track_verify

        def track_pull(*args, **kwargs):
            call_order.append("pull_artifact_from_ghcr")
            return {"Measurements": {"PCR4": "abc", "PCR7": "def"}}
        mock_pull.side_effect = track_pull

        mock_boto3.client.return_value = Mock()
        mock_os.unlink = Mock()

        # Mock open for writing build result
        from unittest.mock import mock_open
        with patch("builtins.open", mock_open()):
            result = build_ami.main()

        # Verify both were called
        assert "verify_artifact_signature" in call_order, \
            "verify_artifact_signature must be called during main flow"
        assert "pull_artifact_from_ghcr" in call_order, \
            "pull_artifact_from_ghcr must be called during main flow"

        # Verify order: signature verification BEFORE artifact download
        verify_idx = call_order.index("verify_artifact_signature")
        pull_idx = call_order.index("pull_artifact_from_ghcr")
        assert verify_idx < pull_idx, \
            "verify_artifact_signature must be called BEFORE pull_artifact_from_ghcr"


@settings(max_examples=5)
@given(artifact_ref=artifact_ref_strategy())
def test_verify_signature_extracts_owner_repo_and_constructs_commands(artifact_ref: str):
    """
    Property 67: Signature Verification Requirement

    For any valid artifact reference, the verification function should extract
    owner/repo correctly and construct the right commands (oras manifest fetch,
    curl for attestation bundle, gh attestation verify).

    **Validates: Requirements 17.9, 17.10, 17.12**
    """
    mock_ssh_client = Mock()

    # Parse expected owner/repo from artifact_ref
    parts = artifact_ref.replace("ghcr.io/", "").split(":")[0].split("/")
    expected_owner = parts[0]
    expected_repo = parts[1]

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (0, "verification output", "")

        result = build_ami.verify_artifact_signature(mock_ssh_client, artifact_ref)

        assert result is True, "Should return True when exit code is 0"

        # Verify the command was called
        assert mock_execute.called, "execute_remote_command must be called"

        # Get the command that was executed
        cmd = mock_execute.call_args[0][1]

        # Verify the command contains key verification steps
        assert "oras manifest fetch" in cmd, \
            "Command should include oras manifest fetch"
        assert "sha256sum" in cmd, \
            "Command should calculate sha256 digest"
        assert f"api.github.com/repos/{expected_owner}/{expected_repo}/attestations" in cmd, \
            "Command should download attestation from correct GitHub API endpoint"
        assert "gh attestation verify" in cmd, \
            "Command should use gh attestation verify"
        assert "bundle.json" in cmd, \
            "Command should reference bundle.json for offline verification"
        assert "GH_FORCE_TTY=1" in cmd, \
            "Command should set GH_FORCE_TTY=1 environment variable"


@settings(max_examples=5)
@given(exit_code=st.integers(min_value=0, max_value=0))
def test_verify_signature_returns_true_on_success(exit_code: int):
    """
    Property 67: Signature Verification Requirement

    For any successful verification (exit code 0), the function should return True.

    **Validates: Requirements 17.9, 17.10, 17.12**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (exit_code, "verified", "")

        result = build_ami.verify_artifact_signature(
            mock_ssh_client, "ghcr.io/myowner/myrepo:latest"
        )

        assert result is True, \
            "verify_artifact_signature should return True when exit code is 0"


@settings(max_examples=5)
@given(exit_code=st.integers(min_value=1, max_value=255))
def test_verify_signature_returns_false_on_failure(exit_code: int):
    """
    Property 67: Signature Verification Requirement

    For any failed verification (non-zero exit code), the function should return False.

    **Validates: Requirements 17.9, 17.10, 17.12**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (exit_code, "", "verification failed")

        result = build_ami.verify_artifact_signature(
            mock_ssh_client, "ghcr.io/myowner/myrepo:latest"
        )

        assert result is False, \
            "verify_artifact_signature should return False when exit code is non-zero"


# Property 68: Untrusted Artifact Rejection
# For any artifact with invalid or missing attestation, the AMI converter should
# terminate without creating an AMI.
# **Validates: Requirements 17.9, 17.10, 17.12**


@settings(max_examples=5)
@given(artifact_ref=artifact_ref_strategy())
def test_untrusted_artifact_raises_runtime_error(artifact_ref: str):
    """
    Property 68: Untrusted Artifact Rejection

    For any artifact where signature verification fails, the main flow should
    raise RuntimeError("SIGNATURE VERIFICATION FAILED") and NOT proceed to
    pull artifacts or create AMI.

    **Validates: Requirements 17.9, 17.10, 17.12**
    """
    with patch.object(build_ami, 'parse_arguments') as mock_args, \
         patch.object(build_ami, 'validate_artifact_reference'), \
         patch.object(build_ami, 'validate_aws_region'), \
         patch.object(build_ami, 'validate_output_file_path'), \
         patch.object(build_ami, 'boto3') as mock_boto3, \
         patch.object(build_ami, 'get_user_public_ip', return_value="1.2.3.4"), \
         patch.object(build_ami, 'provision_ami_build_instance', return_value=("i-123", "1.2.3.4", "key")), \
         patch.object(build_ami, 'save_ssh_private_key', return_value="/tmp/key"), \
         patch.object(build_ami, 'wait_for_instance_ready'), \
         patch.object(build_ami, 'verify_ssh_connectivity', return_value=Mock()), \
         patch.object(build_ami, 'install_all_tools'), \
         patch.object(build_ami, 'verify_artifact_signature', return_value=False) as mock_verify, \
         patch.object(build_ami, 'pull_artifact_from_ghcr') as mock_pull, \
         patch.object(build_ami, 'upload_snapshot') as mock_upload, \
         patch.object(build_ami, 'register_ami') as mock_register, \
         patch.object(build_ami, 'cleanup_infrastructure'), \
         patch.object(build_ami, 'os') as mock_os:

        mock_args.return_value = Mock(
            artifact_ref=artifact_ref,
            region="us-east-1",
            instance_type="m5.large",
            output_file="/tmp/output.json",
        )
        mock_boto3.client.return_value = Mock()

        # Main should return 1 (failure) due to failed verification
        # The RuntimeError is caught internally and converted to return code 1
        result = build_ami.main()
        assert result == 1, \
            "main() should return 1 when signature verification fails"

        # Verify that pull_artifact_from_ghcr was NOT called
        mock_pull.assert_not_called(), \
            "pull_artifact_from_ghcr must NOT be called when verification fails"

        # Verify that upload_snapshot was NOT called
        mock_upload.assert_not_called(), \
            "upload_snapshot must NOT be called when verification fails"

        # Verify that register_ami was NOT called
        mock_register.assert_not_called(), \
            "register_ami must NOT be called when verification fails"


@settings(max_examples=5)
@given(exit_code=st.integers(min_value=1, max_value=255))
def test_untrusted_artifact_with_various_failure_codes(exit_code: int):
    """
    Property 68: Untrusted Artifact Rejection

    For any non-zero exit code from the verification command, the function
    should return False, causing the main flow to reject the artifact.

    **Validates: Requirements 17.9, 17.10, 17.12**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (exit_code, "", "attestation not found")

        result = build_ami.verify_artifact_signature(
            mock_ssh_client, "ghcr.io/testowner/testrepo:v1.0"
        )

        assert result is False, \
            f"verify_artifact_signature should return False for exit code {exit_code}"


def test_unparseable_artifact_ref_returns_false():
    """
    Property 68: Untrusted Artifact Rejection

    For any artifact reference that cannot be parsed to extract owner/repo,
    the verification function should return False without executing commands.

    **Validates: Requirements 17.9, 17.10, 17.12**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        # Artifact ref with no owner/repo structure
        result = build_ami.verify_artifact_signature(
            mock_ssh_client, "invalid-ref"
        )

        assert result is False, \
            "Should return False for unparseable artifact reference"

        # execute_remote_command should NOT be called for unparseable refs
        mock_execute.assert_not_called(), \
            "Should not execute remote commands for unparseable artifact references"
