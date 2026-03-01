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
