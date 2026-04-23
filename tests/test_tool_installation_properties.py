"""
Property-based tests for tool installation on AMI build instance.

These tests validate the correctness properties for tool installation,
including verification of installed tools and proper error handling.
"""

import re
import sys
from pathlib import Path
from typing import Tuple
from unittest.mock import Mock, MagicMock, patch, call

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


# Mock SSH client for testing
def create_mock_ssh_client(
    command_responses: dict[str, Tuple[int, str, str]]
) -> Mock:
    """
    Create a mock SSH client that returns predefined responses for commands.
    
    Args:
        command_responses: Dict mapping command patterns to (exit_code, stdout, stderr) tuples
    
    Returns:
        Mock SSH client
    """
    mock_client = Mock()
    
    def mock_execute(ssh_client, command, stream_output=True):
        # Find matching command response
        for pattern, response in command_responses.items():
            if pattern in command:
                return response
        # Default response if no match
        return (0, "", "")
    
    return mock_client, mock_execute


# Feature: github-actions-remote-executor, Property 70: Tool Installation Verification
@settings(max_examples=20)
@given(
    oras_version=st.text(min_size=5, max_size=10),
    gh_version=st.text(min_size=5, max_size=10)
)
def test_tool_installation_verification(oras_version: str, gh_version: str):
    """
    Property 70: Tool Installation Verification
    
    For any tool installation on the build instance, the installation should be
    verified before proceeding to the next step. Each tool must have a verification
    command that confirms successful installation.
    
    **Validates: Requirements 16.8, 16.9, 16.10**
    """
    # Mock SSH client
    mock_ssh_client = Mock()
    
    # Define successful command responses (sha256sum must be checked before oras_ since the sha256sum command contains oras_ in the path)
    command_responses = {
        "dnf install -y git gcc": (0, "Installed successfully", ""),
        "gpg --recv-keys": (0, "Key imported", ""),
        "rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz": (0, "Downloaded", ""),
        "gpg --verify": (0, "Good signature", ""),
        "install.sh --prefix": (0, "Rust installed", ""),
        "sha256sum": (0, "6cdc692f929100feb08aa8de584d02f7bcc30ec7d88bc2adc2054d782db57c64  /tmp/oras_1.3.0_linux_amd64.tar.gz", ""),
        "curl -LO": (0, "ORAS downloaded", ""),
        "tar -xzf oras_": (0, "ORAS extracted", ""),
        "oras version": (0, f"Version: {oras_version}", ""),
        "dnf install gh": (0, "GitHub CLI installed", ""),
        "gh version": (0, f"gh version {gh_version}", ""),
        "git clone": (0, "Cloned", ""),
        "cargo install --locked coldsnap": (0, "Built coldsnap", ""),
        "coldsnap --help": (0, "coldsnap help output", "")
    }
    
    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        # Configure mock to return appropriate responses
        def side_effect(ssh_client, command, stream_output=True):
            for pattern, response in command_responses.items():
                if pattern in command:
                    return response
            return (0, "", "")
        
        mock_execute.side_effect = side_effect
        
        # Test system dependencies installation with verification
        build_ami.install_system_dependencies(mock_ssh_client)
        
        # Verify installation command was called
        calls = [call[0][1] for call in mock_execute.call_args_list]
        assert any("dnf install -y git gcc" in cmd for cmd in calls), \
            "System dependencies installation command should be executed"
        
        mock_execute.reset_mock()
        
        # Test Rust installation with verification
        build_ami.install_rust(mock_ssh_client)
        calls = [call[0][1] for call in mock_execute.call_args_list]
        assert any("rust-1.94.1" in cmd for cmd in calls), \
            "Rust installation command should be executed"
        
        mock_execute.reset_mock()
        
        # Test ORAS installation with verification
        build_ami.install_oras(mock_ssh_client)
        calls = [call[0][1] for call in mock_execute.call_args_list]
        assert any("oras version" in cmd for cmd in calls), \
            "ORAS verification command should be executed"
        
        mock_execute.reset_mock()
        
        # Test GitHub CLI installation with verification
        build_ami.install_github_cli(mock_ssh_client)
        calls = [call[0][1] for call in mock_execute.call_args_list]
        assert any("gh version" in cmd for cmd in calls), \
            "GitHub CLI verification command should be executed"
        
        mock_execute.reset_mock()
        
        # Test coldsnap installation with verification
        build_ami.install_coldsnap(mock_ssh_client)
        calls = [call[0][1] for call in mock_execute.call_args_list]
        assert any("coldsnap --help" in cmd for cmd in calls), \
            "Coldsnap verification command should be executed"


