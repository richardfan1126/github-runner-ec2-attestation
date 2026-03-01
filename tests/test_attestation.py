"""Unit tests for attestation generator"""
import json
import os
import subprocess
import tempfile
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

import pytest

from src.attestation import AttestationGenerator, AttestationError
from src.models import AttestationDocument


@pytest.fixture
def generator():
    """Create an attestation generator with mocked NSM device path"""
    return AttestationGenerator(nsm_device_path="/usr/bin/nitro-tpm-attest")


@pytest.fixture
def mock_nsm_device(tmp_path):
    """Create a mock NSM device executable"""
    mock_device = tmp_path / "nitro-tpm-attest"
    mock_device.write_text("#!/bin/bash\necho 'mock attestation'")
    mock_device.chmod(0o755)
    return str(mock_device)


class TestNSMAvailability:
    """Tests for NSM device availability checking"""
    
    def test_nsm_available_when_exists_and_executable(self, tmp_path):
        """Test NSM is available when device exists and is executable"""
        mock_device = tmp_path / "nitro-tpm-attest"
        mock_device.write_text("#!/bin/bash\necho 'test'")
        mock_device.chmod(0o755)
        
        generator = AttestationGenerator(nsm_device_path=str(mock_device))
        assert generator.verify_nsm_available() is True
    
    def test_nsm_unavailable_when_not_exists(self):
        """Test NSM is unavailable when device does not exist"""
        generator = AttestationGenerator(nsm_device_path="/nonexistent/path")
        assert generator.verify_nsm_available() is False
    
    def test_nsm_unavailable_when_not_executable(self, tmp_path):
        """Test NSM is unavailable when device exists but is not executable"""
        mock_device = tmp_path / "nitro-tpm-attest"
        mock_device.write_text("#!/bin/bash\necho 'test'")
        mock_device.chmod(0o644)  # Not executable
        
        generator = AttestationGenerator(nsm_device_path=str(mock_device))
        assert generator.verify_nsm_available() is False


