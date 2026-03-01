"""Property-based tests for request validation

Feature: github-actions-remote-executor
Tests Properties 1, 2, 4, 5, 6, 7 from the design document
"""
import pytest
from hypothesis import given, strategies as st, assume
from src.validation import RequestValidator, ValidationResult


# Custom strategies for generating test data
@st.composite
def valid_github_url(draw):
    """Generate valid GitHub repository URLs"""
    # Use ASCII alphanumeric plus allowed special chars
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
    """Generate valid script paths without path traversal"""
    # Generate path components that don't contain traversal sequences
    components = draw(st.lists(
        st.text(
            alphabet=st.characters(blacklist_characters='\\/:*?"<>|'),
            min_size=1,
            max_size=50
        ).filter(lambda x: '..' not in x and x.strip()),
        min_size=1,
        max_size=5
    ))
    return '/'.join(components)


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
def valid_execution_request(draw):
    """Generate valid execution requests"""
    return {
        'repository_url': draw(valid_github_url()),
        'commit_hash': draw(valid_commit_hash()),
        'script_path': draw(valid_script_path()),
        'github_token': draw(valid_github_token())
    }


# Property 1: Valid Request Acceptance
# Feature: github-actions-remote-executor, Property 1: Valid Request Acceptance
@given(request=valid_execution_request())
def test_property_1_valid_request_acceptance(request):
    """
    Property 1: For any execution request containing a valid repository URL,
    commit hash, script file path, and GitHub token, the server should accept
    and process the request.
    
    Validates: Requirements 1.3
    """
    validator = RequestValidator()
    result = validator.validate_execution_request(request)
    
    assert result.valid, f"Valid request was rejected: {result.errors}"
    assert len(result.errors) == 0, "Valid request should have no errors"


# Property 2: Malformed Request Rejection
# Feature: github-actions-remote-executor, Property 2: Malformed Request Rejection
@given(
    request=st.dictionaries(
        keys=st.text(min_size=1, max_size=50),
        values=st.one_of(st.none(), st.text(), st.integers(), st.booleans()),
        min_size=0,
        max_size=10
    )
)
def test_property_2_malformed_request_rejection(request):
    """
    Property 2: For any malformed request body, the Request Validator should
    return HTTP 400 with a descriptive error message.
    
    Validates: Requirements 1.4
    """
    # Exclude valid requests from this test
    required_fields = {'repository_url', 'commit_hash', 'script_path', 'github_token'}
    has_all_fields = all(field in request for field in required_fields)
    
    if has_all_fields:
        # Check if all fields are non-empty strings
        all_non_empty_strings = all(
            isinstance(request.get(field), str) and request.get(field)
            for field in required_fields
        )
        assume(not all_non_empty_strings)
    
    validator = RequestValidator()
    result = validator.validate_execution_request(request)
    
    # Malformed requests should be rejected
    assert not result.valid, "Malformed request should be rejected"
    assert len(result.errors) > 0, "Malformed request should have error messages"
    
    # Error messages should be descriptive (non-empty strings)
    for error in result.errors:
        assert isinstance(error, str), "Error should be a string"
        assert len(error) > 0, "Error message should be descriptive"


# Property 4: Required Field Validation
# Feature: github-actions-remote-executor, Property 4: Required Field Validation
@given(
    base_request=valid_execution_request(),
    missing_field=st.sampled_from(['repository_url', 'commit_hash', 'script_path', 'github_token'])
)
def test_property_4_required_field_validation(base_request, missing_field):
    """
    Property 4: For any execution request with one or more missing required fields
    (repository_url, commit_hash, script_path, github_token), the Request Validator
    should reject the request and return HTTP 400.
    
    Validates: Requirements 2.1, 2.6
    """
    # Create request with missing field
    request = base_request.copy()
    del request[missing_field]
    
    validator = RequestValidator()
    result = validator.validate_execution_request(request)
    
    assert not result.valid, f"Request missing {missing_field} should be rejected"
    assert len(result.errors) > 0, "Should have error messages"
    
    # Check that error mentions the missing field
    error_text = ' '.join(result.errors).lower()
    assert missing_field.lower() in error_text or 'missing' in error_text, \
        f"Error should mention missing field: {missing_field}"


@given(
    base_request=valid_execution_request(),
    empty_field=st.sampled_from(['repository_url', 'commit_hash', 'script_path', 'github_token'])
)
def test_property_4_empty_field_validation(base_request, empty_field):
    """
    Property 4 (variant): For any execution request with empty required fields,
    the Request Validator should reject the request.
    
    Validates: Requirements 2.1, 2.6
    """
    # Create request with empty field
    request = base_request.copy()
    request[empty_field] = ''
    
    validator = RequestValidator()
    result = validator.validate_execution_request(request)
    
    assert not result.valid, f"Request with empty {empty_field} should be rejected"
    assert len(result.errors) > 0, "Should have error messages"