@settings(max_examples=20)
@given(
    error_message=st.text(min_size=10, max_size=100)
)
def test_tool_installation_failure_handling(error_message: str):
    """
    For any tool installation that fails (non-zero exit code),
    a RuntimeError should be raised with descriptive error information.
    
    **Validates: Requirements 16.11, 16.12**
    """
    mock_ssh_client = Mock()
    
    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        # Simulate installation failure
        mock_execute.return_value = (1, "", error_message)
        
        # Installation should raise RuntimeError
        with pytest.raises(RuntimeError) as exc_info:
            build_ami.install_system_dependencies(mock_ssh_client)
        
        # Error message should contain stderr output
        assert error_message in str(exc_info.value) or "Failed" in str(exc_info.value), \
            "RuntimeError should contain descriptive error information"


@settings(max_examples=20)
@given(
    verification_exit_code=st.integers(min_value=1, max_value=255)
)
def test_tool_verification_failure_detection(verification_exit_code: int):
    """
    For any tool where the verification command returns a non-zero exit code,
    the installation function should raise a RuntimeError indicating verification failure.
    
    **Validates: Requirements 16.8, 16.9, 16.10**
    """
    mock_ssh_client = Mock()
    
    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        # Installation succeeds but verification fails
        def side_effect(ssh_client, command, stream_output=True):
            if "oras version" in command:
                return (verification_exit_code, "", "Command not found")
            if "sha256sum" in command:
                return (0, "6cdc692f929100feb08aa8de584d02f7bcc30ec7d88bc2adc2054d782db57c64  /tmp/oras_1.3.0_linux_amd64.tar.gz", "")
            return (0, "Success", "")
        
        mock_execute.side_effect = side_effect
        
        # Should raise RuntimeError due to verification failure
        with pytest.raises(RuntimeError) as exc_info:
            build_ami.install_oras(mock_ssh_client)
        
        assert "verify" in str(exc_info.value).lower() or "failed" in str(exc_info.value).lower(), \
            "Error should indicate verification failure"


def test_install_all_tools_sequential_execution():
    """
    For any call to install_all_tools, all tool installation functions should be
    executed in the correct sequence: system deps, Rust, ORAS, GitHub CLI, coldsnap.
    
    **Validates: Requirements 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.11**
    """
    mock_ssh_client = Mock()
    
    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        # Track order of commands
        executed_commands = []
        
        def side_effect(ssh_client, command, stream_output=True):
            executed_commands.append(command)
            # Return matching checksum for sha256sum command
            if "sha256sum" in command:
                return (0, "6cdc692f929100feb08aa8de584d02f7bcc30ec7d88bc2adc2054d782db57c64  /tmp/oras_1.3.0_linux_amd64.tar.gz", "")
            # Return success for all commands
            return (0, "Success", "")
        
        mock_execute.side_effect = side_effect
        
        # Execute install_all_tools
        build_ami.install_all_tools(mock_ssh_client)
        
        # Verify correct execution order
        command_str = " ".join(executed_commands)
        
        # Find indices of key commands
        git_gcc_idx = next((i for i, cmd in enumerate(executed_commands) 
                           if "git gcc" in cmd), -1)
        rust_idx = next((i for i, cmd in enumerate(executed_commands) 
                        if "rust-1.94.1" in cmd and "gpg --recv-keys" not in cmd), -1)
        oras_idx = next((i for i, cmd in enumerate(executed_commands) 
                        if "oras_" in cmd), -1)
        gh_idx = next((i for i, cmd in enumerate(executed_commands) 
                      if "gh-cli.repo" in cmd or "dnf install gh" in cmd), -1)
        coldsnap_idx = next((i for i, cmd in enumerate(executed_commands) 
                            if "coldsnap" in cmd and "git clone" in cmd), -1)
        
        # Verify order (all should be found and in sequence)
        assert git_gcc_idx >= 0, "System dependencies should be installed"
        assert rust_idx >= 0, "Rust should be installed"
        assert oras_idx >= 0, "ORAS should be installed"
        assert gh_idx >= 0, "GitHub CLI should be installed"
        assert coldsnap_idx >= 0, "Coldsnap should be installed"
        
        # Verify sequential order
        assert git_gcc_idx < rust_idx, "System deps should be installed before Rust"
        assert rust_idx < oras_idx, "Rust should be installed before ORAS"
        assert oras_idx < gh_idx, "ORAS should be installed before GitHub CLI"
        assert gh_idx < coldsnap_idx, "GitHub CLI should be installed before coldsnap"


