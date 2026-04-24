"""
Unit tests for Rust GPG signature verification in install_rust().

These tests validate that install_rust() uses a GPG-verified standalone tarball
rather than the rustup-init pipe-to-shell approach, and that GPG verification
failures prevent extraction and installation.

Requirements: 17.17
"""

import sys
from pathlib import Path
from unittest.mock import Mock, patch, call

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

import importlib.util
spec = importlib.util.spec_from_file_location("build_ami", scripts_dir / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)


def _make_side_effect(responses: dict):
    """
    Build a side_effect callable that maps command substrings to
    (exit_code, stdout, stderr) tuples.  Falls back to (0, "Success", "").
    """
    def side_effect(ssh_client, command, stream_output=True):
        for pattern, response in responses.items():
            if pattern in command:
                return response
        return (0, "Success", "")
    return side_effect


# ---------------------------------------------------------------------------
# Test: GPG signing key is imported before downloading
# ---------------------------------------------------------------------------

def test_install_rust_imports_gpg_key_before_download():
    """
    install_rust must import the official Rust GPG signing key (85AB96E6FA1BE5FE)
    before downloading the tarball.

    Requirements: 17.17
    """
    mock_ssh = Mock()
    executed = []

    with patch.object(build_ami, 'execute_remote_command') as mock_exec:
        def side_effect(ssh_client, command, stream_output=True):
            executed.append(command)
            # Return appropriate stderr for GPG key import
            if "gpg --batch --no-tty --import" in command:
                return (0, "", "gpg: key 85AB96E6FA1BE5FE: public key imported")
            return (0, "Success", "")

        mock_exec.side_effect = side_effect
        build_ami.install_rust(mock_ssh)

    key_import_idx = next(
        (i for i, cmd in enumerate(executed) if "gpg --batch --no-tty --import" in cmd),
        -1,
    )
    tarball_download_idx = next(
        (i for i, cmd in enumerate(executed) if "rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz" in cmd and ".asc" not in cmd and "gpg" not in cmd),
        -1,
    )

    assert key_import_idx >= 0, "gpg --batch --no-tty --import must be called"
    assert tarball_download_idx >= 0, "Standalone tarball download must be called"
    assert key_import_idx < tarball_download_idx, \
        "GPG key must be imported before the tarball is downloaded"


# ---------------------------------------------------------------------------
# Test: standalone tarball is downloaded (not rustup-init)
# ---------------------------------------------------------------------------

def test_install_rust_downloads_standalone_tarball_not_rustup():
    """
    install_rust must download the standalone tarball from
    static.rust-lang.org/dist/rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz,
    NOT pipe rustup-init from sh.rustup.rs.

    Requirements: 17.17
    """
    mock_ssh = Mock()
    executed = []

    with patch.object(build_ami, 'execute_remote_command') as mock_exec:
        def side_effect(ssh_client, command, stream_output=True):
            executed.append(command)
            # Return appropriate stderr for GPG key import
            if "gpg --batch --no-tty --import" in command:
                return (0, "", "gpg: key 85AB96E6FA1BE5FE: public key imported")
            return (0, "Success", "")

        mock_exec.side_effect = side_effect
        build_ami.install_rust(mock_ssh)

    # Must download standalone tarball
    assert any(
        "static.rust-lang.org/dist/rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz" in cmd
        and ".asc" not in cmd
        for cmd in executed
    ), "Must download standalone tarball from static.rust-lang.org"

    # Must NOT use rustup-init pipe-to-shell
    assert not any("sh.rustup.rs" in cmd for cmd in executed), \
        "Must not use rustup-init pipe-to-shell (sh.rustup.rs)"
    assert not any("rustup-init" in cmd for cmd in executed), \
        "Must not use rustup-init binary"


# ---------------------------------------------------------------------------
# Test: detached GPG signature (.asc) is downloaded
# ---------------------------------------------------------------------------

def test_install_rust_downloads_detached_signature():
    """
    install_rust must download the detached GPG signature (.asc) for the tarball.

    Requirements: 17.17
    """
    mock_ssh = Mock()
    executed = []

    with patch.object(build_ami, 'execute_remote_command') as mock_exec:
        def side_effect(ssh_client, command, stream_output=True):
            executed.append(command)
            # Return appropriate stderr for GPG key import
            if "gpg --batch --no-tty --import" in command:
                return (0, "", "gpg: key 85AB96E6FA1BE5FE: public key imported")
            return (0, "Success", "")

        mock_exec.side_effect = side_effect
        build_ami.install_rust(mock_ssh)

    assert any(
        "rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz.asc" in cmd
        for cmd in executed
    ), "Must download the detached GPG signature (.asc file)"


# ---------------------------------------------------------------------------
# Test: GPG signature is verified before extraction
# ---------------------------------------------------------------------------

def test_install_rust_verifies_gpg_signature_before_extraction():
    """
    install_rust must run gpg --verify before tar -xzf.

    Requirements: 17.17
    """
    mock_ssh = Mock()
    executed = []

    with patch.object(build_ami, 'execute_remote_command') as mock_exec:
        def side_effect(ssh_client, command, stream_output=True):
            executed.append(command)
            # Return appropriate stderr for GPG key import
            if "gpg --batch --no-tty --import" in command:
                return (0, "", "gpg: key 85AB96E6FA1BE5FE: public key imported")
            return (0, "Good signature", "")

        mock_exec.side_effect = side_effect
        build_ami.install_rust(mock_ssh)

    gpg_verify_idx = next(
        (i for i, cmd in enumerate(executed) if "gpg --verify" in cmd),
        -1,
    )
    tar_extract_idx = next(
        (i for i, cmd in enumerate(executed) if "tar -xzf" in cmd),
        -1,
    )

    assert gpg_verify_idx >= 0, "gpg --verify must be called"
    assert tar_extract_idx >= 0, "tar -xzf must be called on success"
    assert gpg_verify_idx < tar_extract_idx, \
        "gpg --verify must be called before tar -xzf"


