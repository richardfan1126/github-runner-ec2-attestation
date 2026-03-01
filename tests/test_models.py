"""Unit tests for data models"""
from datetime import datetime
from src.models import (
    ExecutionStatus,
    ExecutionRequest,
    ExecutionRecord,
    AttestationDocument,
    OutputData,
    SandboxConfig,
)


def test_execution_status_enum():
    """Test ExecutionStatus enum values"""
    assert ExecutionStatus.QUEUED.value == "queued"
    assert ExecutionStatus.RUNNING.value == "running"
    assert ExecutionStatus.COMPLETED.value == "completed"
    assert ExecutionStatus.FAILED.value == "failed"
    assert ExecutionStatus.TIMED_OUT.value == "timed_out"


def test_execution_request_creation():
    """Test ExecutionRequest dataclass creation"""
    request = ExecutionRequest(
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="scripts/test.sh",
        github_token="ghp_test123",
    )
    
    assert request.repository_url == "https://github.com/owner/repo"
    assert request.commit_hash == "a" * 40
    assert request.script_path == "scripts/test.sh"
    assert request.github_token == "ghp_test123"


def test_execution_record_creation():
    """Test ExecutionRecord dataclass creation"""
    now = datetime.now()
    record = ExecutionRecord(
        execution_id="test-id-123",
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="scripts/test.sh",
        status=ExecutionStatus.QUEUED,
        created_at=now,
        started_at=None,
        completed_at=None,
        exit_code=None,
        timeout_seconds=300,
    )
    
    assert record.execution_id == "test-id-123"
    assert record.status == ExecutionStatus.QUEUED
    assert record.created_at == now
    assert record.started_at is None
    assert record.exit_code is None


def test_attestation_document_creation():
    """Test AttestationDocument dataclass creation"""
    now = datetime.now()
    signature = b"test_signature_bytes"
    
    doc = AttestationDocument(
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="scripts/test.sh",
        timestamp=now,
        signature=signature,
    )
    
    assert doc.repository_url == "https://github.com/owner/repo"
    assert doc.timestamp == now
    assert doc.signature == signature


def test_output_data_creation():
    """Test OutputData dataclass creation"""
    output = OutputData(
        stdout="test output",
        stderr="test error",
        stdout_offset=100,
        stderr_offset=50,
        complete=False,
        exit_code=None,
    )
    
    assert output.stdout == "test output"
    assert output.stderr == "test error"
    assert output.stdout_offset == 100
    assert output.stderr_offset == 50
    assert output.complete is False
    assert output.exit_code is None


def test_output_data_completed():
    """Test OutputData with completed execution"""
    output = OutputData(
        stdout="final output",
        stderr="",
        stdout_offset=200,
        stderr_offset=0,
        complete=True,
        exit_code=0,
    )
    
    assert output.complete is True
    assert output.exit_code == 0


def test_sandbox_config_creation():
    """Test SandboxConfig dataclass creation"""
    config = SandboxConfig(
        working_directory="/tmp/exec-123",
        max_memory_mb=512,
        max_cpu_percent=50,
        network_enabled=False,
        timeout_seconds=300,
        allowed_paths=["/tmp/exec-123", "/usr/bin"],
    )
    
    assert config.working_directory == "/tmp/exec-123"
    assert config.max_memory_mb == 512
    assert config.max_cpu_percent == 50
    assert config.network_enabled is False
    assert config.timeout_seconds == 300
    assert len(config.allowed_paths) == 2
