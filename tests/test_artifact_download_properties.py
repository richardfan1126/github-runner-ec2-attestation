"""
Property-based tests for artifact download and validation.

These tests validate the correctness properties for artifact download,
file validation, and PCR measurements parsing.
"""

import json
import re
import sys
from pathlib import Path
from unittest.mock import Mock, patch

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


# Strategy for generating valid hex strings (like SHA-384 hashes used for PCR values)
def hex_string_strategy(min_size=1, max_size=96):
    """Generate valid hex strings."""
    return st.text(
        alphabet="0123456789abcdefABCDEF",
        min_size=min_size,
        max_size=max_size,
    )


# Strategy for generating valid artifact references
def artifact_ref_strategy():
    """Generate valid GHCR artifact references."""
    return st.builds(
        lambda owner, repo, tag: f"ghcr.io/{owner}/{repo}:{tag}",
        owner=st.from_regex(r"[a-z][a-z0-9\-]{0,19}", fullmatch=True),
        repo=st.from_regex(r"[a-z][a-z0-9\-]{0,19}", fullmatch=True),
        tag=st.from_regex(r"[a-z0-9][a-z0-9.\-]{0,19}", fullmatch=True),
    )


# Property 71: Artifact Download Completeness
# For any artifact download, both the raw disk image and pcr_measurements.json
# should be present in the expected directory.
# **Validates: Requirements 18.4, 18.5**


@settings(max_examples=100)
@given(
    raw_filename=st.from_regex(r"[a-z][a-z0-9\-]{0,19}\.raw", fullmatch=True),
    artifact_ref=artifact_ref_strategy(),
)
def test_artifact_download_completeness_both_files_present(raw_filename, artifact_ref):
    """
    Property 71: Artifact Download Completeness

    For any successful artifact download, validate_artifact_files should succeed
    when both the raw disk image and pcr_measurements.json are present.

    **Validates: Requirements 18.4, 18.5**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        def side_effect(ssh_client, command, stream_output=True):
            if "ls ~/artifacts/build-output/*.raw" in command:
                return (0, raw_filename, "")
            if "test -f ~/artifacts/build-output/pcr_measurements.json" in command:
                return (0, "", "")
            return (0, "", "")

        mock_execute.side_effect = side_effect

        # Should not raise when both files are present
        build_ami.validate_artifact_files(mock_ssh_client)

        # Verify both checks were performed
        calls = [call[0][1] for call in mock_execute.call_args_list]
        assert any("*.raw" in cmd for cmd in calls), \
            "Should check for raw disk image"
        assert any("pcr_measurements.json" in cmd for cmd in calls), \
            "Should check for pcr_measurements.json"


@settings(max_examples=100)
@given(artifact_ref=artifact_ref_strategy())
def test_artifact_download_completeness_missing_raw_image(artifact_ref):
    """
    Property 71: Artifact Download Completeness

    For any artifact download where the raw disk image is missing,
    validate_artifact_files should raise RuntimeError.

    **Validates: Requirements 18.4, 18.5**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        def side_effect(ssh_client, command, stream_output=True):
            if "*.raw" in command:
                return (1, "", "No such file")
            if "pcr_measurements.json" in command:
                return (0, "", "")
            return (0, "", "")

        mock_execute.side_effect = side_effect

        with pytest.raises(RuntimeError) as exc_info:
            build_ami.validate_artifact_files(mock_ssh_client)

        assert "raw" in str(exc_info.value).lower() or "Raw" in str(exc_info.value), \
            "Error should mention missing raw disk image"