# ---------------------------------------------------------------------------
# Test: RuntimeError raised when GPG verification fails
# ---------------------------------------------------------------------------

def test_install_rust_raises_on_gpg_verification_failure():
    """
    install_rust must raise RuntimeError when gpg --verify returns a non-zero
    exit code, indicating a bad or missing signature.

    Requirements: 17.17
    """
    mock_ssh = Mock()

    with patch.object(build_ami, 'execute_remote_command') as mock_exec:
        mock_exec.side_effect = _make_side_effect({
            "gpg --verify": (1, "", "BAD signature from Rust project"),
        })

        with pytest.raises(RuntimeError) as exc_info:
            build_ami.install_rust(mock_ssh)

    error_msg = str(exc_info.value).lower()
    assert (
        "gpg" in error_msg
        or "signature" in error_msg
        or "verification" in error_msg
    ), "RuntimeError message should reference GPG/signature verification failure"


# ---------------------------------------------------------------------------
# Test: extraction is NOT attempted when GPG verification fails
# ---------------------------------------------------------------------------

def test_install_rust_does_not_extract_on_gpg_failure():
    """
    install_rust must NOT run tar -xzf or install.sh when GPG verification fails.

    Requirements: 17.17
    """
    mock_ssh = Mock()
    executed = []

    with patch.object(build_ami, 'execute_remote_command') as mock_exec:
        def side_effect(ssh_client, command, stream_output=True):
            executed.append(command)
            # Return appropriate stderr for GPG key import
            if "gpg --batch --no-tty --import" in command:
                return (0, "", "gpg: key 85AB96E6FA1BE5FE: public key imported")
            if "gpg --verify" in command:
                return (1, "", "BAD signature")
            return (0, "Success", "")

        mock_exec.side_effect = side_effect

        with pytest.raises(RuntimeError):
            build_ami.install_rust(mock_ssh)

    assert not any("tar -xzf" in cmd for cmd in executed), \
        "tar -xzf must NOT be executed when GPG verification fails"
    assert not any("install.sh" in cmd for cmd in executed), \
        "install.sh must NOT be executed when GPG verification fails"


# ---------------------------------------------------------------------------
# Test: extraction and install proceed when GPG verification succeeds
# ---------------------------------------------------------------------------

def test_install_rust_extracts_and_installs_on_gpg_success():
    """
    install_rust must run tar -xzf and install.sh when GPG verification succeeds.

    Requirements: 17.17
    """
    mock_ssh = Mock()
    executed = []

    with patch.object(build_ami, 'execute_remote_command') as mock_exec:
        def side_effect(ssh_client, command, stream_output=True):
            executed.append(command)
            # Return appropriate stderr for GPG key import
            if "gpg --batch --no-tty --import" in command:
                return (0, "", "gpg: key 85AB96E6FA1BE5FE: public key imported")
            return (0, "Good signature", "")

        mock_exec.side_effect = side_effect
        build_ami.install_rust(mock_ssh)

    assert any("tar -xzf" in cmd for cmd in executed), \
        "tar -xzf must be executed when GPG verification succeeds"
    assert any("install.sh" in cmd and "--prefix=/home/ec2-user/.cargo" in cmd for cmd in executed), \
        "install.sh --prefix=/home/ec2-user/.cargo must be executed when GPG verification succeeds"


# ---------------------------------------------------------------------------
# Test: tarball, signature, and extracted directory are removed after install
# ---------------------------------------------------------------------------

def test_install_rust_removes_tarball_signature_and_extracted_dir():
    """
    install_rust must remove the tarball, detached signature, and extracted
    directory after successful installation.

    Requirements: 17.17
    """
    mock_ssh = Mock()
    executed = []

    with patch.object(build_ami, 'execute_remote_command') as mock_exec:
        def side_effect(ssh_client, command, stream_output=True):
            executed.append(command)
            # Return appropriate stderr for GPG key import
            if "gpg --batch --no-tty --import" in command:
                return (0, "", "gpg: key 85AB96E6FA1BE5FE: public key imported")
            return (0, "Success", "")

        mock_exec.side_effect = side_effect
        build_ami.install_rust(mock_ssh)

    cleanup_cmds = [cmd for cmd in executed if "rm" in cmd]
    assert len(cleanup_cmds) > 0, "At least one rm command must be executed for cleanup"

    cleanup_str = " ".join(cleanup_cmds)
    assert "rust-1.94.1.tar.gz" in cleanup_str, \
        "Cleanup must remove the tarball (rust-1.94.1.tar.gz)"
    assert "rust-1.94.1.tar.gz.asc" in cleanup_str, \
        "Cleanup must remove the detached signature (.asc)"
    assert "rust-1.94.1-x86_64-unknown-linux-gnu" in cleanup_str, \
        "Cleanup must remove the extracted directory"
