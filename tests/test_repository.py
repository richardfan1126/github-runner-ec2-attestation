"""Unit tests for GitHub repository client"""
import os
import tempfile
import pytest
from unittest.mock import Mock, patch, MagicMock
from src.repository import RepositoryClient, AuthResult, GitHubAPIError
from src.models import CloneResult


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


class TestCloneRepo:
    """Tests for repository cloning"""

    def test_clone_repo_success(self, client, temp_dir):
        """Test successful repository clone"""
        with patch('subprocess.run') as mock_run:
            # Mock clone success
            clone_result = Mock(returncode=0, stdout="", stderr="")
            # Mock fetch success
            fetch_result = Mock(returncode=0, stdout="", stderr="")
            # Mock checkout success
            checkout_result = Mock(returncode=0, stdout="", stderr="")
            # Mock token strip success
            strip_result = Mock(returncode=0, stdout="", stderr="")
            mock_run.side_effect = [clone_result, fetch_result, checkout_result, strip_result]

            # Create a dummy file in the clone dir so it's not "empty"
            with patch('os.listdir', return_value=[".git", "script.sh"]):
                with patch('tempfile.mkdtemp', return_value=os.path.join(temp_dir, "abc12345_clone")):
                    os.makedirs(os.path.join(temp_dir, "abc12345_clone"), exist_ok=True)
                    os.makedirs(os.path.join(temp_dir, "abc12345_clone", ".git"), exist_ok=True)
                    result = client.clone_repo(
                        "https://github.com/owner/repo",
                        "abc123def456abc123def456abc123def456abc1",
                        "ghp_token123"
                    )

            assert isinstance(result, CloneResult)
            assert result.clone_path.startswith(temp_dir)

    def test_clone_repo_auth_failure(self, client, temp_dir):
        """Test clone failure due to authentication"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=128,
                stdout="",
                stderr="fatal: Authentication failed for 'https://github.com/owner/repo.git'"
            )

            with pytest.raises(GitHubAPIError) as exc_info:
                client.clone_repo(
                    "https://github.com/owner/repo",
                    "abc123def456abc123def456abc123def456abc1",
                    "bad_token"
                )

            assert exc_info.value.status_code == 401
            assert "Authentication" in exc_info.value.message

    def test_clone_repo_not_found(self, client, temp_dir):
        """Test clone failure due to repository not found"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=128,
                stdout="",
                stderr="fatal: repository 'https://github.com/owner/nonexistent.git/' not found"
            )

            with pytest.raises(GitHubAPIError) as exc_info:
                client.clone_repo(
                    "https://github.com/owner/nonexistent",
                    "abc123def456abc123def456abc123def456abc1",
                    "ghp_token123"
                )

            assert exc_info.value.status_code == 404
            assert "not found" in exc_info.value.message.lower()

    def test_clone_repo_commit_not_found(self, client, temp_dir):
        """Test clone when commit doesn't exist"""
        with patch('subprocess.run') as mock_run:
            # Clone succeeds
            clone_result = Mock(returncode=0, stdout="", stderr="")
            # Fetch fails (commit not in remote)
            fetch_result = Mock(returncode=1, stdout="", stderr="")
            # Checkout fails
            checkout_result = Mock(returncode=1, stdout="", stderr="error: pathspec 'badcommit' did not match")
            mock_run.side_effect = [clone_result, fetch_result, checkout_result]

            with pytest.raises(GitHubAPIError) as exc_info:
                client.clone_repo(
                    "https://github.com/owner/repo",
                    "badcommitbadcommitbadcommitbadcommitbadc",
                    "ghp_token123"
                )

            assert exc_info.value.status_code == 404
            assert "Commit not found" in exc_info.value.message

    def test_clone_repo_timeout(self, client, temp_dir):
        """Test clone timeout"""
        import subprocess
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git clone", timeout=120)

            with pytest.raises(GitHubAPIError) as exc_info:
                client.clone_repo(
                    "https://github.com/owner/repo",
                    "abc123def456abc123def456abc123def456abc1",
                    "ghp_token123"
                )

            assert exc_info.value.status_code == 500
            assert "timed out" in exc_info.value.message.lower()

    def test_clone_repo_network_error(self, client, temp_dir):
        """Test clone with network error"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=128,
                stdout="",
                stderr="fatal: unable to access 'https://github.com/owner/repo.git/': Could not resolve host"
            )

            with pytest.raises(GitHubAPIError) as exc_info:
                client.clone_repo(
                    "https://github.com/owner/repo",
                    "abc123def456abc123def456abc123def456abc1",
                    "ghp_token123"
                )

            assert exc_info.value.status_code == 500


class TestValidateScriptExists:
    """Tests for script existence validation"""

    def test_script_exists(self, client, temp_dir):
        """Test validation when script exists"""
        script_path = os.path.join(temp_dir, "script.sh")
        with open(script_path, "w") as f:
            f.write("#!/bin/bash\necho hello")

        assert client.validate_script_exists(temp_dir, "script.sh") is True

    def test_script_not_found(self, client, temp_dir):
        """Test validation when script doesn't exist"""
        with pytest.raises(GitHubAPIError) as exc_info:
            client.validate_script_exists(temp_dir, "nonexistent.sh")

        assert exc_info.value.status_code == 404
        assert "File not found" in exc_info.value.message

    def test_script_is_directory(self, client, temp_dir):
        """Test validation when path is a directory, not a file"""
        os.makedirs(os.path.join(temp_dir, "scripts"), exist_ok=True)

        with pytest.raises(GitHubAPIError) as exc_info:
            client.validate_script_exists(temp_dir, "scripts")

        assert exc_info.value.status_code == 404


