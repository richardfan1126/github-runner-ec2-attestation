"""Property-based tests for AWS Nitro attestation generator

Feature: github-actions-remote-executor
Tests Properties 15, 16, 17, 20 from the design document
"""
import os
import tempfile
import pytest
from hypothesis import given, strategies as st, assume, settings
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, UTC
from src.attestation import AttestationGenerator, AttestationError
from src.models import AttestationDocument


# Custom strategies for generating test data
@st.composite
def valid_github_url(draw):
    """Generate valid GitHub repository URLs"""
    owner = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-',
        min_size=1,
        max_size=39
    ))
    repo = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-',
        min_size=1,
        max_size=100
    ))
    trailing_slash = draw(st.sampled_from(['', '/']))
    return f"https://github.com/{owner}/{repo}{trailing_slash}"


@st.composite
def valid_commit_hash(draw):
    """Generate valid Git commit SHA (40 hex characters)"""
    return draw(st.text(alphabet='0123456789abcdef', min_size=40, max_size=40))


@st.composite
def valid_script_path(draw):
    """Generate valid script paths"""
    components = draw(st.lists(
        st.text(
            alphabet=st.characters(
                blacklist_characters='\\/:*?"<>|\x00',
                blacklist_categories=('Cc', 'Cs')
            ),
            min_size=1,
            max_size=50
        ).filter(lambda x: '..' not in x and x.strip() and '\x00' not in x),
        min_size=1,
        max_size=5
    ))
    return '/'.join(components)


@st.composite
def valid_nonce(draw):
    """Generate valid nonce strings"""
    return draw(st.text(
        alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')),
        min_size=16,
        max_size=64
    ))


@st.composite
def cbor_attestation_bytes(draw):
    """Generate mock CBOR-encoded attestation document bytes"""
    # Generate realistic-looking binary data
    size = draw(st.integers(min_value=100, max_value=5000))
    return draw(st.binary(min_size=size, max_size=size))


# Property 15: Attestation Document Generation
# Feature: github-actions-remote-executor, Property 15: Attestation Document Generation
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path(),
    nonce=st.one_of(st.none(), valid_nonce()),
    attestation_bytes=cbor_attestation_bytes()
)
@settings(max_examples=20)
def test_property_15_attestation_document_generation(
    repo_url, commit, path, nonce, attestation_bytes
):
    """
    Property 15: For any successfully retrieved script file, the Attestation
    Generator should create an attestation document.
    
    Validates: Requirements 4.1
    """
    generator = AttestationGenerator()
    
    with patch('subprocess.run') as mock_run:
        # Mock successful attestation generation
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = attestation_bytes
        mock_result.stderr = b''
        mock_run.return_value = mock_result
        
        # Generate attestation
        attestation_doc, error = generator.generate_attestation(
            repository_url=repo_url,
            commit_hash=commit,
            script_path=path,
            nonce=nonce
        )
        
        # Should successfully create attestation document
        assert attestation_doc is not None, "Should create attestation document"
        assert error is None, "Should not have error on success"
        
        # Should be an AttestationDocument instance
        assert isinstance(attestation_doc, AttestationDocument)
        
        # Verify subprocess was called
        assert mock_run.called, "Should invoke nitro-tpm-attest"
        
        # Verify command structure
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert '/usr/bin/nitro-tpm-attest' in cmd[0] or 'nitro-tpm-attest' in cmd[0]
        assert '--user-data' in cmd
        
        # If nonce provided, should be in command
        if nonce is not None:
            assert '--nonce' in cmd


