"""Unit tests for request validation"""
import pytest
from src.validation import RequestValidator, ValidationResult


@pytest.fixture
def validator():
    """Create a RequestValidator instance"""
    return RequestValidator()


def test_validation_result_success():
    """Test ValidationResult success creation"""
    result = ValidationResult.success()
    assert result.valid is True
    assert result.errors == []


def test_validation_result_failure():
    """Test ValidationResult failure creation"""
    result = ValidationResult.failure("Error 1", "Error 2")
    assert result.valid is False
    assert len(result.errors) == 2
    assert "Error 1" in result.errors
    assert "Error 2" in result.errors


def test_validate_execution_request_success(validator):
    """Test successful validation of complete request"""
    request = {
        "repository_url": "https://github.com/owner/repo",
        "commit_hash": "a" * 40,
        "script_path": "scripts/test.sh",
        "github_token": "ghp_test123",
    }
    
    result = validator.validate_execution_request(request)
    assert result.valid is True
    assert result.errors == []


def test_validate_execution_request_missing_fields(validator):
    """Test validation fails with missing required fields"""
    request = {
        "repository_url": "https://github.com/owner/repo",
        # Missing commit_hash, script_path, github_token
    }
    
    result = validator.validate_execution_request(request)
    assert result.valid is False
    assert len(result.errors) == 3
    assert any("commit_hash" in err for err in result.errors)
    assert any("script_path" in err for err in result.errors)
    assert any("github_token" in err for err in result.errors)


def test_validate_execution_request_empty_fields(validator):
    """Test validation fails with empty required fields"""
    request = {
        "repository_url": "",
        "commit_hash": "",
        "script_path": "",
        "github_token": "",
    }
    
    result = validator.validate_execution_request(request)
    assert result.valid is False
    assert len(result.errors) >= 4


def test_validate_execution_request_invalid_url(validator):
    """Test validation fails with invalid repository URL"""
    request = {
        "repository_url": "not-a-valid-url",
        "commit_hash": "a" * 40,
        "script_path": "scripts/test.sh",
        "github_token": "ghp_test123",
    }
    
    result = validator.validate_execution_request(request)
    assert result.valid is False
    assert any("Invalid repository URL" in err for err in result.errors)


def test_validate_execution_request_invalid_commit(validator):
    """Test validation fails with invalid commit hash"""
    request = {
        "repository_url": "https://github.com/owner/repo",
        "commit_hash": "invalid",
        "script_path": "scripts/test.sh",
        "github_token": "ghp_test123",
    }
    
    result = validator.validate_execution_request(request)
    assert result.valid is False
    assert any("Invalid commit hash" in err for err in result.errors)


def test_validate_execution_request_invalid_path(validator):
    """Test validation fails with path traversal attempt"""
    request = {
        "repository_url": "https://github.com/owner/repo",
        "commit_hash": "a" * 40,
        "script_path": "../../../etc/passwd",
        "github_token": "ghp_test123",
    }
    
    result = validator.validate_execution_request(request)
    assert result.valid is False
    assert any("Invalid script path" in err for err in result.errors)


def test_validate_repository_url_valid(validator):
    """Test valid GitHub repository URLs"""
    valid_urls = [
        "https://github.com/owner/repo",
        "https://github.com/owner/repo-name",
        "https://github.com/owner/repo.name",
        "https://github.com/owner-name/repo",
        "https://github.com/owner_name/repo_name",
        "https://github.com/owner/repo/",
    ]
    
    for url in valid_urls:
        assert validator.validate_repository_url(url) is True


def test_validate_repository_url_invalid(validator):
    """Test invalid repository URLs"""
    invalid_urls = [
        "",
        "http://github.com/owner/repo",  # Not HTTPS
        "https://gitlab.com/owner/repo",  # Not GitHub
        "https://github.com/owner",  # Missing repo
        "github.com/owner/repo",  # Missing protocol
        "https://github.com/owner/repo/extra/path",  # Extra path
        "https://github.com/owner/repo?query=param",  # Query params
    ]
    
    for url in invalid_urls:
        assert validator.validate_repository_url(url) is False


def test_validate_commit_hash_valid(validator):
    """Test valid commit hashes"""
    valid_hashes = [
        "a" * 40,
        "0" * 40,
        "f" * 40,
        "0123456789abcdef" * 2 + "01234567",
    ]
    
    for hash in valid_hashes:
        assert validator.validate_commit_hash(hash) is True


def test_validate_commit_hash_invalid(validator):
    """Test invalid commit hashes"""
    invalid_hashes = [
        "",
        "a" * 39,  # Too short
        "a" * 41,  # Too long
        "g" * 40,  # Invalid character
        "A" * 40,  # Uppercase not allowed
        "abc123",  # Way too short
    ]
    
    for hash in invalid_hashes:
        assert validator.validate_commit_hash(hash) is False


def test_validate_script_path_valid(validator):
    """Test valid script paths"""
    valid_paths = [
        "script.sh",
        "scripts/test.sh",
        "path/to/script.py",
        "build-script.sh",
        "scripts/build_test.sh",
    ]
    
    for path in valid_paths:
        assert validator.validate_script_path(path) is True


def test_validate_script_path_invalid(validator):
    """Test invalid script paths"""
    invalid_paths = [
        "",
        "   ",  # Whitespace only
        "../script.sh",  # Path traversal
        "scripts/../../../etc/passwd",  # Path traversal
        "scripts/..\\file",  # Windows path traversal
        "scripts\\..\\file",  # Windows path traversal
    ]
    
    for path in invalid_paths:
        assert validator.validate_script_path(path) is False


def test_validate_execution_request_multiple_errors(validator):
    """Test validation returns all errors at once"""
    request = {
        "repository_url": "invalid-url",
        "commit_hash": "short",
        "script_path": "../../../etc/passwd",
        "github_token": "token",
    }
    
    result = validator.validate_execution_request(request)
    assert result.valid is False
    assert len(result.errors) == 3  # URL, commit, and path errors
