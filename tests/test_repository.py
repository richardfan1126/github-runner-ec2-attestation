"""Unit tests for GitHub repository client"""
import os
import tempfile
import pytest
from unittest.mock import Mock, patch, MagicMock
from src.repository import RepositoryClient, AuthResult, FileContent, GitHubAPIError


@pytest.fixture
def temp_dir():
    """Create temporary directory for tests"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def client(temp_dir):
    """Create repository client instance"""
    return RepositoryClient(temp_storage_path=temp_dir)


class TestAuthentication:
    """Tests for GitHub authentication"""
    
    def test_successful_authentication(self, client):
        """Test successful authentication with valid token"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            mock_response = Mock()
            mock_response.status_code = 200
            mock_session.get.return_value = mock_response
            
            result = client.authenticate("valid_token")
            
            assert result.success is True
            assert result.error_message is None
            assert client._authenticated is True
    
    def test_authentication_failure_invalid_token(self, client):
        """Test authentication failure with invalid token"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            mock_response = Mock()
            mock_response.status_code = 401
            mock_session.get.return_value = mock_response
            
            result = client.authenticate("invalid_token")
            
            assert result.success is False
            assert "Invalid or expired" in result.error_message
            assert client._authenticated is False
    
    def test_authentication_network_error(self, client):
        """Test authentication with network error"""
        import requests
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            mock_session.get.side_effect = requests.RequestException("Network error")
            
            result = client.authenticate("token")
            
            assert result.success is False
            assert "Network error" in result.error_message


class TestFetchFile:
    """Tests for file fetching"""
    
    def test_fetch_file_not_authenticated(self, client):
        """Test fetch_file raises error when not authenticated"""
        with pytest.raises(GitHubAPIError) as exc_info:
            client.fetch_file(
                "https://github.com/owner/repo",
                "abc123",
                "script.sh"
            )
        
        assert exc_info.value.status_code == 401
        assert "Not authenticated" in exc_info.value.message
    
    def test_fetch_file_success(self, client, temp_dir):
        """Test successful file fetch"""
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
                "download_url": "https://raw.githubusercontent.com/owner/repo/abc123/script.sh"
            }
            
            # Mock file download response
            download_response = Mock()
            download_response.status_code = 200
            download_response.content = b"#!/bin/bash\necho 'Hello'"
            
            mock_session.get.side_effect = [
                auth_response,  # authenticate call
                content_api_response,  # fetch_file API call
                download_response  # download content call
            ]
            
            client.authenticate("token")
            result = client.fetch_file(
                "https://github.com/owner/repo",
                "abc123",
                "script.sh"
            )
            
            assert isinstance(result, FileContent)
            assert result.content == b"#!/bin/bash\necho 'Hello'"
            assert result.size_bytes == len(b"#!/bin/bash\necho 'Hello'")
            assert os.path.exists(result.temp_path)
            assert result.temp_path.startswith(temp_dir)
    
    def test_fetch_file_not_found(self, client):
        """Test fetch_file with non-existent file"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock 404 for file
            file_response = Mock()
            file_response.status_code = 404
            
            # Mock repo exists check
            repo_response = Mock()
            repo_response.status_code = 200
            
            # Mock commit exists check
            commit_response = Mock()
            commit_response.status_code = 200
            
            mock_session.get.side_effect = [
                auth_response,  # authenticate
                file_response,  # fetch_file
                repo_response,  # check repo exists
                commit_response  # check commit exists
            ]
            
            client.authenticate("token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(
                    "https://github.com/owner/repo",
                    "abc123",
                    "nonexistent.sh"
                )
            
            assert exc_info.value.status_code == 404
            assert "File not found" in exc_info.value.message
    
    def test_fetch_file_repository_not_found(self, client):
        """Test fetch_file with non-existent repository"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock 404 for file
            file_response = Mock()
            file_response.status_code = 404
            
            # Mock 404 for repo check
            repo_response = Mock()
            repo_response.status_code = 404
            
            mock_session.get.side_effect = [
                auth_response,  # authenticate
                file_response,  # fetch_file
                repo_response  # check repo exists
            ]
            
            client.authenticate("token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(
                    "https://github.com/owner/nonexistent",
                    "abc123",
                    "script.sh"
                )
            
            assert exc_info.value.status_code == 404
            assert "Repository not found" in exc_info.value.message
    
    def test_fetch_file_commit_not_found(self, client):
        """Test fetch_file with non-existent commit"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock 404 for file
            file_response = Mock()
            file_response.status_code = 404
            
            # Mock repo exists
            repo_response = Mock()
            repo_response.status_code = 200
            
            # Mock 404 for commit check
            commit_response = Mock()
            commit_response.status_code = 404
            
            mock_session.get.side_effect = [
                auth_response,  # authenticate
                file_response,  # fetch_file
                repo_response,  # check repo exists
                commit_response  # check commit exists
            ]
            
            client.authenticate("token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(
                    "https://github.com/owner/repo",
                    "nonexistent123",
                    "script.sh"
                )
            
            assert exc_info.value.status_code == 404
            assert "Commit not found" in exc_info.value.message
    
    def test_fetch_file_rate_limit(self, client):
        """Test fetch_file with rate limit error"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock 403 rate limit
            rate_limit_response = Mock()
            rate_limit_response.status_code = 403
            rate_limit_response.text = "API rate limit exceeded"
            
            mock_session.get.side_effect = [
                auth_response,  # authenticate
                rate_limit_response  # fetch_file
            ]
            
            client.authenticate("token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(
                    "https://github.com/owner/repo",
                    "abc123",
                    "script.sh"
                )
            
            assert exc_info.value.status_code == 429


class TestURLParsing:
    """Tests for URL parsing"""
    
    def test_parse_https_url(self, client):
        """Test parsing HTTPS GitHub URL"""
        owner, repo = client._parse_repo_url("https://github.com/owner/repo")
        assert owner == "owner"
        assert repo == "repo"
    
    def test_parse_https_url_with_trailing_slash(self, client):
        """Test parsing URL with trailing slash"""
        owner, repo = client._parse_repo_url("https://github.com/owner/repo/")
        assert owner == "owner"
        assert repo == "repo"
    
    def test_parse_https_url_with_git_suffix(self, client):
        """Test parsing URL with .git suffix"""
        owner, repo = client._parse_repo_url("https://github.com/owner/repo.git")
        assert owner == "owner"
        assert repo == "repo"
    
    def test_parse_ssh_url(self, client):
        """Test parsing SSH GitHub URL"""
        owner, repo = client._parse_repo_url("git@github.com:owner/repo")
        assert owner == "owner"
        assert repo == "repo"
    
    def test_parse_invalid_url(self, client):
        """Test parsing invalid URL"""
        with pytest.raises(GitHubAPIError) as exc_info:
            client._parse_repo_url("https://gitlab.com/owner/repo")
        
        assert exc_info.value.status_code == 400
        assert "Invalid GitHub repository URL" in exc_info.value.message


class TestTempFileStorage:
    """Tests for temporary file storage"""
    
    def test_store_temp_file(self, client, temp_dir):
        """Test storing file in temporary location"""
        content = b"test content"
        temp_path = client._store_temp_file(content, "abc123", "script.sh")
        
        assert os.path.exists(temp_path)
        assert temp_path.startswith(temp_dir)
        assert "abc123" in os.path.basename(temp_path)
        assert "script.sh" in os.path.basename(temp_path)
        
        with open(temp_path, "rb") as f:
            assert f.read() == content
        
        # Check permissions (owner read/write only)
        stat_info = os.stat(temp_path)
        assert stat_info.st_mode & 0o777 == 0o600
    
    def test_cleanup_temp_file(self, client, temp_dir):
        """Test cleaning up temporary file"""
        content = b"test content"
        temp_path = client._store_temp_file(content, "abc123", "script.sh")
        
        assert os.path.exists(temp_path)
        
        client.cleanup_temp_file(temp_path)
        
        assert not os.path.exists(temp_path)
    
    def test_cleanup_nonexistent_file(self, client):
        """Test cleanup of non-existent file doesn't raise error"""
        # Should not raise exception
        client.cleanup_temp_file("/nonexistent/path/file.txt")