@settings(max_examples=20)
@given(
    failing_tool=st.sampled_from([
        "system_deps", "rust", "oras", "github_cli", "coldsnap"
    ])
)
def test_install_all_tools_failure_propagation(failing_tool: str):
    """
    For any tool installation failure in install_all_tools, the function should
    immediately raise a RuntimeError and not proceed to subsequent installations.
    
    **Validates: Requirements 16.12**
    """
    mock_ssh_client = Mock()
    
    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        executed_commands = []
        
        def side_effect(ssh_client, command, stream_output=True):
            executed_commands.append(command)
            
            # Fail at the specified tool
            if failing_tool == "system_deps" and "git gcc" in command:
                return (1, "", "Failed to install system deps")
            elif failing_tool == "rust" and "gpg --recv-keys" in command:
                return (1, "", "Failed to install Rust")
            elif failing_tool == "oras" and "oras_" in command and "sha256sum" not in command:
                return (1, "", "Failed to install ORAS")
            elif failing_tool == "github_cli" and ("gh-cli.repo" in command or "dnf install gh" in command):
                return (1, "", "Failed to install GitHub CLI")
            elif failing_tool == "coldsnap" and "git clone" in command and "coldsnap" in command:
                return (1, "", "Failed to install coldsnap")
            
            # Return matching checksum for sha256sum command
            if "sha256sum" in command:
                return (0, "6cdc692f929100feb08aa8de584d02f7bcc30ec7d88bc2adc2054d782db57c64  /tmp/oras_1.3.0_linux_amd64.tar.gz", "")
            
            return (0, "Success", "")
        
        mock_execute.side_effect = side_effect
        
        # Should raise RuntimeError
        with pytest.raises(RuntimeError):
            build_ami.install_all_tools(mock_ssh_client)
        
        # Verify that execution stopped at the failing tool
        # (subsequent tools should not have been attempted)
        command_str = " ".join(executed_commands)
        
        if failing_tool == "system_deps":
            # Should not proceed to Rust
            assert not any("rust-1.94.1" in cmd for cmd in executed_commands), \
                "Should not proceed to Rust after system deps failure"
        elif failing_tool == "rust":
            # Should not proceed to ORAS
            assert not any("oras_" in cmd for cmd in executed_commands), \
                "Should not proceed to ORAS after Rust failure"
        elif failing_tool == "oras":
            # Should not proceed to GitHub CLI
            assert not any("gh-cli.repo" in cmd for cmd in executed_commands), \
                "Should not proceed to GitHub CLI after ORAS failure"
        elif failing_tool == "github_cli":
            # Should not proceed to coldsnap
            assert not any("coldsnap" in cmd and "git clone" in cmd for cmd in executed_commands), \
                "Should not proceed to coldsnap after GitHub CLI failure"


@settings(max_examples=20)
@given(
    stdout_output=st.text(min_size=10, max_size=200)
)
def test_tool_verification_output_logging(stdout_output: str):
    """
    For any successful tool installation with verification output,
    the verification output should be captured and can be logged.
    
    **Validates: Requirements 16.8, 16.9, 16.10, 16.11**
    """
    mock_ssh_client = Mock()
    
    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        def side_effect(ssh_client, command, stream_output=True):
            if "oras version" in command:
                return (0, stdout_output, "")
            if "sha256sum" in command:
                return (0, "6cdc692f929100feb08aa8de584d02f7bcc30ec7d88bc2adc2054d782db57c64  /tmp/oras_1.3.0_linux_amd64.tar.gz", "")
            return (0, "Success", "")
        
        mock_execute.side_effect = side_effect
        
        # Should complete successfully
        build_ami.install_oras(mock_ssh_client)
        
        # Verify that verification command was called
        verification_calls = [
            call for call in mock_execute.call_args_list
            if "oras version" in call[0][1]
        ]
        
        assert len(verification_calls) > 0, \
            "Verification command should be executed"


def test_coldsnap_uses_full_cargo_path():
    """
    For any coldsnap installation, the cargo command should use the full path
    /home/ec2-user/.cargo/bin/cargo to ensure it's found after Rust installation.
    
    **Validates: Requirements 16.6, 16.7**
    """
    mock_ssh_client = Mock()
    
    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (0, "Success", "")
        
        build_ami.install_coldsnap(mock_ssh_client)
        
        # Check that cargo is called with full path
        cargo_calls = [
            call for call in mock_execute.call_args_list
            if "cargo install" in call[0][1]
        ]
        
        assert len(cargo_calls) > 0, "Cargo install command should be executed"
        
        # Verify full path is used
        cargo_command = cargo_calls[0][0][1]
        assert "/home/ec2-user/.cargo/bin/cargo" in cargo_command, \
            "Cargo should be called with full path"


