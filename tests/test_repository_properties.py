"""Property-based tests for GitHub repository client

Feature: github-actions-remote-executor
Tests Properties 8, 9, 10, 11, 12, 13, 14, 145 from the design document
"""
import os
import tempfile
import pytest
from hypothesis import given, strategies as st, assume, settings
from unittest.mock import Mock, patch
from src.repository import RepositoryClient, AuthResult, GitHubAPIError
from src.models import CloneResult


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

            mock_response = Mock()
            mock_response.status_code = 200
            mock_session.get.return_value = mock_response

            result = client.authenticate(token)

            assert result.success is True
            assert result.error_message is None
            assert client._authenticated is True

            mock_session.headers.update.assert_called_once()
            call_args = mock_session.headers.update.call_args[0][0]
            assert 'Authorization' in call_args
            assert token in call_args['Authorization'] or 'Bearer' in call_args['Authorization']


# Property 9: Exact Commit Retrieval via Clone
# Feature: github-actions-remote-executor, Property 9: Exact Commit File Retrieval
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
)
@settings(max_examples=20)
def test_property_9_exact_commit_clone(repo_url, commit):
    """
    Property 9: For any valid repository and commit hash, the Repository Client
    should clone the repo and checkout the exact commit.

    Validates: Requirements 3.2
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)

        with patch('subprocess.run') as mock_run:
            clone_result = Mock(returncode=0, stdout="", stderr="")
            fetch_result = Mock(returncode=0, stdout="", stderr="")
            checkout_result = Mock(returncode=0, stdout="", stderr="")
            strip_result = Mock(returncode=0, stdout="", stderr="")
            mock_run.side_effect = [clone_result, fetch_result, checkout_result, strip_result]

            with patch('os.listdir', return_value=[".git", "file.txt"]):
                with patch('tempfile.mkdtemp', return_value=os.path.join(temp_dir, "clone")):
                    os.makedirs(os.path.join(temp_dir, "clone"), exist_ok=True)
                    os.makedirs(os.path.join(temp_dir, "clone", ".git"), exist_ok=True)
                    result = client.clone_repo(repo_url, commit, "test_token")

            assert isinstance(result, CloneResult)
            assert result.clone_path is not None

            # Verify git checkout was called with the exact commit
            checkout_call = mock_run.call_args_list[2]
            assert commit in checkout_call[0][0]


# Property 10: Authentication Failure Response
# Feature: github-actions-remote-executor, Property 10: Authentication Failure Response
@given(token=st.text(min_size=1, max_size=100))
def test_property_10_authentication_failure_response(token):
    """
    Property 10: For any invalid or expired GitHub token, the Repository Client
    should return an authentication error.

    Validates: Requirements 3.3
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)

        with patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session

            mock_response = Mock()
            mock_response.status_code = 401
            mock_session.get.return_value = mock_response

            result = client.authenticate(token)

            assert result.success is False
            assert result.error_message is not None
            assert len(result.error_message) > 0

            error_lower = result.error_message.lower()
            assert 'invalid' in error_lower or 'expired' in error_lower or 'token' in error_lower
            assert client._authenticated is False


# Property 11: Repository Not Found Response
# Feature: github-actions-remote-executor, Property 11: Repository Not Found Response
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
)
@settings(max_examples=20)
def test_property_11_repository_not_found_response(repo_url, commit):
    """
    Property 11: For any non-existent repository URL, the Repository Client
    should return HTTP 404 with a repository not found error.

    Validates: Requirements 3.4
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=128,
                stdout="",
                stderr="fatal: repository 'https://github.com/owner/repo.git/' not found"
            )

            with pytest.raises(GitHubAPIError) as exc_info:
                client.clone_repo(repo_url, commit, "test_token")

            assert exc_info.value.status_code == 404
            error_lower = exc_info.value.message.lower()
            assert 'not found' in error_lower


# Property 12: Commit Not Found Response
# Feature: github-actions-remote-executor, Property 12: Commit Not Found Response
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
)
@settings(max_examples=20)
def test_property_12_commit_not_found_response(repo_url, commit):
    """
    Property 12: For any non-existent commit hash in a valid repository, the
    Repository Client should return HTTP 404 with a commit not found error.

    Validates: Requirements 3.5
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)

        with patch('subprocess.run') as mock_run:
            clone_result = Mock(returncode=0, stdout="", stderr="")
            fetch_result = Mock(returncode=1, stdout="", stderr="")
            checkout_result = Mock(returncode=1, stdout="", stderr="error: pathspec did not match")
            mock_run.side_effect = [clone_result, fetch_result, checkout_result]

            with pytest.raises(GitHubAPIError) as exc_info:
                client.clone_repo(repo_url, commit, "test_token")

            assert exc_info.value.status_code == 404
            error_lower = exc_info.value.message.lower()
            assert 'commit' in error_lower and 'not found' in error_lower