class TestFileSizeValidation:
    """Tests for file size validation (Requirement 8.4)"""
    
    def test_fetch_large_file(self, client, temp_dir):
        """Test fetching a large file returns correct size"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Create large content (1MB)
            large_content = b"x" * (1024 * 1024)
            
            # Mock file content API response
            content_api_response = Mock()
            content_api_response.status_code = 200
            content_api_response.json.return_value = {
                "download_url": "https://raw.githubusercontent.com/owner/repo/abc123/large.sh"
            }
            
            # Mock file download response
            download_response = Mock()
            download_response.status_code = 200
            download_response.content = large_content
            
            mock_session.get.side_effect = [
                auth_response,
                content_api_response,
                download_response
            ]
            
            client.authenticate("token")
            result = client.fetch_file(
                "https://github.com/owner/repo",
                "abc123",
                "large.sh"
            )
            
            assert result.size_bytes == 1024 * 1024
            assert len(result.content) == 1024 * 1024
    
    def test_fetch_empty_file(self, client):
        """Test fetching an empty file"""
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
                "download_url": "https://raw.githubusercontent.com/owner/repo/abc123/empty.sh"
            }
            
            # Mock file download response with empty content
            download_response = Mock()
            download_response.status_code = 200
            download_response.content = b""
            
            mock_session.get.side_effect = [
                auth_response,
                content_api_response,
                download_response
            ]
            
            client.authenticate("token")
            result = client.fetch_file(
                "https://github.com/owner/repo",
                "abc123",
                "empty.sh"
            )
            
            assert result.size_bytes == 0
            assert result.content == b""
    
    def test_file_size_in_response(self, client):
        """Test that FileContent includes accurate size information"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            test_content = b"#!/bin/bash\necho 'test'\n"
            
            # Mock file content API response
            content_api_response = Mock()
            content_api_response.status_code = 200
            content_api_response.json.return_value = {
                "download_url": "https://raw.githubusercontent.com/owner/repo/abc123/script.sh"
            }
            
            # Mock file download response
            download_response = Mock()
            download_response.status_code = 200
            download_response.content = test_content
            
            mock_session.get.side_effect = [
                auth_response,
                content_api_response,
                download_response
            ]
            
            client.authenticate("token")
            result = client.fetch_file(
                "https://github.com/owner/repo",
                "abc123",
                "script.sh"
            )
            
            # Verify size matches actual content length
            assert result.size_bytes == len(test_content)
            assert result.size_bytes == len(result.content)