class TestAttestationDocumentStructure:
    """Tests for attestation document structure"""
    
    @patch("subprocess.run")
    def test_attestation_document_contains_all_fields(self, mock_run, generator):
        """Test attestation document includes all required fields"""
        # Mock successful subprocess execution
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = b"mock_cbor_attestation_data"
        mock_run.return_value = mock_result
        
        repo_url = "https://github.com/owner/repo"
        commit = "abc123def456" * 2  # 40 chars
        script = "scripts/build.sh"
        
        doc, error = generator.generate_attestation(repo_url, commit, script)
        
        assert error is None
        assert doc is not None
        assert isinstance(doc, AttestationDocument)
        assert doc.repository_url == repo_url
        assert doc.commit_hash == commit
        assert doc.script_path == script
        assert isinstance(doc.timestamp, datetime)
        assert isinstance(doc.signature, bytes)
    
    @patch("subprocess.run")
    def test_attestation_document_signature_is_cbor_bytes(self, mock_run, generator):
        """Test attestation document signature is CBOR-encoded bytes"""
        cbor_data = b"\xa1\x01\x02"  # Simple CBOR structure
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = cbor_data
        mock_run.return_value = mock_result
        
        doc, error = generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh"
        )
        
        assert error is None
        assert doc.signature == cbor_data
        assert isinstance(doc.signature, bytes)
    
    @patch("subprocess.run")
    def test_attestation_timestamp_is_recent(self, mock_run, generator):
        """Test attestation timestamp is close to current time"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = b"mock_attestation"
        mock_run.return_value = mock_result
        
        before = datetime.utcnow()
        doc, error = generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh"
        )
        after = datetime.utcnow()
        
        assert error is None
        assert before <= doc.timestamp <= after


class TestMockedNSMDevice:
    """Tests with mocked NSM device subprocess calls"""
    
    @patch("subprocess.run")
    def test_subprocess_called_with_correct_command(self, mock_run, generator):
        """Test subprocess is called with correct nitro-tpm-attest command"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = b"attestation"
        mock_run.return_value = mock_result
        
        generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh"
        )
        
        # Verify subprocess.run was called
        assert mock_run.called
        call_args = mock_run.call_args
        
        # Check command structure
        cmd = call_args[0][0]
        assert cmd[0] == "/usr/bin/nitro-tpm-attest"
        assert "--user-data" in cmd
        
        # Verify timeout and capture_output
        assert call_args[1]["timeout"] == 30
        assert call_args[1]["capture_output"] is True
    
    @patch("subprocess.run")
    def test_user_data_file_contains_execution_metadata(self, mock_run, generator):
        """Test user_data file contains repository URL, commit, script path, and timestamp"""
        captured_user_data = {}
        
        def capture_and_run(cmd, **kwargs):
            # Capture user_data before subprocess completes
            user_data_idx = cmd.index("--user-data")
            user_data_path = cmd[user_data_idx + 1]
            with open(user_data_path, "r") as f:
                captured_user_data.update(json.load(f))
            
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = b"attestation"
            return mock_result
        
        mock_run.side_effect = capture_and_run
        
        repo_url = "https://github.com/owner/repo"
        commit = "abc123" * 7  # 42 chars, truncate to 40
        script = "scripts/test.sh"
        
        generator.generate_attestation(repo_url, commit, script)
        
        # Verify captured user_data content
        assert captured_user_data["repository_url"] == repo_url
        assert captured_user_data["commit_hash"] == commit
        assert captured_user_data["script_path"] == script
        assert "timestamp" in captured_user_data
    
    @patch("subprocess.run")
    def test_nonce_file_created_when_provided(self, mock_run, generator):
        """Test nonce file is created and passed when nonce is provided"""
        captured_nonce = []
        
        def capture_and_run(cmd, **kwargs):
            # Capture nonce before subprocess completes
            if "--nonce" in cmd:
                nonce_idx = cmd.index("--nonce")
                nonce_path = cmd[nonce_idx + 1]
                with open(nonce_path, "r") as f:
                    captured_nonce.append(f.read())
            
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = b"attestation"
            return mock_result
        
        mock_run.side_effect = capture_and_run
        
        nonce = "test_nonce_12345"
        
        generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh",
            nonce=nonce
        )
        
        # Verify nonce flag was in command and content was correct
        call_args = mock_run.call_args[0][0]
        assert "--nonce" in call_args
        assert len(captured_nonce) == 1
        assert captured_nonce[0] == nonce
    
    @patch("subprocess.run")
    def test_nonce_not_included_when_not_provided(self, mock_run, generator):
        """Test nonce flag is not included when nonce is None"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = b"attestation"
        mock_run.return_value = mock_result
        
        generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh",
            nonce=None
        )
        
        call_args = mock_run.call_args[0][0]
        assert "--nonce" not in call_args
    
    @patch("subprocess.run")
    def test_temporary_files_cleaned_up_on_success(self, mock_run, generator):
        """Test temporary files are cleaned up after successful attestation"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = b"attestation"
        mock_run.return_value = mock_result
        
        generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh",
            nonce="test_nonce"
        )
        
        # Get file paths from subprocess call
        call_args = mock_run.call_args[0][0]
        user_data_idx = call_args.index("--user-data")
        user_data_path = call_args[user_data_idx + 1]
        nonce_idx = call_args.index("--nonce")
        nonce_path = call_args[nonce_idx + 1]
        
        # Verify files are cleaned up
        assert not os.path.exists(user_data_path)
        assert not os.path.exists(nonce_path)
    
    @patch("subprocess.run")
    def test_temporary_files_cleaned_up_on_failure(self, mock_run, generator):
        """Test temporary files are cleaned up even when attestation fails"""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = b""
        mock_result.stderr = b"error"
        mock_run.return_value = mock_result
        
        # Track file paths before they're cleaned up
        created_files = []
        
        original_mkstemp = tempfile.mkstemp
        def tracking_mkstemp(*args, **kwargs):
            fd, path = original_mkstemp(*args, **kwargs)
            created_files.append(path)
            return fd, path
        
        with patch("tempfile.mkstemp", side_effect=tracking_mkstemp):
            doc, error = generator.generate_attestation(
                "https://github.com/owner/repo",
                "a" * 40,
                "script.sh",
                nonce="test_nonce"
            )
        
        assert error is not None
        # Verify all created files are cleaned up
        for path in created_files:
            assert not os.path.exists(path)


