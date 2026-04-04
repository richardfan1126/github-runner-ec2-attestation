"""Data models for GitHub Actions Remote Executor"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, List


@dataclass
class CloneResult:
    """Result of cloning a repository"""
    clone_path: str
    script_path: str


class ExecutionStatus(Enum):
    """Status of script execution"""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass
class ExecutionRequest:
    """Request to execute a script from a GitHub repository"""
    repository_url: str
    commit_hash: str
    script_path: str
    github_token: str


@dataclass
class ExecutionRecord:
    """Record of a script execution"""
    execution_id: str
    repository_url: str
    commit_hash: str
    script_path: str
    status: ExecutionStatus
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    exit_code: Optional[int]
    timeout_seconds: int


@dataclass
class AttestationDocument:
    """Attestation document proving execution environment"""
    repository_url: str
    commit_hash: str
    script_path: str
    timestamp: datetime
    signature: bytes  # CBOR-encoded NitroTPM attestation


@dataclass
class OutputData:
    """Output data from script execution"""
    stdout: str
    stderr: str
    stdout_offset: int
    stderr_offset: int
    complete: bool
    exit_code: Optional[int]


@dataclass
class SandboxConfig:
    """Configuration for sandbox execution environment"""
    working_directory: str
    max_memory_mb: int
    max_cpu_percent: int
    network_enabled: bool
    timeout_seconds: int
    allowed_paths: List[str]


@dataclass
class OIDCValidationResult:
    """Result of OIDC token validation"""
    valid: bool
    status_code: int  # 200 on success, 401 or 403 on failure
    error_message: Optional[str]
    claims: Optional[dict]


@dataclass
class OIDCTokenClaims:
    """Decoded claims from a GitHub Actions OIDC token"""
    iss: str       # Must be https://token.actions.githubusercontent.com
    aud: str       # Must match Expected_Audience
    repository: str  # Must match an entry in Allowed_Repositories
    exp: int       # Unix timestamp, must not be expired
    sub: str       # Subject (e.g., repo:owner/repo:ref:refs/heads/main)