class TestErrorHandling:
    """Tests for comprehensive error handling"""
    
    def test_fetch_file_directory_instead_of_file(self, client):
        """Test fetch_file when path points to a directory"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock directory response (GitHub returns list for directories)
            dir_response = Mock()
            dir_response.status_code = 200
            dir_response.json.return_value = [
                {"name": "file1.sh", "type": "file"},
                {"name": "file2.sh", "type": "file"}
            ]
            
            mock_session.get.side_effect = [
                auth_response,
                dir_response
            ]
            
            client.authenticate("token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(
                    "https://github.com/owner/repo",
                    "abc123",
                    "scripts"
                )
            
            assert exc_info.value.status_code == 400
            assert "directory" in exc_info.value.message.lower()
    
    def test_fetch_file_no_download_url(self, client):
        """Test fetch_file when GitHub response lacks download_url"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock response without download_url
            content_api_response = Mock()
            content_api_response.status_code = 200
            content_api_response.json.return_value = {
                "name": "script.sh",
                "type": "file"
                # Missing download_url
            }
            
            mock_session.get.side_effect = [
                auth_response,
                content_api_response
            ]
            
            client.authenticate("token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(
                    "https://github.com/owner/repo",
                    "abc123",
                    "script.sh"
                )
            
            assert exc_info.value.status_code == 500
            assert "download URL" in exc_info.value.message
    
    def test_fetch_file_download_fails(self, client):
        """Test fetch_file when content download fails"""
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
                "download_url": "https://raw.githubusercontent.com/owner/repo/abc123/script.sh"
            }
            
            # Mock failed download
            download_response = Mock()
            download_response.status_code = 500
            
            mock_session.get.side_effect = [
                auth_response,
                content_api_response,
                download_response
            ]
            
            client.authenticate("token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(
                    "https://github.com/owner/repo",
                    "abc123",
                    "script.sh"
                )
            
            assert "Failed to download file content" in exc_info.value.message
    
    def test_fetch_file_network_error(self, client):
        """Test fetch_file with network error during file fetch"""
        import requests
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            mock_session.get.side_effect = [
                auth_response,
                requests.RequestException("Connection timeout")
            ]
            
            client.authenticate("token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(
                    "https://github.com/owner/repo",
                    "abc123",
                    "script.sh"
                )
            
            assert exc_info.value.status_code == 500
            assert "Network error" in exc_info.value.message
    
    def test_fetch_file_invalid_commit_format(self, client):
        """Test fetch_file with invalid commit hash format"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock 404 for file
            file_response = Mock()
            file_response.status_code = 404
            
            # Mock repo exists
            repo_response = Mock()
            repo_response.status_code = 200
            
            # Mock 422 for invalid commit format
            commit_response = Mock()
            commit_response.status_code = 422
            
            mock_session.get.side_effect = [
                auth_response,
                file_response,
                repo_response,
                commit_response
            ]
            
            client.authenticate("token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(
                    "https://github.com/owner/repo",
                    "invalid",
                    "script.sh"
                )
            
            assert exc_info.value.status_code == 400
            assert "Invalid commit hash" in exc_info.value.message
    
    def test_fetch_file_forbidden_access(self, client):
        """Test fetch_file with forbidden access (not rate limit)"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock 403 forbidden (not rate limit)
            forbidden_response = Mock()
            forbidden_response.status_code = 403
            forbidden_response.text = "Access forbidden"
            
            mock_session.get.side_effect = [
                auth_response,
                forbidden_response
            ]
            
            client.authenticate("token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(
                    "https://github.com/owner/repo",
                    "abc123",
                    "script.sh"
                )
            
            assert exc_info.value.status_code == 403
            assert "forbidden" in exc_info.value.message.lower()
    
    def test_authentication_unexpected_status(self, client):
        """Test authentication with unexpected status code"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            mock_response = Mock()
            mock_response.status_code = 500
            mock_session.get.return_value = mock_response
            
            result = client.authenticate("token")
            
            assert result.success is False
            assert "GitHub API error: 500" in result.error_message
            assert client._authenticated is False
    
    def test_fetch_file_unexpected_status(self, client):
        """Test fetch_file with unexpected status code"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Mock unexpected status
            unexpected_response = Mock()
            unexpected_response.status_code = 502
            
            mock_session.get.side_effect = [
                auth_response,
                unexpected_response
            ]
            
            client.authenticate("token")
            
            with pytest.raises(GitHubAPIError) as exc_info:
                client.fetch_file(
                    "https://github.com/owner/repo",
                    "abc123",
                    "script.sh"
                )
            
            assert exc_info.value.status_code == 502
            assert "GitHub API error: 502" in exc_info.value.message