def test_coldsnap_verification_uses_full_path():
    """
    For any coldsnap verification, the coldsnap command should use the full path
    /home/ec2-user/.cargo/bin/coldsnap.
    
    **Validates: Requirements 16.10**
    """
    mock_ssh_client = Mock()
    
    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (0, "Success", "")
        
        build_ami.install_coldsnap(mock_ssh_client)
        
        # Check that coldsnap verification uses full path
        verification_calls = [
            call for call in mock_execute.call_args_list
            if "coldsnap --help" in call[0][1]
        ]
        
        assert len(verification_calls) > 0, \
            "Coldsnap verification command should be executed"
        
        # Verify full path is used
        verification_command = verification_calls[0][0][1]
        assert "/home/ec2-user/.cargo/bin/coldsnap" in verification_command, \
            "Coldsnap verification should use full path"


# Feature: github-actions-remote-executor, Property 158: ORAS Checksum Verification
@settings(max_examples=100)
@given(
    bad_checksum=st.text(
        alphabet=st.sampled_from("0123456789abcdef"),
        min_size=64,
        max_size=64,
    )
)
def test_oras_checksum_verification(bad_checksum: str):
    """
    Property 158: ORAS Checksum Verification

    For any ORAS CLI download, the AMI_Converter should verify the downloaded archive
    against a known SHA-256 checksum before installation. If the checksum does not match,
    the converter should fail with an integrity verification error.

    **Validates: Requirements 17.13, 17.14**
    """
    mock_ssh_client = Mock()

    # The expected checksum hardcoded in install_oras
    expected_checksum = "6cdc692f929100feb08aa8de584d02f7bcc30ec7d88bc2adc2054d782db57c64"

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        # --- Case 1: matching checksum should succeed ---
        def side_effect_match(ssh_client, command, stream_output=True):
            if "sha256sum" in command:
                return (0, f"{expected_checksum}  /tmp/oras_1.3.0_linux_amd64.tar.gz", "")
            if "oras version" in command:
                return (0, "Version: 1.3.0", "")
            return (0, "Success", "")

        mock_execute.side_effect = side_effect_match
        # Should succeed without raising
        build_ami.install_oras(mock_ssh_client)

        # Verify sha256sum command was executed on the remote instance
        calls = [c[0][1] for c in mock_execute.call_args_list]
        assert any("sha256sum" in cmd for cmd in calls), \
            "sha256sum command should be executed on the remote instance"

        mock_execute.reset_mock()

        # --- Case 2: mismatched checksum should raise RuntimeError ---
        assume(bad_checksum != expected_checksum)

        def side_effect_mismatch(ssh_client, command, stream_output=True):
            if "sha256sum" in command:
                return (0, f"{bad_checksum}  /tmp/oras_1.3.0_linux_amd64.tar.gz", "")
            return (0, "Success", "")

        mock_execute.side_effect = side_effect_mismatch

        with pytest.raises(RuntimeError) as exc_info:
            build_ami.install_oras(mock_ssh_client)

        error_msg = str(exc_info.value).lower()
        assert "integrity" in error_msg or "verification" in error_msg or "checksum" in error_msg, \
            "RuntimeError should indicate an integrity verification failure"


# Feature: github-actions-remote-executor, Property 159: Coldsnap Pinned Version
@settings(max_examples=100)
@given(
    data=st.data()
)
def test_coldsnap_pinned_version(data):
    """
    Property 159: Coldsnap Pinned Version

    For any coldsnap installation, the AMI_Converter should clone coldsnap at a
    specific pinned git tag or commit hash rather than HEAD.

    **Validates: Requirements 17.15**
    """
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        mock_execute.return_value = (0, "Success", "")

        build_ami.install_coldsnap(mock_ssh_client)

        # Find the git clone command
        calls = [c[0][1] for c in mock_execute.call_args_list]
        clone_calls = [cmd for cmd in calls if "git clone" in cmd and "coldsnap" in cmd]

        assert len(clone_calls) > 0, "git clone command for coldsnap should be executed"

        clone_cmd = clone_calls[0]

        # Verify the clone command includes --branch with a pinned version
        assert "--branch" in clone_cmd, \
            "git clone should use --branch to pin to a specific version"

        # Verify a version tag is specified (e.g., v0.9.0)
        assert re.search(r"--branch\s+v[\d.]+", clone_cmd), \
            "git clone should specify a version tag (e.g., v0.9.0)"

        # Verify --depth 1 is used for shallow clone
        assert "--depth 1" in clone_cmd or "--depth=1" in clone_cmd, \
            "git clone should use --depth 1 for a shallow clone at the pinned version"