# Property 16: Attestation Document Completeness
# Feature: github-actions-remote-executor, Property 16: Attestation Document Completeness
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path(),
    attestation_bytes=cbor_attestation_bytes()
)
@settings(max_examples=20)
def test_property_16_attestation_document_completeness(
    repo_url, commit, path, attestation_bytes
):
    """
    Property 16: For any generated attestation document, it should include the
    repository URL, commit hash, script file path, and timestamp.
    
    Validates: Requirements 4.2, 4.3, 4.4, 4.5
    """
    generator = AttestationGenerator()
    
    with patch('subprocess.run') as mock_run:
        # Mock successful attestation generation
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = attestation_bytes
        mock_result.stderr = b''
        mock_run.return_value = mock_result
        
        # Capture time before generation
        time_before = datetime.now(UTC)
        
        # Generate attestation
        attestation_doc, error = generator.generate_attestation(
            repository_url=repo_url,
            commit_hash=commit,
            script_path=path
        )
        
        # Capture time after generation
        time_after = datetime.now(UTC)
        
        # Should have all required fields
        assert attestation_doc is not None
        
        # Requirement 4.2: Should include repository URL
        assert attestation_doc.repository_url == repo_url, \
            "Attestation should include repository URL"
        
        # Requirement 4.3: Should include commit hash
        assert attestation_doc.commit_hash == commit, \
            "Attestation should include commit hash"
        
        # Requirement 4.4: Should include script file path
        assert attestation_doc.script_path == path, \
            "Attestation should include script path"
        
        # Requirement 4.5: Should include timestamp
        assert attestation_doc.timestamp is not None, \
            "Attestation should include timestamp"
        assert isinstance(attestation_doc.timestamp, datetime), \
            "Timestamp should be a datetime object"
        
        # Timestamp should be reasonable (between before and after)
        assert time_before <= attestation_doc.timestamp <= time_after, \
            "Timestamp should be within generation time window"


# Property 17: Attestation Document Signing
# Feature: github-actions-remote-executor, Property 17: Attestation Document Signing
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path(),
    attestation_bytes=cbor_attestation_bytes()
)
@settings(max_examples=20)
def test_property_17_attestation_document_signing(
    repo_url, commit, path, attestation_bytes
):
    """
    Property 17: For any generated attestation document, it should be signed
    using AWS Nitro attestation capabilities and the signature should be verifiable.
    
    Validates: Requirements 4.6
    """
    generator = AttestationGenerator()
    
    with patch('subprocess.run') as mock_run:
        # Mock successful attestation generation with signature
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = attestation_bytes  # CBOR-encoded attestation
        mock_result.stderr = b''
        mock_run.return_value = mock_result
        
        # Generate attestation
        attestation_doc, error = generator.generate_attestation(
            repository_url=repo_url,
            commit_hash=commit,
            script_path=path
        )
        
        # Should have signature
        assert attestation_doc is not None
        assert attestation_doc.signature is not None, \
            "Attestation should include signature"
        
        # Signature should be bytes (CBOR-encoded)
        assert isinstance(attestation_doc.signature, bytes), \
            "Signature should be bytes"
        
        # Signature should not be empty
        assert len(attestation_doc.signature) > 0, \
            "Signature should not be empty"
        
        # Signature should be the CBOR-encoded attestation from NSM
        assert attestation_doc.signature == attestation_bytes, \
            "Signature should be the CBOR attestation from nitro-tpm-attest"
        
        # Verify that nitro-tpm-attest was invoked (which does the signing)
        assert mock_run.called
        call_args = mock_run.call_args
        
        # Should have timeout configured
        assert call_args[1].get('timeout') == 30, \
            "Should have 30-second timeout"
        
        # Should capture output
        assert call_args[1].get('capture_output') is True, \
            "Should capture output"


# Property 20: Attestation Failure Response
# Feature: github-actions-remote-executor, Property 20: Attestation Failure Response
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path(),
    exit_code=st.integers(min_value=1, max_value=255),
    stderr_msg=st.text(min_size=1, max_size=500)
)
@settings(max_examples=20)
def test_property_20_attestation_failure_response(
    repo_url, commit, path, exit_code, stderr_msg
):
    """
    Property 20: For any attestation generation failure, the server should
    return HTTP 500 with an attestation error message.
    
    Validates: Requirements 4.10
    """
    generator = AttestationGenerator()
    
    with patch('subprocess.run') as mock_run:
        # Mock attestation generation failure
        mock_result = Mock()
        mock_result.returncode = exit_code
        mock_result.stdout = b''
        mock_result.stderr = stderr_msg.encode('utf-8')
        mock_run.return_value = mock_result
        
        # Generate attestation
        attestation_doc, error = generator.generate_attestation(
            repository_url=repo_url,
            commit_hash=commit,
            script_path=path
        )
        
        # Should fail to create attestation document
        assert attestation_doc is None, "Should not create attestation on failure"
        assert error is not None, "Should have error on failure"
        
        # Error should be an AttestationError instance
        assert isinstance(error, AttestationError)
        
        # Error should contain detailed information
        assert error.command is not None and len(error.command) > 0, \
            "Error should include command"
        assert error.exit_code == exit_code, \
            "Error should include exit code"
        assert error.stderr is not None, \
            "Error should include stderr"
        assert error.context is not None and len(error.context) > 0, \
            "Error should include context"
        
        # Context should be descriptive
        assert 'failed' in error.context.lower() or 'error' in error.context.lower(), \
            "Error context should describe the failure"