@settings(max_examples=100)
@given(artifact_ref=artifact_ref_strategy())
def test_artifact_download_completeness_missing_pcr_measurements(artifact_ref):
    """
    Property 71: Artifact Download Completeness

    For any artifact download where pcr_measurements.json is missing,
    validate_artifact_files should raise RuntimeError.

    **Validates: Requirements 18.4, 18.5**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        def side_effect(ssh_client, command, stream_output=True):
            if "*.raw" in command:
                return (0, "image.raw", "")
            if "pcr_measurements.json" in command:
                return (1, "", "No such file")
            return (0, "", "")

        mock_execute.side_effect = side_effect

        with pytest.raises(RuntimeError) as exc_info:
            build_ami.validate_artifact_files(mock_ssh_client)

        assert "pcr_measurements" in str(exc_info.value), \
            "Error should mention missing pcr_measurements.json"


# Property 72: PCR Measurements Round-Trip
# For any artifact with PCR measurements, the PCR values in the artifact
# annotations should match the values in the downloaded pcr_measurements.json file.
# **Validates: Requirements 18.7**


@settings(max_examples=100)
@given(
    pcr4=hex_string_strategy(min_size=2, max_size=96),
    pcr7=hex_string_strategy(min_size=2, max_size=96),
)
def test_pcr_measurements_round_trip(pcr4, pcr7):
    """
    Property 72: PCR Measurements Round-Trip

    For any valid PCR measurements JSON, validate_pcr_measurements should
    parse and return the same PCR4 and PCR7 values that were in the file.

    **Validates: Requirements 18.7**
    """
    mock_ssh_client = Mock()

    pcr_json = json.dumps({
        "Measurements": {
            "PCR4": pcr4,
            "PCR7": pcr7,
        }
    })

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (0, pcr_json, "")

        result = build_ami.validate_pcr_measurements(mock_ssh_client)

        # Verify round-trip: returned values match input
        assert result['Measurements']['PCR4'] == pcr4, \
            f"PCR4 should round-trip: expected {pcr4}, got {result['Measurements']['PCR4']}"
        assert result['Measurements']['PCR7'] == pcr7, \
            f"PCR7 should round-trip: expected {pcr7}, got {result['Measurements']['PCR7']}"


@settings(max_examples=100)
@given(
    invalid_json=st.text(min_size=1, max_size=50).filter(
        lambda s: not s.strip().startswith('{')
    ),
)
def test_pcr_measurements_invalid_json_raises_error(invalid_json):
    """
    Property 72: PCR Measurements Round-Trip

    For any non-JSON content in pcr_measurements.json, validate_pcr_measurements
    should raise RuntimeError.

    **Validates: Requirements 18.7**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (0, invalid_json, "")

        with pytest.raises(RuntimeError) as exc_info:
            build_ami.validate_pcr_measurements(mock_ssh_client)

        assert "parsing" in str(exc_info.value).lower() or "Failed" in str(exc_info.value), \
            "Error should indicate parsing failure"


@settings(max_examples=100)
@given(
    pcr4=st.text(min_size=1, max_size=20).filter(
        lambda s: not re.match(r'^[0-9a-fA-F]+$', s)
    ),
    pcr7=hex_string_strategy(min_size=2, max_size=96),
)
def test_pcr_measurements_invalid_hex_pcr4_raises_error(pcr4, pcr7):
    """
    Property 72: PCR Measurements Round-Trip

    For any PCR4 value that is not a valid hex string, validate_pcr_measurements
    should raise RuntimeError.

    **Validates: Requirements 18.7**
    """
    mock_ssh_client = Mock()

    pcr_json = json.dumps({
        "Measurements": {
            "PCR4": pcr4,
            "PCR7": pcr7,
        }
    })

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (0, pcr_json, "")

        with pytest.raises(RuntimeError) as exc_info:
            build_ami.validate_pcr_measurements(mock_ssh_client)

        assert "PCR4" in str(exc_info.value), \
            "Error should mention invalid PCR4 value"


@settings(max_examples=100)
@given(
    pcr4=hex_string_strategy(min_size=2, max_size=96),
    pcr7=st.text(min_size=1, max_size=20).filter(
        lambda s: not re.match(r'^[0-9a-fA-F]+$', s)
    ),
)
def test_pcr_measurements_invalid_hex_pcr7_raises_error(pcr4, pcr7):
    """
    Property 72: PCR Measurements Round-Trip

    For any PCR7 value that is not a valid hex string, validate_pcr_measurements
    should raise RuntimeError.

    **Validates: Requirements 18.7**
    """
    mock_ssh_client = Mock()

    pcr_json = json.dumps({
        "Measurements": {
            "PCR4": pcr4,
            "PCR7": pcr7,
        }
    })

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (0, pcr_json, "")

        with pytest.raises(RuntimeError) as exc_info:
            build_ami.validate_pcr_measurements(mock_ssh_client)

        assert "PCR7" in str(exc_info.value), \
            "Error should mention invalid PCR7 value"


def test_pcr_measurements_missing_measurements_key():
    """
    Property 72: PCR Measurements Round-Trip

    When pcr_measurements.json is missing the Measurements key,
    validate_pcr_measurements should raise RuntimeError.

    **Validates: Requirements 18.7**
    """
    mock_ssh_client = Mock()

    pcr_json = json.dumps({"other_key": "value"})

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (0, pcr_json, "")

        with pytest.raises(RuntimeError) as exc_info:
            build_ami.validate_pcr_measurements(mock_ssh_client)

        assert "PCR" in str(exc_info.value) or "Measurements" in str(exc_info.value), \
            "Error should indicate missing Measurements key"


def test_pcr_measurements_empty_pcr_values():
    """
    Property 72: PCR Measurements Round-Trip

    When PCR values are empty strings, validate_pcr_measurements should
    raise RuntimeError.

    **Validates: Requirements 18.7**
    """
    mock_ssh_client = Mock()

    pcr_json = json.dumps({
        "Measurements": {
            "PCR4": "",
            "PCR7": "",
        }
    })

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (0, pcr_json, "")

        with pytest.raises(RuntimeError) as exc_info:
            build_ami.validate_pcr_measurements(mock_ssh_client)

        assert "PCR4" in str(exc_info.value), \
            "Error should mention invalid PCR4 value"
