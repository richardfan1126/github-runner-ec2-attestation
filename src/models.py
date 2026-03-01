"""Data models for GitHub Actions Remote Executor"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, List


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
    signature: bytes  # CBOR-encoded NSM attestation


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
