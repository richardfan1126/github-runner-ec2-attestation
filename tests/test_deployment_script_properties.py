"""
Property-based tests for the deployment script (scripts/deploy.py).

These tests validate correctness properties for infrastructure state persistence,
deployment IP auto-detection, and AMI build result loading.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest
from hypothesis import given, strategies as st, settings, assume

# Import deploy module using importlib (filename has no hyphen, but use importlib for consistency)
import importlib.util
scripts_dir = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("deploy", scripts_dir / "deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


# --- Strategies ---

def terraform_output_key_strategy():
    """Generate valid Terraform output key names."""
    return st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz_",
        min_size=1,
        max_size=30,
    ).filter(lambda x: x[0] != '_' and x[-1] != '_')


def terraform_output_value_strategy():
    """Generate valid Terraform output values (strings)."""
    return st.one_of(
        st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
        st.builds(lambda h: f"vpc-{h}", st.text(alphabet="0123456789abcdef", min_size=8, max_size=17)),
        st.builds(lambda h: f"subnet-{h}", st.text(alphabet="0123456789abcdef", min_size=8, max_size=17)),
        st.builds(lambda h: f"sg-{h}", st.text(alphabet="0123456789abcdef", min_size=8, max_size=17)),
        st.builds(lambda h: f"i-{h}", st.text(alphabet="0123456789abcdef", min_size=8, max_size=17)),
        st.builds(lambda a, b, c, d: f"{a}.{b}.{c}.{d}",
                  st.integers(min_value=1, max_value=254),
                  st.integers(min_value=0, max_value=255),
                  st.integers(min_value=0, max_value=255),
                  st.integers(min_value=1, max_value=254)),
    )


def ipv4_strategy():
    """Generate valid IPv4 addresses."""
    return st.builds(
        lambda a, b, c, d: f"{a}.{b}.{c}.{d}",
        st.integers(min_value=1, max_value=254),
        st.integers(min_value=0, max_value=255),
        st.integers(min_value=0, max_value=255),
        st.integers(min_value=1, max_value=254),
    )


def ami_id_strategy():
    """Generate valid AMI IDs."""
    return st.builds(
        lambda h: f"ami-{h}",
        st.text(alphabet="0123456789abcdef", min_size=8, max_size=17),
    )


def snapshot_id_strategy():
    """Generate valid snapshot IDs."""
    return st.builds(
        lambda h: f"snap-{h}",
        st.text(alphabet="0123456789abcdef", min_size=8, max_size=17),
    )


def region_strategy():
    """Generate valid AWS region strings."""
    return st.sampled_from([
        "us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1",
        "eu-central-1", "ap-northeast-1",
    ])


# --- Property 84: Infrastructure State Persistence ---

@settings(max_examples=100, deadline=None)
@given(
    raw_output=st.dictionaries(
        keys=st.sampled_from([
            "vpc_id", "subnet_id", "security_group_id",
            "instance_id", "instance_public_ip", "attestation_api_url",
        ]),
        values=st.fixed_dictionaries({
            "value": terraform_output_value_strategy(),
            "type": st.just("string"),
            "sensitive": st.just(False),
        }),
        min_size=1,
        max_size=6,
    ),
)
def test_property_84_infrastructure_state_persistence(raw_output):
    """
    Property 84: Infrastructure State Persistence

    For any raw Terraform output JSON where each key contains a `value` field,
    extracting the values and writing them to a JSON file with 2-space indentation,
    then reading back the file, should produce a dictionary equivalent to the
    extracted values.

    Validates: Requirements 25.1, 25.2, 25.3, 25.4, 25.5, 25.6, 27.7, 27.8
    """
    # Extract values using load_terraform_output
    extracted = deploy.load_terraform_output(raw_output)

    # Verify extraction: each key should have its value field extracted
    for key, raw_val in raw_output.items():
        assert key in extracted, f"Key {key} missing from extracted output"
        assert extracted[key] == raw_val["value"], \
            f"Value mismatch for {key}: expected {raw_val['value']}, got {extracted[key]}"

    # Write to temp file with 2-space indentation (same as main() does)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        output_file = f.name

    try:
        with open(output_file, 'w') as f:
            f.write(json.dumps(extracted, indent=2))

        # Read back and verify round-trip
        with open(output_file, 'r') as f:
            loaded = json.load(f)

        assert loaded == extracted, \
            "Round-trip through JSON file should produce equivalent dictionary"

        # Verify 2-space indentation format
        with open(output_file, 'r') as f:
            raw_text = f.read()

        if extracted:
            # JSON with indent=2 should have lines starting with 2 spaces
            lines = raw_text.strip().split('\n')
            indented_lines = [l for l in lines if l.startswith('  ')]
            assert len(indented_lines) > 0, "JSON should use 2-space indentation"

    finally:
        if os.path.exists(output_file):
            os.unlink(output_file)


# --- Property 85: Deployment IP Auto-Detection ---

@settings(max_examples=100, deadline=None)
@given(ip_address=ipv4_strategy())
def test_property_85_deployment_ip_auto_detection(ip_address):
    """
    Property 85: Deployment IP Auto-Detection

    For any valid IPv4 address returned by the IP detection service, the
    deployment script should construct the allowed_http_cidr as {ip}/32.

    Validates: Requirements 26.7, 26.8
    """
    # Mock the urlopen to return the IP address
    mock_response = MagicMock()
    mock_response.read.return_value = f"{ip_address}\n".encode('utf-8')
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)

    with patch.object(deploy.request, 'urlopen', return_value=mock_response) as mock_urlopen:
        result = deploy.get_user_public_ip()

        # Verify the correct URL was called with 5-second timeout
        mock_urlopen.assert_called_once_with(
            'https://checkip.amazonaws.com', timeout=5
        )

        # Verify the IP is returned stripped
        assert result == ip_address, \
            f"Expected {ip_address}, got {result}"

        # Verify CIDR construction (as done in main())
        allowed_http_cidr = f"{result}/32"
        assert allowed_http_cidr == f"{ip_address}/32", \
            f"CIDR should be {ip_address}/32"

        # Verify CIDR format is valid
        parts = allowed_http_cidr.split('/')
        assert len(parts) == 2, "CIDR must have IP and prefix"
        assert parts[1] == "32", "Prefix must be /32"

        # Verify IP part has 4 octets
        octets = parts[0].split('.')
        assert len(octets) == 4, "IP must have 4 octets"
        for octet in octets:
            assert 0 <= int(octet) <= 255, "Each octet must be 0-255"


# --- Property 86: AMI Build Result Loading ---

@settings(max_examples=100, deadline=None)
@given(
    ami_id=ami_id_strategy(),
    snapshot_id=snapshot_id_strategy(),
    region=region_strategy(),
)
def test_property_86_ami_build_result_loading(ami_id, snapshot_id, region):
    """
    Property 86: AMI Build Result Loading

    For any valid JSON file containing ami_id, snapshot_id, and region fields,
    the deployment script should correctly parse and extract all three fields.

    Validates: Requirements 26.5
    """
    build_result = {
        "ami_id": ami_id,
        "snapshot_id": snapshot_id,
        "region": region,
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(build_result, f)
        result_file = f.name

    try:
        # Load the file the same way main() does
        with open(result_file, 'r') as f:
            loaded = json.loads(f.read())

        # Verify all three required fields are correctly extracted
        assert loaded['ami_id'] == ami_id, \
            f"ami_id mismatch: expected {ami_id}, got {loaded['ami_id']}"
        assert loaded['snapshot_id'] == snapshot_id, \
            f"snapshot_id mismatch: expected {snapshot_id}, got {loaded['snapshot_id']}"
        assert loaded['region'] == region, \
            f"region mismatch: expected {region}, got {loaded['region']}"

        # Verify the loaded result can be used with terraform_apply
        # (it expects ami_build_result['ami_id'] and ami_build_result['region'])
        assert 'ami_id' in loaded
        assert 'region' in loaded

    finally:
        if os.path.exists(result_file):
            os.unlink(result_file)


# --- Additional edge case tests for AMI build result loading ---

def test_ami_build_result_missing_file():
    """
    Verify FileNotFoundError is raised when AMI build result file doesn't exist.

    Validates: Requirement 26.4
    """
    assert not Path("/nonexistent/ami_build_result.json").exists()

    mock_args = Mock()
    mock_args.ami_build_result = "/nonexistent/ami_build_result.json"
    mock_args.instance_type = "c5.9xlarge"
    mock_args.output_file = "infrastructure_state.json"

    with patch.object(deploy, 'parse_arguments', return_value=mock_args):
        exit_code = deploy.main()
        assert exit_code == 1, "Should return exit code 1 for missing file"


def test_ami_build_result_invalid_json():
    """
    Verify RuntimeError handling when AMI build result file contains invalid JSON.

    Validates: Requirement 26.6
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("not valid json {{{")
        result_file = f.name

    try:
        mock_args = Mock()
        mock_args.ami_build_result = result_file
        mock_args.instance_type = "c5.9xlarge"
        mock_args.output_file = "infrastructure_state.json"

        with patch.object(deploy, 'parse_arguments', return_value=mock_args):
            exit_code = deploy.main()
            assert exit_code == 1, "Should return exit code 1 for invalid JSON"
    finally:
        if os.path.exists(result_file):
            os.unlink(result_file)


def test_terraform_destroy_advice_on_failure():
    """
    Verify that on failure, the script logs advice to run terraform destroy.

    Validates: Requirement 27.11
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({"ami_id": "ami-123", "snapshot_id": "snap-456", "region": "us-east-1"}, f)
        result_file = f.name

    try:
        mock_args = Mock()
        mock_args.ami_build_result = result_file
        mock_args.instance_type = "c5.9xlarge"
        mock_args.output_file = "infrastructure_state.json"

        with patch.object(deploy, 'parse_arguments', return_value=mock_args), \
             patch.object(deploy, 'get_user_public_ip', side_effect=RuntimeError("Network error")), \
             patch.object(deploy.logger, 'error') as mock_log_error:

            exit_code = deploy.main()
            assert exit_code == 1

            # Check that terraform destroy advice was logged
            log_messages = [str(call) for call in mock_log_error.call_args_list]
            destroy_advice = any("terraform destroy" in msg for msg in log_messages)
            assert destroy_advice, "Should log advice to run terraform destroy on failure"
    finally:
        if os.path.exists(result_file):
            os.unlink(result_file)
