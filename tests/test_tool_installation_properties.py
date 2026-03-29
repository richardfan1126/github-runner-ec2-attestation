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
    
    # Define successful command responses
    command_responses = {
        "dnf install -y git gcc": (0, "Installed successfully", ""),
        "sh.rustup.rs": (0, "Rust installed", ""),
        "oras_": (0, "ORAS downloaded", ""),
        "oras version": (0, f"Version: {oras_version}", ""),
        "dnf install gh": (0, "GitHub CLI installed", ""),
        "gh version": (0, f"gh version {gh_version}", ""),
        "git clone https://github.com/awslabs/coldsnap.git": (0, "Cloned", ""),
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
        assert any("sh.rustup.rs" in cmd for cmd in calls), \
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
                        if "rustup" in cmd), -1)
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
            elif failing_tool == "rust" and "rustup" in command:
                return (1, "", "Failed to install Rust")
            elif failing_tool == "oras" and "oras_" in command:
                return (1, "", "Failed to install ORAS")
            elif failing_tool == "github_cli" and ("gh-cli.repo" in command or "dnf install gh" in command):
                return (1, "", "Failed to install GitHub CLI")
            elif failing_tool == "coldsnap" and "git clone" in command and "coldsnap" in command:
                return (1, "", "Failed to install coldsnap")
            
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
            assert not any("rustup" in cmd for cmd in executed_commands), \
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
