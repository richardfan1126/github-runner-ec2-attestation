"""Request validation for GitHub Actions Remote Executor"""
import re
from dataclasses import dataclass
from typing import Optional, List
from src.models import ExecutionRequest


@dataclass
class ValidationResult:
    """Result of validation with optional error messages"""
    valid: bool
    errors: List[str]
    
    @classmethod
    def success(cls) -> "ValidationResult":
        """Create a successful validation result"""
        return cls(valid=True, errors=[])
    
    @classmethod
    def failure(cls, *errors: str) -> "ValidationResult":
        """Create a failed validation result with error messages"""
        return cls(valid=False, errors=list(errors))


class RequestValidator:
    """Validates execution requests and their components"""
    
    # GitHub URL pattern: https://github.com/owner/repo
    GITHUB_URL_PATTERN = re.compile(
        r'^https://github\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+/?$'
    )
    
    # Git commit SHA pattern: 40 hexadecimal characters
    COMMIT_HASH_PATTERN = re.compile(r'^[0-9a-f]{40}$')
    
    # Path traversal patterns to detect
    PATH_TRAVERSAL_PATTERNS = ['../', '..\\', '/../', '\\..\\']
    
    def validate_execution_request(self, request: dict) -> ValidationResult:
        """
        Validates execution request structure and fields.
        
        Args:
            request: Dictionary containing request data
            
        Returns:
            ValidationResult with validation status and any error messages
        """
        errors = []
        
        # Check for required fields
        required_fields = ['repository_url', 'commit_hash', 'script_path', 'github_token']
        for field in required_fields:
            if field not in request:
                errors.append(f"Missing required field: {field}")
            elif not request[field]:
                errors.append(f"Field cannot be empty: {field}")
        
        # If required fields are missing, return early
        if errors:
            return ValidationResult.failure(*errors)
        
        # Validate repository URL format
        if not self.validate_repository_url(request['repository_url']):
            errors.append(
                f"Invalid repository URL format: {request['repository_url']}. "
                "Must be a valid GitHub repository URL (https://github.com/owner/repo)"
            )
        
        # Validate commit hash format
        if not self.validate_commit_hash(request['commit_hash']):
            errors.append(
                f"Invalid commit hash format: {request['commit_hash']}. "
                "Must be a 40-character hexadecimal SHA"
            )
        
        # Validate script path
        if not self.validate_script_path(request['script_path']):
            errors.append(
                f"Invalid script path: {request['script_path']}. "
                "Path must be non-empty and cannot contain path traversal sequences"
            )
        
        if errors:
            return ValidationResult.failure(*errors)
        
        return ValidationResult.success()
    
    def validate_repository_url(self, url: str) -> bool:
        """
        Validates GitHub repository URL format.
        
        Args:
            url: Repository URL to validate
            
        Returns:
            True if URL is valid GitHub format, False otherwise
        """
        if not url:
            return False
        
        return bool(self.GITHUB_URL_PATTERN.match(url))
    
    def validate_commit_hash(self, hash: str) -> bool:
        """
        Validates Git commit SHA format.
        
        Args:
            hash: Commit hash to validate
            
        Returns:
            True if hash is valid 40-character hex SHA, False otherwise
        """
        if not hash:
            return False
        
        return bool(self.COMMIT_HASH_PATTERN.match(hash))
    
    def validate_script_path(self, path: str) -> bool:
        """
        Validates script file path.
        
        Args:
            path: Script file path to validate
            
        Returns:
            True if path is valid (non-empty, no path traversal), False otherwise
        """
        if not path or not path.strip():
            return False
        
        # Check for path traversal attempts
        for pattern in self.PATH_TRAVERSAL_PATTERNS:
            if pattern in path:
                return False
        
        return True