class TestCleanupClone:
    """Tests for clone cleanup"""

    def test_cleanup_clone_success(self, client, temp_dir):
        """Test successful cleanup of cloned directory"""
        clone_dir = os.path.join(temp_dir, "clone_test")
        os.makedirs(clone_dir)
        with open(os.path.join(clone_dir, "file.txt"), "w") as f:
            f.write("test")

        client.cleanup_clone(clone_dir)
        assert not os.path.exists(clone_dir)

    def test_cleanup_nonexistent_directory(self, client):
        """Test cleanup of non-existent directory doesn't raise"""
        client.cleanup_clone("/nonexistent/path/clone")

    def test_cleanup_empty_path(self, client):
        """Test cleanup with empty path doesn't raise"""
        client.cleanup_clone("")

    def test_cleanup_none_path(self, client):
        """Test cleanup with None path doesn't raise"""
        client.cleanup_clone(None)


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


class TestTokenStrippingAndGitRemoval:
    """Tests for token stripping and .git directory removal (Requirements 3.10, 3.11, 3.12)"""

    def test_git_remote_set_url_called_with_clean_url(self, client, temp_dir):
        """Test that git remote set-url is called after clone with the correct clean URL"""
        with patch('subprocess.run') as mock_run:
            clone_result = Mock(returncode=0, stdout="", stderr="")
            fetch_result = Mock(returncode=0, stdout="", stderr="")
            checkout_result = Mock(returncode=0, stdout="", stderr="")
            strip_result = Mock(returncode=0, stdout="", stderr="")
            mock_run.side_effect = [clone_result, fetch_result, checkout_result, strip_result]

            clone_dir = os.path.join(temp_dir, "abc12345_clone")
            with patch('os.listdir', return_value=[".git", "script.sh"]):
                with patch('tempfile.mkdtemp', return_value=clone_dir):
                    os.makedirs(clone_dir, exist_ok=True)
                    os.makedirs(os.path.join(clone_dir, ".git"), exist_ok=True)

                    client.clone_repo(
                        "https://github.com/myowner/myrepo",
                        "abc123def456abc123def456abc123def456abc1",
                        "ghp_secrettoken123"
                    )

            # 4th subprocess call should be git remote set-url
            strip_call = mock_run.call_args_list[3]
            assert strip_call[0][0] == [
                "git", "remote", "set-url", "origin",
                "https://github.com/myowner/myrepo.git"
            ]
            # Token must not appear in the clean URL
            assert "ghp_secrettoken123" not in strip_call[0][0][4]

    def test_git_directory_removed_after_token_stripping(self, client, temp_dir):
        """Test that .git directory is removed after token stripping"""
        with patch('subprocess.run') as mock_run:
            clone_result = Mock(returncode=0, stdout="", stderr="")
            fetch_result = Mock(returncode=0, stdout="", stderr="")
            checkout_result = Mock(returncode=0, stdout="", stderr="")
            strip_result = Mock(returncode=0, stdout="", stderr="")
            mock_run.side_effect = [clone_result, fetch_result, checkout_result, strip_result]

            clone_dir = os.path.join(temp_dir, "abc12345_clone")
            with patch('os.listdir', return_value=[".git", "script.sh"]):
                with patch('tempfile.mkdtemp', return_value=clone_dir):
                    os.makedirs(clone_dir, exist_ok=True)
                    os.makedirs(os.path.join(clone_dir, ".git"), exist_ok=True)

                    result = client.clone_repo(
                        "https://github.com/owner/repo",
                        "abc123def456abc123def456abc123def456abc1",
                        "ghp_token123"
                    )

            # .git directory should have been removed
            assert not os.path.exists(os.path.join(clone_dir, ".git"))

    def test_operation_ordering_clone_strip_remove(self, client, temp_dir):
        """Test ordering: clone → checkout → strip token → remove .git → return result"""
        call_order = []

        with patch('subprocess.run') as mock_run:
            def track_subprocess(*args, **kwargs):
                cmd = args[0]
                if cmd[0] == "git" and cmd[1] == "clone":
                    call_order.append("clone")
                    return Mock(returncode=0, stdout="", stderr="")
                elif cmd[0] == "git" and cmd[1] == "fetch":
                    call_order.append("fetch")
                    return Mock(returncode=0, stdout="", stderr="")
                elif cmd[0] == "git" and cmd[1] == "checkout":
                    call_order.append("checkout")
                    return Mock(returncode=0, stdout="", stderr="")
                elif cmd[0] == "git" and cmd[1] == "remote":
                    call_order.append("strip_token")
                    return Mock(returncode=0, stdout="", stderr="")
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = track_subprocess

            clone_dir = os.path.join(temp_dir, "abc12345_clone")
            with patch('os.listdir', return_value=[".git", "script.sh"]):
                with patch('tempfile.mkdtemp', return_value=clone_dir):
                    os.makedirs(clone_dir, exist_ok=True)
                    git_dir = os.path.join(clone_dir, ".git")
                    os.makedirs(git_dir, exist_ok=True)

                    with patch('shutil.rmtree') as mock_rmtree:
                        def track_rmtree(path):
                            if ".git" in path:
                                call_order.append("remove_git")
                        mock_rmtree.side_effect = track_rmtree

                        result = client.clone_repo(
                            "https://github.com/owner/repo",
                            "abc123def456abc123def456abc123def456abc1",
                            "ghp_token123"
                        )

            assert isinstance(result, CloneResult)
            assert call_order == ["clone", "fetch", "checkout", "strip_token", "remove_git"]
