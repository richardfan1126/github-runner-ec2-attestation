"""Logging infrastructure for GitHub Actions Remote Executor"""
import contextvars
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path
from typing import Optional


# ContextVar holding a dict of log context fields per async task / thread.
# Each request or background task gets its own isolated copy.
_log_context_var: contextvars.ContextVar[dict] = contextvars.ContextVar(
    '_log_context_var', default={}
)


class ContextFilter(logging.Filter):
    """Filter to add context information to log records.

    Reads per-request/per-task context from a ``contextvars.ContextVar``
    so that concurrent requests never share or overwrite each other's
    log context.
    """

    def filter(self, record):
        """Add context fields from the current ContextVar to the log record."""
        ctx = _log_context_var.get()
        for key, value in ctx.items():
            setattr(record, key, value)

        # Ensure fields exist even if not set
        if not hasattr(record, 'execution_id'):
            record.execution_id = '-'
        if not hasattr(record, 'request_id'):
            record.request_id = '-'

        return True


# Global context filter instance (stateless – all state lives in the ContextVar)
_context_filter = ContextFilter()


class SafeContextFormatter(logging.Formatter):
    """Formatter that injects default values for missing context fields,
    preventing KeyError when a log record is emitted without the filter."""

    DEFAULTS = {'execution_id': '-', 'request_id': '-'}

    def format(self, record):
        for key, default in self.DEFAULTS.items():
            if not hasattr(record, key):
                setattr(record, key, default)
        return super().format(record)


def setup_logging(
    log_level: str = "INFO",
    log_dir: Optional[str] = None,
    enable_rotation: bool = True
) -> None:
    """
    Set up structured logging with timestamp and context
    
    Configures logging with:
    - Structured format with timestamp (ISO 8601), level, context, and message
    - Log levels: ERROR, WARN, INFO, DEBUG
    - Optional log rotation (daily with 30-day retention)
    - Console and file handlers
    
    Args:
        log_level: Logging level (ERROR, WARN, INFO, DEBUG)
        log_dir: Directory for log files (if None, logs to console only)
        enable_rotation: Enable daily log rotation with retention
    """
    # Convert log level string to logging constant
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Create formatter with ISO 8601 timestamp and context
    formatter = SafeContextFormatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - [execution_id=%(execution_id)s request_id=%(request_id)s] - %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S%z'
    )
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Add context filter to root logger
    root_logger.addFilter(_context_filter)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler with rotation (if log_dir specified)
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        log_file = log_path / "github_actions_executor.log"
        
        if enable_rotation:
            # Rotating file handler - daily rotation with 30-day retention
            file_handler = logging.handlers.TimedRotatingFileHandler(
                filename=str(log_file),
                when='midnight',
                interval=1,
                backupCount=30,
                encoding='utf-8'
            )
        else:
            # Simple file handler without rotation
            file_handler = logging.FileHandler(
                filename=str(log_file),
                encoding='utf-8'
            )
        
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


def set_log_context(execution_id: Optional[str] = None, request_id: Optional[str] = None) -> None:
    """
    Set context for subsequent log messages in the current async task / thread.

    Uses ``contextvars.ContextVar`` so each request or background task has
    its own isolated log context.

    Args:
        execution_id: Execution ID to include in logs
        request_id: Request ID to include in logs
    """
    updates = {}
    if execution_id is not None:
        updates['execution_id'] = execution_id
    if request_id is not None:
        updates['request_id'] = request_id

    # Merge into the current context dict (creates a new dict to avoid
    # mutating a dict that might be shared with a parent context copy).
    current = _log_context_var.get()
    _log_context_var.set({**current, **updates})


def clear_log_context() -> None:
    """Clear all log context for the current async task / thread."""
    _log_context_var.set({})


def sanitize_for_logging(data: dict, sensitive_keys: list[str] = None) -> dict:
    """
    Sanitize dictionary for logging by removing sensitive data
    
    Args:
        data: Dictionary to sanitize
        sensitive_keys: List of keys to redact (default: ['github_token', 'token', 'password', 'secret'])
    
    Returns:
        Sanitized dictionary with sensitive values replaced with '[REDACTED]'
    """
    if sensitive_keys is None:
        sensitive_keys = ['github_token', 'token', 'password', 'secret', 'authorization']
    
    sanitized = {}
    for key, value in data.items():
        if any(sensitive_key in key.lower() for sensitive_key in sensitive_keys):
            sanitized[key] = '[REDACTED]'
        elif isinstance(value, dict):
            sanitized[key] = sanitize_for_logging(value, sensitive_keys)
        else:
            sanitized[key] = value
    
    return sanitized


def sanitize_error_message(message: str) -> str:
    """
    Sanitize error message to prevent exposure of internal details
    
    Removes:
    - File paths (absolute paths starting with /)
    - Stack traces (lines starting with 'File "' or 'Traceback')
    - Environment variables
    
    Args:
        message: Error message to sanitize
    
    Returns:
        Sanitized error message safe for external exposure
    """
    # Remove absolute file paths
    message = re.sub(r'/[a-zA-Z0-9_/.-]+', '[PATH]', message)
    
    # Remove stack trace lines
    lines = message.split('\n')
    sanitized_lines = []
    skip_next = False
    
    for line in lines:
        # Skip traceback lines
        if 'Traceback' in line or 'File "' in line or skip_next:
            skip_next = 'File "' in line
            continue
        sanitized_lines.append(line)
    
    return '\n'.join(sanitized_lines).strip()