class TestAttestationErrors:
    """Tests for attestation error handling"""
    
    @patch("subprocess.run")
    def test_subprocess_failure_returns_error(self, mock_run, generator):
        """Test subprocess failure returns AttestationError"""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = b"some output"
        mock_result.stderr = b"error message"
        mock_run.return_value = mock_result
        
        doc, error = generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh"
        )
        
        assert doc is None
        assert error is not None
        assert isinstance(error, AttestationError)
        assert error.exit_code == 1
        assert "error message" in error.stderr
        assert "nitro-tpm-attest failed" in error.context
    
    @patch("subprocess.run")
    def test_timeout_returns_error(self, mock_run, generator):
        """Test subprocess timeout returns AttestationError"""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["nitro-tpm-attest"],
            timeout=30,
            output=b"partial output",
            stderr=b"timeout error"
        )
        
        doc, error = generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh"
        )
        
        assert doc is None
        assert error is not None
        assert isinstance(error, AttestationError)
        assert error.exit_code == -1
        assert "timed out after 30 seconds" in error.context
    
    @patch("subprocess.run")
    def test_os_error_returns_error(self, mock_run, generator):
        """Test OS error returns AttestationError"""
        mock_run.side_effect = OSError("Permission denied")
        
        doc, error = generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh"
        )
        
        assert doc is None
        assert error is not None
        assert isinstance(error, AttestationError)
        assert "OS error" in error.context
        assert "Permission denied" in error.stderr
    
    @patch("subprocess.run")
    def test_unexpected_exception_returns_error(self, mock_run, generator):
        """Test unexpected exception returns AttestationError"""
        mock_run.side_effect = RuntimeError("Unexpected error")
        
        doc, error = generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh"
        )
        
        assert doc is None
        assert error is not None
        assert isinstance(error, AttestationError)
        assert "Unexpected error" in error.context
        assert "RuntimeError" in error.context
    
    @patch("subprocess.run")
    def test_error_includes_command_details(self, mock_run, generator):
        """Test error includes full command that was executed"""
        mock_result = Mock()
        mock_result.returncode = 2
        mock_result.stdout = b""
        mock_result.stderr = b"command failed"
        mock_run.return_value = mock_result
        
        doc, error = generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh"
        )
        
        assert error is not None
        assert "/usr/bin/nitro-tpm-attest" in error.command
        assert "--user-data" in error.command


class TestSignatureVerification:
    """Tests for attestation signature verification"""
    
    @patch("subprocess.run")
    def test_signature_is_raw_stdout_from_subprocess(self, mock_run, generator):
        """Test signature is the raw stdout bytes from nitro-tpm-attest"""
        expected_signature = b"\xa1\x01\x02\x03\x04\x05"
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = expected_signature
        mock_run.return_value = mock_result
        
        doc, error = generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh"
        )
        
        assert error is None
        assert doc.signature == expected_signature
    
    @patch("subprocess.run")
    def test_signature_preserves_binary_data(self, mock_run, generator):
        """Test signature preserves binary data including null bytes"""
        binary_signature = bytes(range(256))  # All possible byte values
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = binary_signature
        mock_run.return_value = mock_result
        
        doc, error = generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh"
        )
        
        assert error is None
        assert doc.signature == binary_signature
        assert len(doc.signature) == 256
    
    @patch("subprocess.run")
    def test_empty_signature_handled(self, mock_run, generator):
        """Test empty signature from subprocess is handled"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = b""
        mock_run.return_value = mock_result
        
        doc, error = generator.generate_attestation(
            "https://github.com/owner/repo",
            "a" * 40,
            "script.sh"
        )
        
        assert error is None
        assert doc.signature == b""