# Feature: github-actions-remote-executor, Property 169: Rust Installer GPG Verification
@settings(max_examples=20)
@given(data=st.data())
def test_rust_installer_gpg_verification(data):
    """
    Property 169: Rust Installer GPG Verification

    For any Rust installation, verify the GPG signature of the standalone tarball is
    fetched and verified against the official Rust project signing key before extraction.
    If verification fails, the tarball is not extracted and an error is raised.

    Parses install_rust() to verify it contains:
    - a key import step (gpg --recv-keys)
    - a tarball download step (from static.rust-lang.org/dist/rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz)
    - a signature download step (.asc)
    - a gpg --verify step
    - a failure path that does not extract
    - only then an extraction and install step

    **Validates: Requirements 17.17**
    """
    import inspect

    # Parse the source of install_rust to verify structural requirements
    source = inspect.getsource(build_ami.install_rust)

    # Must import the official Rust GPG signing key
    assert "gpg --recv-keys" in source, \
        "install_rust must import the Rust GPG signing key via gpg --recv-keys"
    assert "85AB96E6FA1BE5FE" in source, \
        "install_rust must use the official Rust project signing key 85AB96E6FA1BE5FE"

    # Must download the standalone tarball (not rustup-init)
    assert "static.rust-lang.org/dist/rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz" in source, \
        "install_rust must download the standalone tarball from static.rust-lang.org"

    # Must download the detached GPG signature (.asc)
    assert ".asc" in source, \
        "install_rust must download the detached GPG signature (.asc file)"

    # Must verify the GPG signature before extraction
    assert "gpg --verify" in source, \
        "install_rust must verify the GPG signature with gpg --verify"

    # Must have an extraction step (tar -xzf) that comes after gpg --verify
    assert "tar -xzf" in source, \
        "install_rust must extract the tarball with tar -xzf"

    # Verify ordering: gpg --verify must appear before tar -xzf in the source
    gpg_verify_pos = source.index("gpg --verify")
    tar_extract_pos = source.index("tar -xzf")
    assert gpg_verify_pos < tar_extract_pos, \
        "gpg --verify must appear before tar -xzf (verify before extract)"

    # Must use the standalone installer script
    assert "install.sh" in source, \
        "install_rust must use the standalone install.sh script"

    # Must clean up after installation
    assert "rm -rf" in source, \
        "install_rust must clean up the tarball, signature, and extracted directory"

    # --- Behavioural check: GPG failure prevents extraction ---
    mock_ssh_client = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute:
        executed_commands = []

        def side_effect_gpg_fail(ssh_client, command, stream_output=True):
            executed_commands.append(command)
            if "gpg --verify" in command:
                return (1, "", "BAD signature")
            return (0, "Success", "")

        mock_execute.side_effect = side_effect_gpg_fail

        with pytest.raises(RuntimeError) as exc_info:
            build_ami.install_rust(mock_ssh_client)

        # Extraction must NOT have been attempted after GPG failure
        assert not any("tar -xzf" in cmd for cmd in executed_commands), \
            "tar -xzf must not be executed when GPG verification fails"
        assert not any("install.sh" in cmd for cmd in executed_commands), \
            "install.sh must not be executed when GPG verification fails"

        error_msg = str(exc_info.value).lower()
        assert "gpg" in error_msg or "signature" in error_msg or "verification" in error_msg, \
            "RuntimeError should indicate GPG/signature verification failure"

    # --- Behavioural check: successful GPG verification allows extraction ---
    mock_ssh_client2 = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_execute2:
        executed_commands2 = []

        def side_effect_gpg_ok(ssh_client, command, stream_output=True):
            executed_commands2.append(command)
            return (0, "Good signature", "")

        mock_execute2.side_effect = side_effect_gpg_ok

        # Should succeed without raising
        build_ami.install_rust(mock_ssh_client2)

        # Extraction must have been attempted after successful GPG verification
        assert any("tar -xzf" in cmd for cmd in executed_commands2), \
            "tar -xzf must be executed when GPG verification succeeds"
        assert any("install.sh" in cmd for cmd in executed_commands2), \
            "install.sh must be executed when GPG verification succeeds"