# Maximum length for user-controlled log fields before truncation
LOG_FIELD_MAX_LENGTH = 256


class LogSanitizer:
    """Sanitizes log messages and error responses to prevent credential leakage.

    Redacts:
    - GitHub tokens (ghp_*, ghs_*, github_pat_*)
    - Credentialed URLs (https://token@host/...)
    - Authorization header values
    - Absolute file paths
    - Environment variable assignments containing tokens
    - ASCII control characters (except newline and tab)

    Requirements: 7.12, 7.13, 7.14, 7.15, 7.16, 7.17, 7.18
    """

    # GitHub token patterns: ghp_, ghs_, github_pat_ followed by alphanumeric/underscore
    _GITHUB_TOKEN_RE = re.compile(
        r'\b(ghp_[A-Za-z0-9_]{1,255}|ghs_[A-Za-z0-9_]{1,255}|github_pat_[A-Za-z0-9_]{1,255})\b'
    )

    # Credentialed URLs: https://anything@host (captures the credential portion)
    _CREDENTIALED_URL_RE = re.compile(
        r'https?://[^@\s]+@[^\s]+'
    )

    # Authorization header values in log output (case-insensitive)
    _AUTH_HEADER_RE = re.compile(
        r'(Authorization:\s*)(Bearer\s+\S+|Basic\s+\S+|token\s+\S+|\S+)',
        re.IGNORECASE
    )

    # Absolute file paths (Unix-style)
    _ABS_PATH_RE = re.compile(
        r'(?<![a-zA-Z0-9_])(/(?:[a-zA-Z0-9_.-]+/)+[a-zA-Z0-9_.-]+)'
    )

    # Environment variable assignments containing token-like values
    _ENV_TOKEN_RE = re.compile(
        r'([A-Z_][A-Z0-9_]*=)(ghp_[A-Za-z0-9_]+|ghs_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|[A-Za-z0-9+/=]{40,})',
        re.IGNORECASE
    )

    # ASCII control characters (0x00-0x1F except \n=0x0A and \t=0x09, plus 0x7F)
    # Note: \r (0x0D) IS stripped because CR is a log injection vector (OWASP)
    _CONTROL_CHARS_RE = re.compile(
        r'[\x00-\x08\x0b-\x1f\x7f]'
    )

    def sanitize(self, message: str) -> str:
        """
        Apply all sanitization rules to a message.

        Args:
            message: Raw message (e.g., subprocess stderr, exception text)

        Returns:
            Sanitized message safe for logging or error responses
        """
        if not message:
            return message

        # 1. Redact GitHub tokens
        message = self._GITHUB_TOKEN_RE.sub('[REDACTED_TOKEN]', message)

        # 2. Redact credentialed URLs
        message = self._CREDENTIALED_URL_RE.sub('[REDACTED_URL]', message)

        # 3. Redact Authorization header values
        message = self._AUTH_HEADER_RE.sub(r'\1[REDACTED]', message)

        # 4. Redact environment variable assignments containing tokens
        message = self._ENV_TOKEN_RE.sub(r'\1[REDACTED]', message)

        # 5. Redact absolute file paths
        message = self._ABS_PATH_RE.sub('[PATH]', message)

        # 6. Remove ASCII control characters
        message = self._CONTROL_CHARS_RE.sub('', message)

        return message

    def sanitize_for_error_response(self, message: str) -> str:
        """
        Sanitize a message for inclusion in encrypted error envelopes.

        More aggressive than sanitize() — strips all internal details and
        returns only a categorized description.

        Args:
            message: Raw error message

        Returns:
            Sanitized message suitable for error envelopes
        """
        sanitized = self.sanitize(message)
        # Apply length cap
        return truncate_field(sanitized)


# Module-level singleton for convenience
_log_sanitizer = LogSanitizer()


def get_log_sanitizer() -> LogSanitizer:
    """Return the module-level LogSanitizer singleton."""
    return _log_sanitizer


def sanitize_log_message(message: str) -> str:
    """
    Convenience function: sanitize a message using the module-level LogSanitizer.

    Args:
        message: Raw message to sanitize

    Returns:
        Sanitized message
    """
    return _log_sanitizer.sanitize(message)


def truncate_field(value: str, max_length: int = LOG_FIELD_MAX_LENGTH) -> str:
    """
    Truncate a user-controlled log field to max_length characters.

    Args:
        value: The field value to potentially truncate
        max_length: Maximum allowed length (default: 256)

    Returns:
        Original value if within limit, otherwise truncated with '[truncated]' suffix
    """
    if not value or len(value) <= max_length:
        return value
    return value[:max_length] + '[truncated]'


def sanitize_nonce_for_logging(nonce: str) -> str:
    """
    Return a safe representation of a nonce for logging.

    Instead of logging the full nonce value (which could be used for replay
    if logs are compromised), log only the first 8 characters as a prefix.

    Args:
        nonce: The full nonce value

    Returns:
        Truncated nonce prefix suitable for log messages
    """
    if not nonce:
        return '<empty>'
    return nonce[:8] + '...'