# Property 13: File Not Found Response
# Feature: github-actions-remote-executor, Property 13: File Not Found Response
@given(
    path=valid_script_path()
)
@settings(max_examples=20)
def test_property_13_file_not_found_response(path):
    """
    Property 13: For any non-existent file path in a cloned repo, the Repository
    Client should raise a 404 error.

    Validates: Requirements 3.6
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)

        with pytest.raises(GitHubAPIError) as exc_info:
            client.validate_script_exists(temp_dir, path)

        assert exc_info.value.status_code == 404
        error_lower = exc_info.value.message.lower()
        assert 'file' in error_lower and 'not found' in error_lower


# Property 14: Temporary Repository Clone Storage
# Feature: github-actions-remote-executor, Property 14: Temporary Repository Clone Storage
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
)
@settings(max_examples=20)
def test_property_14_temporary_clone_storage(repo_url, commit):
    """
    Property 14: For any successfully cloned repository, the Repository Client
    should clone into a temporary directory under the configured temp storage path.

    Validates: Requirements 3.7
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)

        with patch('subprocess.run') as mock_run:
            clone_result = Mock(returncode=0, stdout="", stderr="")
            fetch_result = Mock(returncode=0, stdout="", stderr="")
            checkout_result = Mock(returncode=0, stdout="", stderr="")
            strip_result = Mock(returncode=0, stdout="", stderr="")
            mock_run.side_effect = [clone_result, fetch_result, checkout_result, strip_result]

            with patch('os.listdir', return_value=[".git", "README.md"]):
                with patch('shutil.rmtree'):
                    result = client.clone_repo(repo_url, commit, "test_token")

            assert result.clone_path is not None
            assert result.clone_path.startswith(temp_dir)

            # Commit hash prefix should be in the directory name
            dirname = os.path.basename(result.clone_path)
            assert commit[:8] in dirname


# Property 145: Token Stripping and .git Removal
# Feature: github-actions-remote-executor, Property 145: Token Stripping and .git Removal
@given(
    repo_url=valid_github_url(),
    commit=valid_commit_hash(),
    token=valid_github_token(),
)
@settings(max_examples=20)
def test_property_145_token_stripping_and_git_removal(repo_url, commit, token):
    """
    Property 145: For any successfully cloned repository, the Repository_Client
    should strip the GitHub token from .git/config and then remove the .git
    directory entirely before the repository is mounted into the Execution_Container.

    Validates: Requirements 3.10, 3.11, 3.12
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        client = RepositoryClient(temp_storage_path=temp_dir)

        with patch('subprocess.run') as mock_run:
            clone_result_mock = Mock(returncode=0, stdout="", stderr="")
            fetch_result_mock = Mock(returncode=0, stdout="", stderr="")
            checkout_result_mock = Mock(returncode=0, stdout="", stderr="")
            strip_result_mock = Mock(returncode=0, stdout="", stderr="")
            mock_run.side_effect = [
                clone_result_mock,
                fetch_result_mock,
                checkout_result_mock,
                strip_result_mock,
            ]

            clone_dir = os.path.join(temp_dir, "clone")
            with patch('os.listdir', return_value=[".git", "file.txt"]):
                with patch('tempfile.mkdtemp', return_value=clone_dir):
                    os.makedirs(clone_dir, exist_ok=True)
                    # Create a .git directory so shutil.rmtree can remove it
                    os.makedirs(os.path.join(clone_dir, ".git"), exist_ok=True)

                    result = client.clone_repo(repo_url, commit, token)

            assert isinstance(result, CloneResult)

            # Verify subprocess calls
            assert mock_run.call_count == 4
            calls = mock_run.call_args_list

            # 4th call should be git remote set-url with clean URL (no token)
            strip_call_args = calls[3][0][0]
            assert strip_call_args[0] == "git"
            assert strip_call_args[1] == "remote"
            assert strip_call_args[2] == "set-url"
            assert strip_call_args[3] == "origin"
            clean_url = strip_call_args[4]
            assert token not in clean_url
            assert "github.com/" in clean_url
            assert clean_url.endswith(".git")

            # Verify .git directory was removed
            assert not os.path.exists(os.path.join(clone_dir, ".git"))
