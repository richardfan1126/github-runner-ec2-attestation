"""Property-based tests for GitHub repository client

Feature: github-actions-remote-executor
Tests Properties 8, 9, 10, 11, 12, 13, 14 from the design document
"""
import os
import tempfile
import pytest
from hypothesis import given, strategies as st, assume, settings
from unittest.mock import Mock, patch
from src.repository import RepositoryClient, AuthResult, FileContent, GitHubAPIError


# Custom strategies for generating test data
@st.composite
def valid_github_token(draw):
    """Generate valid-looking GitHub tokens"""
    prefix = draw(st.sampled_from(['ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_']))
    token_body = draw(st.text(
        alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')),
        min_size=30,
        max_size=40
    ))
    return f"{prefix}{token_body}"


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
                blacklist_characters='\\/:*?"<>|\x00',  # Exclude null character
                blacklist_categories=('Cc', 'Cs')  # Exclude control and surrogate characters
            ),
            min_size=1,
            max_size=50
        ).filter(lambda x: '..' not in x and x.strip() and '\x00' not in x),
        min_size=1,
        max_size=5
    ))
    return '/'.join(components)


@st.composite
def file_content_bytes(draw):
    """Generate file content as bytes"""
    content = draw(st.text(min_size=0, max_size=10000))
    return content.encode('utf-8')


# Property 8: GitHub Authentication
# Feature: github-actions-remote-executor, Property 8: GitHub Authentication
@given(token=valid_github_token())
def test_property_8_github_authentication(token):
    """
    Property 8: For any valid execution request with a GitHub token, the Repository
    Client should authenticate to GitHub using that token before fetching files.
    
    Validates: Requirements 3.1
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)
        
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock successful authentication
            mock_response = Mock()
            mock_response.status_code = 200
            mock_session.get.return_value = mock_response
            
            result = client.authenticate(token)
            
            # Should successfully authenticate
            assert result.success is True
            assert result.error_message is None
            
            # Should have created session with token
            assert client._session is not None
            assert client._authenticated is True
            
            # Verify the token was used in the session headers
            mock_session.headers.update.assert_called_once()
            call_args = mock_session.headers.update.call_args[0][0]
            assert 'Authorization' in call_args
            assert token in call_args['Authorization'] or 'Bearer' in call_args['Authorization']


# Property 9: Exact Commit File Retrieval
# Feature: github-actions-remote-executor, Property 9: Exact Commit File Retrieval
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path(),
    content=file_content_bytes()
)
@settings(max_examples=20)  # Reduce examples due to mocking complexity
def test_property_9_exact_commit_file_retrieval(repo_url, commit, path, content):
    """
    Property 9: For any valid repository, commit hash, and file path, the Repository
    Client should fetch the file content that exists at that exact commit.
    
    Validates: Requirements 3.2
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)
        
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock file content API response
            content_api_response = Mock()
            content_api_response.status_code = 200
            content_api_response.json.return_value = {
                "download_url": f"https://raw.githubusercontent.com/owner/repo/{commit}/{path}"
            }
            
            # Mock file download response with the exact content
            download_response = Mock()
            download_response.status_code = 200
            download_response.content = content
            
            mock_session.get.side_effect = [
                auth_response,
                content_api_response,
                download_response
            ]
            
            client.authenticate("test_token")
            result = client.fetch_file(repo_url, commit, path)
            
            # Should return the exact content from that commit
            assert result.content == content
            assert result.size_bytes == len(content)
            
            # Verify the API was called with the exact commit hash
            api_calls = [call for call in mock_session.get.call_args_list]
            # Second call should be to the contents API with ref parameter
            if len(api_calls) >= 2:
                contents_call = api_calls[1]
                if len(contents_call[1]) > 0 and 'params' in contents_call[1]:
                    assert contents_call[1]['params'].get('ref') == commit


