"""GitHub repository client for fetching files"""
import os
import tempfile
from dataclasses import dataclass
from typing import Optional
import requests


@dataclass
class AuthResult:
    """Result of GitHub authentication"""
    success: bool
    error_message: Optional[str] = None


@dataclass
class FileContent:
    """Content of a fetched file"""
    content: bytes
    temp_path: str
    size_bytes: int


class GitHubAPIError(Exception):
    """Base exception for GitHub API errors"""
    def __init__(self, message: str, status_code: int):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class RepositoryClient:
    """Client for fetching files from GitHub repositories"""
    
    def __init__(self, temp_storage_path: str):
        """
        Initialize repository client
        
        Args:
            temp_storage_path: Base path for storing temporary files
        """
        self.temp_storage_path = temp_storage_path
        self._session: Optional[requests.Session] = None
        self._authenticated = False
    
    def authenticate(self, token: str) -> AuthResult:
        """
        Authenticate with GitHub using token
        
        Args:
            token: GitHub personal access token or Actions token
            
        Returns:
            AuthResult indicating success or failure
        """
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHub-Actions-Remote-Executor/1.0"
        })
        
        # Verify authentication by making a test request
        try:
            response = self._session.get("https://api.github.com/user")
            if response.status_code == 200:
                self._authenticated = True
                return AuthResult(success=True)
            elif response.status_code == 401:
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
    
    def fetch_file(self, repo_url: str, commit: str, path: str) -> FileContent:
        """
        Fetch file content from specific commit
        
        Args:
            repo_url: GitHub repository URL (e.g., https://github.com/owner/repo)
            commit: Git commit SHA
            path: Path to file in repository
            
        Returns:
            FileContent with file data and temporary storage location
            
        Raises:
            GitHubAPIError: For various GitHub API errors with appropriate status codes
        """
        if not self._authenticated or self._session is None:
            raise GitHubAPIError("Not authenticated", 401)
        
        # Extract owner and repo from URL
        owner, repo = self._parse_repo_url(repo_url)
        
        # Construct GitHub API URL for file content
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        params = {"ref": commit}
        
        try:
            response = self._session.get(api_url, params=params)
            
            # Handle different error cases
            if response.status_code == 401:
                raise GitHubAPIError("Authentication failed", 401)
            elif response.status_code == 404:
                # Determine what's not found by checking repository existence
                self._check_repository_exists(owner, repo)
                self._check_commit_exists(owner, repo, commit)
                # If we get here, the file doesn't exist
                raise GitHubAPIError(f"File not found: {path}", 404)
            elif response.status_code == 403:
                # Check if it's a rate limit
                if "rate limit" in response.text.lower():
                    raise GitHubAPIError("GitHub API rate limit exceeded", 429)
                raise GitHubAPIError("Access forbidden", 403)
            elif response.status_code != 200:
                raise GitHubAPIError(
                    f"GitHub API error: {response.status_code}",
                    response.status_code
                )
            
            # Get file content (GitHub returns base64 encoded)
            data = response.json()
            
            # Handle directory case
            if isinstance(data, list):
                raise GitHubAPIError(f"Path is a directory, not a file: {path}", 400)
            
            # Download raw content
            download_url = data.get("download_url")
            if not download_url:
                raise GitHubAPIError("No download URL in response", 500)
            
            content_response = self._session.get(download_url)
            if content_response.status_code != 200:
                raise GitHubAPIError(
                    f"Failed to download file content: {content_response.status_code}",
                    content_response.status_code
                )
            
            content = content_response.content
            
            # Store in temporary secure location
            temp_path = self._store_temp_file(content, commit, path)
            
            return FileContent(
                content=content,
                temp_path=temp_path,
                size_bytes=len(content)
            )
            
        except requests.RequestException as e:
            raise GitHubAPIError(f"Network error: {str(e)}", 500)
    
    def _parse_repo_url(self, repo_url: str) -> tuple[str, str]:
        """
        Parse GitHub repository URL to extract owner and repo name
        
        Args:
            repo_url: GitHub repository URL
            
        Returns:
            Tuple of (owner, repo)
            
        Raises:
            GitHubAPIError: If URL format is invalid
        """
        # Remove trailing slashes and .git suffix
        url = repo_url.rstrip("/").removesuffix(".git")
        
        # Handle both https://github.com/owner/repo and git@github.com:owner/repo
        if "github.com/" in url:
            parts = url.split("github.com/")[-1].split("/")
        elif "github.com:" in url:
            parts = url.split("github.com:")[-1].split("/")
        else:
            raise GitHubAPIError(f"Invalid GitHub repository URL: {repo_url}", 400)
        
        if len(parts) < 2:
            raise GitHubAPIError(f"Invalid GitHub repository URL: {repo_url}", 400)
        
        return parts[0], parts[1]
    
    def _check_repository_exists(self, owner: str, repo: str) -> None:
        """
        Check if repository exists
        
        Raises:
            GitHubAPIError: If repository doesn't exist
        """
        if self._session is None:
            raise GitHubAPIError("Not authenticated", 401)
        
        response = self._session.get(f"https://api.github.com/repos/{owner}/{repo}")
        if response.status_code == 404:
            raise GitHubAPIError(f"Repository not found: {owner}/{repo}", 404)
    
    def _check_commit_exists(self, owner: str, repo: str, commit: str) -> None:
        """
        Check if commit exists in repository
        
        Raises:
            GitHubAPIError: If commit doesn't exist
        """
        if self._session is None:
            raise GitHubAPIError("Not authenticated", 401)
        
        response = self._session.get(
            f"https://api.github.com/repos/{owner}/{repo}/commits/{commit}"
        )
        if response.status_code == 404:
            raise GitHubAPIError(f"Commit not found: {commit}", 404)
        elif response.status_code == 422:
            raise GitHubAPIError(f"Invalid commit hash: {commit}", 400)
    
    def _store_temp_file(self, content: bytes, commit: str, path: str) -> str:
        """
        Store file content in temporary secure location
        
        Args:
            content: File content bytes
            commit: Commit hash (for unique naming)
            path: Original file path (for extension)
            
        Returns:
            Path to temporary file
        """
        # Create temp directory if it doesn't exist
        os.makedirs(self.temp_storage_path, exist_ok=True)
        
        # Create a temporary file with secure permissions
        fd, temp_path = tempfile.mkstemp(
            dir=self.temp_storage_path,
            prefix=f"{commit[:8]}_",
            suffix=f"_{os.path.basename(path)}"
        )
        
        try:
            # Write content and close file descriptor
            os.write(fd, content)
        finally:
            os.close(fd)
        
        # Set restrictive permissions (owner read/write only)
        os.chmod(temp_path, 0o600)
        
        return temp_path
    
    def cleanup_temp_file(self, temp_path: str) -> None:
        """
        Clean up temporary file
        
        Args:
            temp_path: Path to temporary file to remove
        """
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            # Log but don't raise - cleanup is best effort
            pass
