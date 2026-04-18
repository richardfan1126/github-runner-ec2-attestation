"""GitHub repository client for cloning repositories"""
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from src.models import CloneResult

logger = logging.getLogger(__name__)


@dataclass
class AuthResult:
    """Result of GitHub authentication"""
    success: bool
    error_message: Optional[str] = None


class GitHubAPIError(Exception):
    """Base exception for GitHub API errors"""
    def __init__(self, message: str, status_code: int):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class RepositoryClient:
    """Client for cloning GitHub repositories"""

    def __init__(self, temp_storage_path: str):
        """
        Initialize repository client

        Args:
            temp_storage_path: Base path for storing cloned repositories
        """
        self.temp_storage_path = temp_storage_path
        self._token: Optional[str] = None
        self._authenticated = False

    def authenticate(self, token: str) -> AuthResult:
        """
        Store GitHub token for use in clone operations.

        Args:
            token: GitHub personal access token or Actions token

        Returns:
            AuthResult indicating success or failure
        """
        import requests
        try:
            session = requests.Session()
            session.headers.update({
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "GitHub-Actions-Remote-Executor/1.0"
            })
            response = session.get("https://api.github.com/rate_limit")
            if response.status_code == 200:
                self._token = token
                self._authenticated = True
                return AuthResult(success=True)
            elif response.status_code in (401, 403):
                self._authenticated = False
                return AuthResult(
                    success=False,
                    error_message="Invalid or expired GitHub token"
                )
            else:
                self._authenticated = False
                return AuthResult(
                    success=False,
                    error_message=f"GitHub API error: {response.status_code}"
                )
        except requests.RequestException as e:
            self._authenticated = False
            return AuthResult(
                success=False,
                error_message=f"Network error during authentication: {str(e)}"
            )

    def clone_repo(self, repo_url: str, commit: str, token: str) -> CloneResult:
        """
        Clone a repository at a specific commit into a temp directory.

        Uses `git clone --depth 1` with the token embedded in the URL,
        then checks out the exact commit.

        Args:
            repo_url: GitHub repository URL (e.g., https://github.com/owner/repo)
            commit: Git commit SHA to checkout
            token: GitHub token for authentication

        Returns:
            CloneResult with clone_path and script_path

        Raises:
            GitHubAPIError: For clone failures with appropriate status codes
        """
        owner, repo = self._parse_repo_url(repo_url)
        clone_url = f"https://{token}@github.com/{owner}/{repo}.git"

        os.makedirs(self.temp_storage_path, exist_ok=True)
        clone_dir = tempfile.mkdtemp(dir=self.temp_storage_path, prefix=f"{commit[:8]}_")

        try:
            # Shallow clone
            result = subprocess.run(
                ["git", "clone", "--depth", "1", clone_url, clone_dir],
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )

            if result.returncode != 0:
                stderr = result.stderr.lower()
                if "authentication" in stderr or "could not read" in stderr or "403" in stderr:
                    raise GitHubAPIError("Authentication failed during clone", 401)
                elif "not found" in stderr or "does not exist" in stderr or "repository" in stderr:
                    raise GitHubAPIError(f"Repository not found: {owner}/{repo}", 404)
                else:
                    raise GitHubAPIError(
                        f"Clone failed: {result.stderr.strip()}", 500
                    )

            # Fetch the specific commit and checkout
            fetch_result = subprocess.run(
                ["git", "fetch", "origin", commit],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=clone_dir,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )

            if fetch_result.returncode != 0:
                # Commit might already be in the shallow clone
                pass

            checkout_result = subprocess.run(
                ["git", "checkout", commit],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=clone_dir,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )

            if checkout_result.returncode != 0:
                raise GitHubAPIError(f"Commit not found: {commit}", 404)

            # Validate the clone is not empty
            entries = os.listdir(clone_dir)
            non_git = [e for e in entries if e != ".git"]
            if not non_git:
                raise GitHubAPIError("Cloned repository is empty", 400)

            # Strip the GitHub token from .git/config
            clean_url = f"https://github.com/{owner}/{repo}.git"
            strip_result = subprocess.run(
                ["git", "remote", "set-url", "origin", clean_url],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=clone_dir,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if strip_result.returncode == 0:
                logger.info("Stripped GitHub token from .git/config")
            else:
                logger.warning(f"Failed to strip token from .git/config: {strip_result.stderr.strip()}")

            # Remove the .git directory entirely
            git_dir = os.path.join(clone_dir, ".git")
            shutil.rmtree(git_dir)
            logger.info("Removed .git directory from cloned repository")

            return CloneResult(clone_path=clone_dir, script_path="")

        except subprocess.TimeoutExpired:
            self.cleanup_clone(clone_dir)
            raise GitHubAPIError("Clone operation timed out", 500)
        except GitHubAPIError:
            self.cleanup_clone(clone_dir)
            raise
        except Exception as e:
            self.cleanup_clone(clone_dir)
            raise GitHubAPIError(f"Network error: {str(e)}", 500)

    def validate_script_exists(self, clone_path: str, script_path: str) -> bool:
        """
        Validate that a script file exists within the cloned repository.

        Args:
            clone_path: Path to the cloned repository directory
            script_path: Relative path to the script within the repo

        Returns:
            True if the file exists

        Raises:
            GitHubAPIError: If the file does not exist (404)
        """
        full_path = os.path.join(clone_path, script_path)
        if not os.path.isfile(full_path):
            raise GitHubAPIError(f"File not found: {script_path}", 404)
        return True

    def cleanup_clone(self, clone_path: str) -> None:
        """
        Remove a cloned repository directory.

        Args:
            clone_path: Path to the cloned repository directory to remove
        """
        try:
            if clone_path and os.path.exists(clone_path):
                shutil.rmtree(clone_path)
        except OSError as e:
            logger.warning(f"Failed to clean up clone directory {clone_path}: {e}")

    def _parse_repo_url(self, repo_url: str) -> tuple[str, str]:
        """
        Parse GitHub repository URL to extract owner and repo name.

        Args:
            repo_url: GitHub repository URL

        Returns:
            Tuple of (owner, repo)

        Raises:
            GitHubAPIError: If URL format is invalid
        """
        url = repo_url.rstrip("/").removesuffix(".git")

        if "github.com/" in url:
            parts = url.split("github.com/")[-1].split("/")
        elif "github.com:" in url:
            parts = url.split("github.com:")[-1].split("/")
        else:
            raise GitHubAPIError(f"Invalid GitHub repository URL: {repo_url}", 400)

        if len(parts) < 2:
            raise GitHubAPIError(f"Invalid GitHub repository URL: {repo_url}", 400)

        return parts[0], parts[1]