# Additional test: Timeout handling
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path()
)
@settings(max_examples=20)
def test_attestation_timeout_handling(repo_url, commit, path):
    """
    Test that attestation generation handles timeouts correctly.
    This validates that the 30-second timeout is enforced.
    """
    generator = AttestationGenerator()
    
    with patch('subprocess.run') as mock_run:
        # Mock timeout
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=['nitro-tpm-attest'],
            timeout=30,
            output=b'partial output',
            stderr=b'timeout error'
        )
        
        # Generate attestation
        attestation_doc, error = generator.generate_attestation(
            repository_url=repo_url,
            commit_hash=commit,
            script_path=path
        )
        
        # Should fail with timeout error
        assert attestation_doc is None
        assert error is not None
        assert isinstance(error, AttestationError)
        
        # Error should indicate timeout
        assert 'timeout' in error.context.lower() or 'timed out' in error.context.lower()
        assert error.exit_code == -1


# Additional test: OS error handling
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path()
)
@settings(max_examples=20)
def test_attestation_os_error_handling(repo_url, commit, path):
    """
    Test that attestation generation handles OS errors correctly.
    This validates error handling for file system and process errors.
    """
    generator = AttestationGenerator()
    
    with patch('subprocess.run') as mock_run:
        # Mock OS error
        mock_run.side_effect = OSError("Permission denied")
        
        # Generate attestation
        attestation_doc, error = generator.generate_attestation(
            repository_url=repo_url,
            commit_hash=commit,
            script_path=path
        )
        
        # Should fail with OS error
        assert attestation_doc is None
        assert error is not None
        assert isinstance(error, AttestationError)
        
        # Error should indicate OS error
        assert 'os error' in error.context.lower() or 'permission' in error.stderr.lower()
        assert error.exit_code == -1


# Additional test: Temporary file cleanup
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path(),
    nonce=valid_nonce(),
    attestation_bytes=cbor_attestation_bytes()
)
@settings(max_examples=20)
def test_temporary_file_cleanup(repo_url, commit, path, nonce, attestation_bytes):
    """
    Test that temporary files (user_data and nonce) are cleaned up after
    attestation generation, even on success.
    
    This test verifies the cleanup behavior by checking that the finally block
    properly handles file cleanup.
    """
    generator = AttestationGenerator()
    
    # Track files created during execution
    original_mkstemp = tempfile.mkstemp
    temp_files_created = []
    
    def track_mkstemp(*args, **kwargs):
        """Track temporary files created"""
        fd, temp_path = original_mkstemp(*args, **kwargs)
        temp_files_created.append(temp_path)
        return fd, temp_path
    
    with patch('subprocess.run') as mock_run:
        # Temporarily replace mkstemp to track files
        tempfile.mkstemp = track_mkstemp
        
        try:
            # Mock successful attestation
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = attestation_bytes
            mock_result.stderr = b''
            mock_run.return_value = mock_result
            
            # Generate attestation with nonce (creates 2 temp files)
            attestation_doc, error = generator.generate_attestation(
                repository_url=repo_url,
                commit_hash=commit,
                script_path=path,
                nonce=nonce
            )
            
            # Should have created temp files
            assert len(temp_files_created) == 2, "Should create user_data and nonce temp files"
            
            # All temp files should be cleaned up
            for temp_file in temp_files_created:
                assert not os.path.exists(temp_file), \
                    f"Temporary file should be cleaned up: {temp_file}"
        finally:
            # Restore original mkstemp
            tempfile.mkstemp = original_mkstemp


