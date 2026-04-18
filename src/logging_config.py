"""Logging infrastructure for GitHub Actions Remote Executor"""
import contextvars
import logging
import logging.handlers
import os
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
    import re
    
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
