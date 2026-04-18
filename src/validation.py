"""Request validation for GitHub Actions Remote Executor"""
import fnmatch
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional, List

import jwt
import requests as http_requests

from src.models import ExecutionRequest, OIDCValidationResult

logger = logging.getLogger(__name__)

GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_JWKS_URL = f"{GITHUB_OIDC_ISSUER}/.well-known/jwks"


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

    def __init__(
        self,
        allowed_repositories: Optional[List[str]] = None,
        expected_audience: Optional[str] = None,
        allowed_branches: Optional[List[str]] = None,
        require_protected_ref: bool = False,
    ):
        self.allowed_repositories = allowed_repositories or []
        self.expected_audience = expected_audience or ""
        self.allowed_branches = allowed_branches
        self.require_protected_ref = require_protected_ref
        self._jwks_cache: Optional[dict] = None

    # ------------------------------------------------------------------
    # OIDC token validation
    # ------------------------------------------------------------------

    def _fetch_jwks(self, force_refresh: bool = False) -> dict:
        """Fetch JWKS from GitHub's OIDC provider, with caching.

        Args:
            force_refresh: If True, bypass the cache and fetch fresh JWKS.

        Returns:
            The JWKS dict (contains a ``keys`` list).
        """
        if self._jwks_cache is not None and not force_refresh:
            return self._jwks_cache

        try:
            resp = http_requests.get(GITHUB_OIDC_JWKS_URL, timeout=10)
            resp.raise_for_status()
            self._jwks_cache = resp.json()
            return self._jwks_cache
        except Exception as exc:
            logger.error(f"Failed to fetch JWKS from {GITHUB_OIDC_JWKS_URL}: {exc}")
            raise

    def validate_oidc_token(self, authorization_header: Optional[str]) -> OIDCValidationResult:
        """Validate a Bearer OIDC token from the Authorization header.

        Returns an ``OIDCValidationResult`` with ``status_code`` 200 on
        success, 401 for authentication failures, or 403 when the token
        is valid but the repository is not in the allow-list.
        """
        # --- extract bearer token ---
        if not authorization_header:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Authorization header is required",
                claims=None,
            )

        parts = authorization_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Authorization header must use Bearer scheme",
                claims=None,
            )

        token = parts[1]

        # --- decode JWT header to get kid ---
        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.exceptions.DecodeError as exc:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message=f"Invalid token format: {exc}",
                claims=None,
            )

        kid = unverified_header.get("kid")
        if not kid:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Token header missing key ID (kid)",
                claims=None,
            )

        # --- find matching key in JWKS (retry once on cache miss) ---
        signing_key = self._find_signing_key(kid)
        if signing_key is None:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message=f"No matching key found for kid: {kid}",
                claims=None,
            )

        # --- verify signature and decode claims ---
        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                issuer=GITHUB_OIDC_ISSUER,
                audience=self.expected_audience,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Token has expired",
                claims=None,
            )
        except jwt.InvalidIssuerError:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Invalid token issuer",
                claims=None,
            )
        except jwt.InvalidAudienceError:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Invalid token audience",
                claims=None,
            )
        except jwt.InvalidSignatureError:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Token signature verification failed",
                claims=None,
            )
        except jwt.PyJWTError as exc:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message=f"Token validation failed: {exc}",
                claims=None,
            )

        # --- validate repository claim ---
        repository = claims.get("repository")
        if not repository or repository not in self.allowed_repositories:
            return OIDCValidationResult(
                valid=False, status_code=403,
                error_message=f"Repository not authorized: {repository}",
                claims=None,
            )

        # --- validate branch restriction ---
        branch_result = self._validate_branch_and_ref(claims)
        if branch_result is not None:
            return branch_result

        return OIDCValidationResult(
            valid=True, status_code=200,
            error_message=None,
            claims=claims,
        )

    def validate_oidc_token_from_body(self, oidc_token: Optional[str]) -> OIDCValidationResult:
        """Validate a raw OIDC token string extracted from the decrypted request body.

        Unlike ``validate_oidc_token`` which expects an ``Authorization: Bearer <token>``
        header, this method accepts the bare JWT string directly (the ``oidc_token``
        field from the decrypted request body).

        Returns an ``OIDCValidationResult`` with ``status_code`` 200 on
        success, 401 for authentication failures, or 403 when the token
        is valid but the repository is not in the allow-list.
        """
        if not oidc_token:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="oidc_token is required",
                claims=None,
            )

        # --- decode JWT header to get kid ---
        try:
            unverified_header = jwt.get_unverified_header(oidc_token)
        except jwt.exceptions.DecodeError as exc:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message=f"Invalid token format: {exc}",
                claims=None,
            )

        kid = unverified_header.get("kid")
        if not kid:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Token header missing key ID (kid)",
                claims=None,
            )

        # --- find matching key in JWKS (retry once on cache miss) ---
        signing_key = self._find_signing_key(kid)
        if signing_key is None:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message=f"No matching key found for kid: {kid}",
                claims=None,
            )

        # --- verify signature and decode claims ---
        try:
            claims = jwt.decode(
                oidc_token,
                signing_key,
                algorithms=["RS256"],
                issuer=GITHUB_OIDC_ISSUER,
                audience=self.expected_audience,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Token has expired",
                claims=None,
            )
        except jwt.InvalidIssuerError:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Invalid token issuer",
                claims=None,
            )
        except jwt.InvalidAudienceError:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Invalid token audience",
                claims=None,
            )
        except jwt.InvalidSignatureError:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message="Token signature verification failed",
                claims=None,
            )
        except jwt.PyJWTError as exc:
            return OIDCValidationResult(
                valid=False, status_code=401,
                error_message=f"Token validation failed: {exc}",
                claims=None,
            )

        # --- validate repository claim ---
        repository = claims.get("repository")
        if not repository or repository not in self.allowed_repositories:
            return OIDCValidationResult(
                valid=False, status_code=403,
                error_message=f"Repository not authorized: {repository}",
                claims=None,
            )

        # --- validate branch restriction ---
        branch_result = self._validate_branch_and_ref(claims)
        if branch_result is not None:
            return branch_result

        return OIDCValidationResult(
            valid=True, status_code=200,
            error_message=None,
            claims=claims,
        )

    def _validate_branch_and_ref(self, claims: dict) -> Optional[OIDCValidationResult]:
        """Validate branch and protected ref restrictions.

        Returns an ``OIDCValidationResult`` rejection if a restriction is
        violated, or ``None`` if all checks pass (or are skipped).
        """
        # Branch restriction
        if self.allowed_branches:
            ref = claims.get("ref", "")
            if not any(fnmatch.fnmatch(ref, pattern) for pattern in self.allowed_branches):
                return OIDCValidationResult(
                    valid=False, status_code=403,
                    error_message=f"Branch not allowed: {ref}",
                    claims=None,
                )

        # Protected ref restriction
        if self.require_protected_ref:
            ref_protected = claims.get("ref_protected", "")
            if ref_protected != "true":
                return OIDCValidationResult(
                    valid=False, status_code=403,
                    error_message=f"Protected ref required but ref_protected is: {ref_protected}",
                    claims=None,
                )

        return None

    def _find_signing_key(self, kid: str):
        """Look up a signing key by kid, refreshing JWKS once on miss."""
        for attempt in range(2):
            try:
                jwks = self._fetch_jwks(force_refresh=(attempt == 1))
            except Exception:
                return None

            jwk_set = jwt.PyJWKSet.from_dict(jwks)
            for key in jwk_set.keys:
                if key.key_id == kid:
                    return key.key
        return None
    
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