# Additional test: Temporary file cleanup on failure
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path(),
    nonce=valid_nonce()
)
@settings(max_examples=20)
def test_temporary_file_cleanup_on_failure(repo_url, commit, path, nonce):
    """
    Test that temporary files are cleaned up even when attestation generation fails.
    """
    generator = AttestationGenerator()
    
    # Track files created during execution
    original_mkstemp = tempfile.mkstemp
    temp_files_created = []
    
    def track_mkstemp(*args, **kwargs):
        """Track temporary files created"""
        fd, temp_path = original_mkstemp(*args, **kwargs)
        temp_files_created.append(temp_path)
        return fd, temp_path
    
    with patch('subprocess.run') as mock_run:
        # Temporarily replace mkstemp to track files
        tempfile.mkstemp = track_mkstemp
        
        try:
            # Mock attestation failure
            mock_result = Mock()
            mock_result.returncode = 1
            mock_result.stdout = b''
            mock_result.stderr = b'Attestation failed'
            mock_run.return_value = mock_result
            
            # Generate attestation with nonce
            attestation_doc, error = generator.generate_attestation(
                repository_url=repo_url,
                commit_hash=commit,
                script_path=path,
                nonce=nonce
            )
            
            # Should have failed
            assert attestation_doc is None
            assert error is not None
            
            # All temp files should still be cleaned up
            for temp_file in temp_files_created:
                assert not os.path.exists(temp_file), \
                    f"Temporary file should be cleaned up even on failure: {temp_file}"
        finally:
            # Restore original mkstemp
            tempfile.mkstemp = original_mkstemp


# Additional test: NSM device availability check
def test_nsm_device_availability_check():
    """
    Test that verify_nsm_available correctly checks for NSM device.
    This is a prerequisite for attestation generation.
    """
    generator = AttestationGenerator()
    
    with patch('os.path.exists') as mock_exists, \
         patch('os.access') as mock_access:
        
        # Test when device exists and is executable
        mock_exists.return_value = True
        mock_access.return_value = True
        assert generator.verify_nsm_available() is True
        
        # Test when device doesn't exist
        mock_exists.return_value = False
        mock_access.return_value = False
        assert generator.verify_nsm_available() is False
        
        # Test when device exists but not executable
        mock_exists.return_value = True
        mock_access.return_value = False
        assert generator.verify_nsm_available() is False


# Additional test: User data JSON structure
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path(),
    attestation_bytes=cbor_attestation_bytes()
)
@settings(max_examples=20)
def test_user_data_json_structure(repo_url, commit, path, attestation_bytes):
    """
    Test that user_data passed to nitro-tpm-attest contains the correct
    execution metadata in JSON format.
    """
    import json
    
    generator = AttestationGenerator()
    
    captured_user_data = None
    
    def capture_write(fd, data):
        """Capture data written to file descriptors"""
        nonlocal captured_user_data
        # First write is user_data, second is nonce
        if captured_user_data is None:
            captured_user_data = data
        return len(data)
    
    with patch('subprocess.run') as mock_run, \
         patch('os.write', side_effect=capture_write), \
         patch('os.close'), \
         patch('os.unlink'):
        
        # Mock successful attestation
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = attestation_bytes
        mock_result.stderr = b''
        mock_run.return_value = mock_result
        
        # Generate attestation
        attestation_doc, error = generator.generate_attestation(
            repository_url=repo_url,
            commit_hash=commit,
            script_path=path
        )
        
        # Should have captured user_data
        assert captured_user_data is not None
        
        # Parse JSON
        user_data = json.loads(captured_user_data.decode('utf-8'))
        
        # Verify structure
        assert 'repository_url' in user_data
        assert user_data['repository_url'] == repo_url
        
        assert 'commit_hash' in user_data
        assert user_data['commit_hash'] == commit
        
        assert 'script_path' in user_data
        assert user_data['script_path'] == path
        
        assert 'timestamp' in user_data
        # Timestamp should be ISO format
        datetime.fromisoformat(user_data['timestamp'])