# Property 5: Repository URL Format Validation
# Feature: github-actions-remote-executor, Property 5: Repository URL Format Validation
@given(
    invalid_url=st.one_of(
        st.text(min_size=0, max_size=100).filter(
            lambda x: not x.startswith('https://github.com/')
        ),
        st.just(''),
        st.just('http://github.com/owner/repo'),  # Wrong protocol
        st.just('https://gitlab.com/owner/repo'),  # Wrong domain
        st.just('https://github.com/'),  # Missing owner/repo
        st.just('https://github.com/owner'),  # Missing repo
        st.just('https://github.com//repo'),  # Missing owner
        st.just('github.com/owner/repo'),  # Missing protocol
    )
)
def test_property_5_repository_url_format_validation(invalid_url):
    """
    Property 5: For any invalid repository URL format, the Request Validator
    should reject the request and return HTTP 400.
    
    Validates: Requirements 2.2
    """
    validator = RequestValidator()
    
    # Test the URL validation method directly
    is_valid = validator.validate_repository_url(invalid_url)
    assert not is_valid, f"Invalid URL should be rejected: {invalid_url}"
    
    # Test in full request context
    request = {
        'repository_url': invalid_url,
        'commit_hash': 'a' * 40,
        'script_path': 'script.sh',
        'github_token': 'ghp_token123'
    }
    
    result = validator.validate_execution_request(request)
    assert not result.valid, "Request with invalid URL should be rejected"


# Property 6: Commit Hash Format Validation
# Feature: github-actions-remote-executor, Property 6: Commit Hash Format Validation
@given(
    invalid_hash=st.one_of(
        st.text(alphabet='0123456789abcdef', min_size=0, max_size=39),  # Too short
        st.text(alphabet='0123456789abcdef', min_size=41, max_size=50),  # Too long
        st.text(alphabet='0123456789ABCDEF', min_size=40, max_size=40),  # Uppercase
        st.just(''),  # Empty
        st.text(min_size=40, max_size=40).filter(
            lambda x: not all(c in '0123456789abcdef' for c in x)
        ),
        # Add some specific invalid examples
        st.just('g' * 40),  # Invalid character 'g'
        st.just('Z' * 40),  # Invalid uppercase
        st.just('abc123'),  # Too short
    )
)
def test_property_6_commit_hash_format_validation(invalid_hash):
    """
    Property 6: For any invalid Git commit SHA format, the Request Validator
    should reject the request and return HTTP 400.
    
    Validates: Requirements 2.3
    """
    # Ensure we're not testing valid hashes (40 lowercase hex chars)
    is_valid_format = (
        len(invalid_hash) == 40 and 
        all(c in '0123456789abcdef' for c in invalid_hash)
    )
    assume(not is_valid_format)
    
    validator = RequestValidator()
    
    # Test the hash validation method directly
    is_valid = validator.validate_commit_hash(invalid_hash)
    assert not is_valid, f"Invalid commit hash should be rejected: {invalid_hash}"
    
    # Test in full request context
    request = {
        'repository_url': 'https://github.com/owner/repo',
        'commit_hash': invalid_hash,
        'script_path': 'script.sh',
        'github_token': 'ghp_token123'
    }
    
    result = validator.validate_execution_request(request)
    assert not result.valid, "Request with invalid commit hash should be rejected"


# Property 7: Validation Error Response
# Feature: github-actions-remote-executor, Property 7: Validation Error Response
@given(
    request=st.dictionaries(
        keys=st.sampled_from(['repository_url', 'commit_hash', 'script_path', 'github_token']),
        values=st.one_of(
            st.text(min_size=0, max_size=100),
            st.just(''),
            st.none()
        ),
        min_size=0,
        max_size=4
    )
)
def test_property_7_validation_error_response(request):
    """
    Property 7: For any validation failure, the Request Validator should return
    HTTP 400 with specific validation error details.
    
    Validates: Requirements 2.5
    """
    # Ensure this is an invalid request
    required_fields = {'repository_url', 'commit_hash', 'script_path', 'github_token'}
    has_all_fields = all(field in request for field in required_fields)
    
    if has_all_fields:
        all_valid = all(
            isinstance(request.get(field), str) and request.get(field)
            for field in required_fields
        )
        assume(not all_valid)
    
    validator = RequestValidator()
    result = validator.validate_execution_request(request)
    
    # Should be invalid
    assert not result.valid, "Invalid request should be rejected"
    
    # Should have specific error details
    assert len(result.errors) > 0, "Should have at least one error message"
    
    # Each error should be specific and descriptive
    for error in result.errors:
        assert isinstance(error, str), "Error should be a string"
        assert len(error) > 10, "Error should be descriptive (more than 10 chars)"
        
        # Error should mention what's wrong (field name or validation issue)
        error_lower = error.lower()
        has_field_mention = any(
            field in error_lower for field in 
            ['repository', 'commit', 'script', 'token', 'url', 'hash', 'path']
        )
        has_issue_mention = any(
            issue in error_lower for issue in
            ['missing', 'invalid', 'empty', 'required', 'format']
        )
        
        assert has_field_mention or has_issue_mention, \
            f"Error should be specific about what failed: {error}"


# Additional test for path traversal detection
@given(
    traversal_pattern=st.sampled_from(['../', '..\\', '/../', '\\..\\'])
)
def test_path_traversal_rejection(traversal_pattern):
    """
    Test that paths containing traversal sequences are rejected.
    This is part of Property 4 (script_path validation).
    """
    validator = RequestValidator()
    
    # Create paths with traversal patterns
    malicious_paths = [
        f"{traversal_pattern}etc/passwd",
        f"scripts/{traversal_pattern}config",
        f"{traversal_pattern}{traversal_pattern}root"
    ]
    
    for path in malicious_paths:
        is_valid = validator.validate_script_path(path)
        assert not is_valid, f"Path with traversal should be rejected: {path}"