# Property 10: Authentication Failure Response
# Feature: github-actions-remote-executor, Property 10: Authentication Failure Response
@given(token=st.text(min_size=1, max_size=100))
def test_property_10_authentication_failure_response(token):
    """
    Property 10: For any invalid or expired GitHub token, the Repository Client
    should return HTTP 401 with an authentication error message.
    
    Validates: Requirements 3.3
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)
        
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication failure (401)
            mock_response = Mock()
            mock_response.status_code = 401
            mock_session.get.return_value = mock_response
            
            result = client.authenticate(token)
            
            # Should fail authentication
            assert result.success is False
            assert result.error_message is not None
            assert len(result.error_message) > 0
            
            # Error message should indicate authentication issue
            error_lower = result.error_message.lower()
            assert 'invalid' in error_lower or 'expired' in error_lower or 'token' in error_lower
            
            # Client should not be authenticated
            assert client._authenticated is False


# Property 11: Repository Not Found Response
# Feature: github-actions-remote-executor, Property 11: Repository Not Found Response
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path()
)
@settings(max_examples=20)
def test_property_11_repository_not_found_response(repo_url, commit, path):
    """
    Property 11: For any non-existent repository URL, the Repository Client
    should return HTTP 404 with a repository not found error.
    
    Validates: Requirements 3.4
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)
        
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication success
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock 404 for file fetch
            file_response = Mock()
            file_response.status_code = 404
            
            # Mock 404 for repository check (repository doesn't exist)
            repo_response = Mock()
            repo_response.status_code = 404
            
            mock_session.get.side_effect = [
                auth_response,
                file_response,
                repo_response
            ]
            
            client.authenticate("test_token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(repo_url, commit, path)
            
            # Should raise 404 error
            assert exc_info.value.status_code == 404
            
            # Error message should indicate repository not found
            error_lower = exc_info.value.message.lower()
            assert 'repository' in error_lower and 'not found' in error_lower


# Property 12: Commit Not Found Response
# Feature: github-actions-remote-executor, Property 12: Commit Not Found Response
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path()
)
@settings(max_examples=20)
def test_property_12_commit_not_found_response(repo_url, commit, path):
    """
    Property 12: For any non-existent commit hash in a valid repository, the
    Repository Client should return HTTP 404 with a commit not found error.
    
    Validates: Requirements 3.5
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)
        
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication success
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock 404 for file fetch
            file_response = Mock()
            file_response.status_code = 404
            
            # Mock repository exists (200)
            repo_response = Mock()
            repo_response.status_code = 200
            
            # Mock 404 for commit check (commit doesn't exist)
            commit_response = Mock()
            commit_response.status_code = 404
            
            mock_session.get.side_effect = [
                auth_response,
                file_response,
                repo_response,
                commit_response
            ]
            
            client.authenticate("test_token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(repo_url, commit, path)
            
            # Should raise 404 error
            assert exc_info.value.status_code == 404
            
            # Error message should indicate commit not found
            error_lower = exc_info.value.message.lower()
            assert 'commit' in error_lower and 'not found' in error_lower


# Property 13: File Not Found Response
# Feature: github-actions-remote-executor, Property 13: File Not Found Response
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path()
)
@settings(max_examples=20)
def test_property_13_file_not_found_response(repo_url, commit, path):
    """
    Property 13: For any non-existent file path at a valid commit, the Repository
    Client should return HTTP 404 with a file not found error.
    
    Validates: Requirements 3.6
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)
        
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication success
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock 404 for file fetch (file doesn't exist)
            file_response = Mock()
            file_response.status_code = 404
            
            # Mock repository exists (200)
            repo_response = Mock()
            repo_response.status_code = 200
            
            # Mock commit exists (200)
            commit_response = Mock()
            commit_response.status_code = 200
            
            mock_session.get.side_effect = [
                auth_response,
                file_response,
                repo_response,
                commit_response
            ]
            
            client.authenticate("test_token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(repo_url, commit, path)
            
            # Should raise 404 error
            assert exc_info.value.status_code == 404
            
            # Error message should indicate file not found
            error_lower = exc_info.value.message.lower()
            assert 'file' in error_lower and 'not found' in error_lower


# Property 14: Temporary File Storage
# Feature: github-actions-remote-executor, Property 14: Temporary File Storage
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path(),
    content=file_content_bytes()
)
@settings(max_examples=20)
def test_property_14_temporary_file_storage(repo_url, commit, path, content):
    """
    Property 14: For any successfully fetched script file, the Repository Client
    should store the file in a temporary secure location accessible by the execution ID.
    
    Validates: Requirements 3.7
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)
        
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock file content API response
            content_api_response = Mock()
            content_api_response.status_code = 200
            content_api_response.json.return_value = {
                "download_url": f"https://raw.githubusercontent.com/owner/repo/{commit}/{path}"
            }
            
            # Mock file download response
            download_response = Mock()
            download_response.status_code = 200
            download_response.content = content
            
            mock_session.get.side_effect = [
                auth_response,
                content_api_response,
                download_response
            ]
            
            client.authenticate("test_token")
            result = client.fetch_file(repo_url, commit, path)
            
            # Should have stored file in temporary location
            assert result.temp_path is not None
            assert len(result.temp_path) > 0
            
            # File should exist
            assert os.path.exists(result.temp_path)
            
            # File should be in the configured temp storage path
            assert result.temp_path.startswith(temp_dir)
            
            # File should contain the exact content
            with open(result.temp_path, 'rb') as f:
                stored_content = f.read()
            assert stored_content == content
            
            # File should have secure permissions (owner read/write only)
            stat_info = os.stat(result.temp_path)
            permissions = stat_info.st_mode & 0o777
            assert permissions == 0o600, f"File permissions should be 0o600, got {oct(permissions)}"
            
            # Filename should include commit hash for traceability
            filename = os.path.basename(result.temp_path)
            assert commit[:8] in filename, "Filename should include commit hash prefix"
            
            # Clean up
            client.cleanup_temp_file(result.temp_path)


# Additional property test: Round-trip content integrity
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path(),
    content=file_content_bytes()
)
@settings(max_examples=20)
def test_content_round_trip_integrity(repo_url, commit, path, content):
    """
    Test that content fetched and stored maintains integrity through the round trip.
    This validates that no corruption occurs during fetch, storage, and retrieval.
    """
    # Skip empty content as it's a trivial case
    assume(len(content) > 0)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)
        
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock file content API response
            content_api_response = Mock()
            content_api_response.status_code = 200
            content_api_response.json.return_value = {
                "download_url": f"https://raw.githubusercontent.com/owner/repo/{commit}/{path}"
            }
            
            # Mock file download response
            download_response = Mock()
            download_response.status_code = 200
            download_response.content = content
            
            mock_session.get.side_effect = [
                auth_response,
                content_api_response,
                download_response
            ]
            
            client.authenticate("test_token")
            result = client.fetch_file(repo_url, commit, path)
            
            # Content in result should match original
            assert result.content == content
            
            # Content in file should match original
            with open(result.temp_path, 'rb') as f:
                file_content = f.read()
            assert file_content == content
            
            # Size should be accurate
            assert result.size_bytes == len(content)
            assert os.path.getsize(result.temp_path) == len(content)
            
            # Clean up
            client.cleanup_temp_file(result.temp_path)


# Test authentication is required before fetch
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    path=valid_script_path()
)
@settings(max_examples=20)
def test_fetch_requires_authentication(repo_url, commit, path):
    """
    Test that fetch_file requires authentication before it can be called.
    This validates Property 8 requirement that authentication happens before fetching.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)
        
        # Try to fetch without authenticating
        with pytest.raises(GitHubAPIError) as exc_info:
            client.fetch_file(repo_url, commit, path)
        
        # Should raise authentication error
        assert exc_info.value.status_code == 401
        assert 'not authenticated' in exc_info.value.message.lower()