class TestEdgeCases:
    """Tests for edge cases and boundary conditions"""
    
    def test_parse_url_with_multiple_slashes(self, client):
        """Test parsing URL with multiple trailing slashes"""
        owner, repo = client._parse_repo_url("https://github.com/owner/repo///")
        assert owner == "owner"
        assert repo == "repo"
    
    def test_parse_url_with_git_suffix_and_slash(self, client):
        """Test parsing URL with both .git suffix and trailing slash"""
        owner, repo = client._parse_repo_url("https://github.com/owner/repo.git/")
        assert owner == "owner"
        assert repo == "repo"
    
    def test_store_temp_file_with_special_characters(self, client, temp_dir):
        """Test storing file with special characters in name"""
        content = b"test content"
        temp_path = client._store_temp_file(
            content,
            "abc123",
            "scripts/my-script_v2.0.sh"
        )
        
        assert os.path.exists(temp_path)
        assert "my-script_v2.0.sh" in os.path.basename(temp_path)
        
        with open(temp_path, "rb") as f:
            assert f.read() == content
    
    def test_store_temp_file_creates_directory(self, client):
        """Test that temp storage directory is created if it doesn't exist"""
        nonexistent_dir = "/tmp/test_repo_client_nonexistent"
        
        # Ensure directory doesn't exist
        if os.path.exists(nonexistent_dir):
            os.rmdir(nonexistent_dir)
        
        client_new = RepositoryClient(temp_storage_path=nonexistent_dir)
        
        try:
            content = b"test"
            temp_path = client_new._store_temp_file(content, "abc123", "test.sh")
            
            assert os.path.exists(nonexistent_dir)
            assert os.path.exists(temp_path)
        finally:
            # Cleanup
            if os.path.exists(temp_path):
                os.remove(temp_path)
            if os.path.exists(nonexistent_dir):
                os.rmdir(nonexistent_dir)
    
    def test_fetch_file_with_binary_content(self, client):
        """Test fetching a binary file (non-text content)"""
        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            
            # Mock authentication
            auth_response = Mock()
            auth_response.status_code = 200
            
            # Binary content (e.g., a small image)
            binary_content = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
            
            # Mock file content API response
            content_api_response = Mock()
            content_api_response.status_code = 200
            content_api_response.json.return_value = {
                "download_url": "https://raw.githubusercontent.com/owner/repo/abc123/image.png"
            }
            
            # Mock file download response
            download_response = Mock()
            download_response.status_code = 200
            download_response.content = binary_content
            
            mock_session.get.side_effect = [
                auth_response,
                content_api_response,
                download_response
            ]
            
            client.authenticate("token")
            result = client.fetch_file(
                "https://github.com/owner/repo",
                "abc123",
                "image.png"
            )
            
            assert result.content == binary_content
            assert result.size_bytes == len(binary_content)
