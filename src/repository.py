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

    def _create_askpass_helper(self, token: str) -> str:
        """
        Create a temporary GIT_ASKPASS helper script that provides the token
        without exposing it in subprocess argv or /proc/<pid>/cmdline.

        The script uses a heredoc-style approach to avoid shell metacharacter
        issues with the token value. GitHub tokens (ghp_*, ghs_*, github_pat_*)
        contain only [A-Za-z0-9_] characters, but we escape defensively.

        Args:
            token: GitHub token to provide via the helper

        Returns:
            Path to the temporary helper script
        """
        # Escape single quotes in token for safe shell embedding
        # Replace ' with '\'' (end quote, escaped quote, start quote)
        safe_token = token.replace("'", "'\\''")
        fd, helper_path = tempfile.mkstemp(prefix="git_askpass_", suffix=".sh")
        try:
            os.write(fd, f"#!/bin/sh\necho '{safe_token}'\n".encode())
        finally:
            os.close(fd)
        os.chmod(helper_path, 0o700)
        return helper_path

    def clone_repo(self, repo_url: str, commit: str, token: str) -> CloneResult:
        """
        Clone a repository at a specific commit into a temp directory.

        Uses a GIT_ASKPASS helper script to provide credentials without
        exposing the token in subprocess argv or /proc/<pid>/cmdline.

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
        clone_url = f"https://x-access-token@github.com/{owner}/{repo}.git"

        os.makedirs(self.temp_storage_path, exist_ok=True)
        clone_dir = tempfile.mkdtemp(dir=self.temp_storage_path, prefix=f"{commit[:8]}_")

        helper_path = self._create_askpass_helper(token)
        try:
            clone_env = {
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": helper_path,
            }

            # Shallow clone using GIT_ASKPASS for credential delivery
            result = subprocess.run(
                ["git", "clone", "--depth", "1", clone_url, clone_dir],
                capture_output=True,
                text=True,
                timeout=120,
                env=clone_env,
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
                env=clone_env,
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

            # Strip the GitHub token from .git/config as defense-in-depth
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
        finally:
            # Always clean up the askpass helper script
            try:
                if os.path.exists(helper_path):
                    os.unlink(helper_path)
                    logger.debug("Cleaned up GIT_ASKPASS helper script")
            except OSError as e:
                logger.warning(f"Failed to clean up GIT_ASKPASS helper: {e}")

    def validate_script_exists(self, clone_path: str, script_path: str) -> bool:
        """
        Validate that a script file exists within the cloned repository.

        Performs symlink-safe validation:
        1. Rejects script paths that are symlinks
        2. Resolves the real path and verifies it stays within the clone directory

        Args:
            clone_path: Path to the cloned repository directory
            script_path: Relative path to the script within the repo

        Returns:
            True if the file exists and passes symlink safety checks

        Raises:
            GitHubAPIError: If the file does not exist (404), is a symlink (400),
                           or resolves outside the clone directory (400)
        """
        full_path = os.path.join(clone_path, script_path)
        if not os.path.exists(full_path):
            raise GitHubAPIError(f"File not found: {script_path}", 404)

        # Reject symlinks — repository-controlled symlinks could point outside
        # the clone directory or to sensitive host files
        if os.path.islink(full_path):
            raise GitHubAPIError(
                "Script path is a symlink; symlinks are not allowed", 400
            )

        # Resolve the real path and verify it stays within the clone directory.
        # This catches path traversal via intermediate symlinked directories
        # (e.g., dir -> /etc, then dir/passwd as script_path).
        real_clone = os.path.realpath(clone_path)
        real_script = os.path.realpath(full_path)
        if not real_script.startswith(real_clone + os.sep) and real_script != real_clone:
            raise GitHubAPIError(
                "Script path resolves outside the clone directory", 400
            )

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
