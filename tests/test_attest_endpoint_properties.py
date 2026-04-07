"""Property-based tests for /attest endpoint behavior.

Feature: github-actions-remote-executor
Tests Properties 123, 124, 125 from the design document.
"""
import base64
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st, settings

from src.server import create_app
from src.config import ServerConfig
from src.encryption import EncryptionManager
from src.models import (
    AttestationDocument,
    CloneResult,
    ExecutionRecord,
    ExecutionStatus,
    OIDCValidationResult,
)
from src.attestation import AttestationError


VALID_OIDC_RESULT = OIDCValidationResult(
    valid=True,
    status_code=200,
    error_message=None,
    claims={
        "repository": "owner/repo",
        "iss": "https://token.actions.githubusercontent.com",
        "aud": "https://example.com",
    },
)

OIDC_BEARER_HEADER = {"Authorization": "Bearer valid.oidc.token"}


def get_test_config():
    """Create test configuration."""
    return ServerConfig(
        port=8000,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=100000,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/test",
        output_retention_hours=24,
        tpm_attest_path="/usr/bin/nitro-tpm-attest",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )


def _make_attestation_doc(signature: bytes = b"test_signature") -> AttestationDocument:
    """Helper to create a mock AttestationDocument."""
    return AttestationDocument(
        repository_url="",
        commit_hash="",
        script_path="",
        timestamp=datetime.now(timezone.utc),
        signature=signature,
    )


# ---------------------------------------------------------------------------
# Property 123: Attest Endpoint No Authentication
# ---------------------------------------------------------------------------

# Feature: github-actions-remote-executor, Property 123: Attest Endpoint No Authentication
@settings(max_examples=20, deadline=None)
@given(nonce=st.one_of(st.none(), st.text(min_size=1, max_size=64)))
def test_attest_endpoint_no_authentication(nonce):
    """
    **Validates: Requirements 37.2, 2.21**

    For any request to the /attest endpoint without any authentication
    credentials, the server should return a successful response containing
    an attestation document.
    """
    app = create_app(get_test_config())
    client = TestClient(app)

    with patch.object(
        app.state.attestation_generator, "generate_attestation"
    ) as mock_attest:
        mock_attest.return_value = (_make_attestation_doc(), None)

        # Build request – no Authorization header at all
        params = {}
        if nonce is not None:
            params["nonce"] = nonce

        response = client.get("/attest", params=params)

        assert response.status_code == 200, (
            f"Expected 200 but got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert "attestation_document" in body
        # The value must be valid base64
        base64.b64decode(body["attestation_document"])


# ---------------------------------------------------------------------------
# Property 124: Attest Attestation Contains Server Public Key
# ---------------------------------------------------------------------------

# Feature: github-actions-remote-executor, Property 124: Attest Attestation Contains Server Public Key
@settings(max_examples=20, deadline=None)
@given(nonce=st.one_of(st.none(), st.text(min_size=1, max_size=64)))
def test_attest_attestation_contains_server_public_key(nonce):
    """
    **Validates: Requirements 37.4, 39.1**

    For any request to the /attest endpoint, the generated
    Attestation_Document should include the Server_Public_Key in the
    `public_key` field passed to generate_attestation.
    """
    encryption_manager = EncryptionManager()
    app = create_app(get_test_config(), encryption_manager=encryption_manager)
    client = TestClient(app)

    with patch.object(
        app.state.attestation_generator, "generate_attestation"
    ) as mock_attest:
        mock_attest.return_value = (_make_attestation_doc(), None)

        params = {}
        if nonce is not None:
            params["nonce"] = nonce

        response = client.get("/attest", params=params)

        assert response.status_code == 200

        # Verify generate_attestation was called with public_key equal to
        # the EncryptionManager's server_public_key bytes.
        mock_attest.assert_called_once()
        call_kwargs = mock_attest.call_args
        assert call_kwargs.kwargs.get("public_key") == encryption_manager.server_public_key, (
            "generate_attestation must be called with public_key=server_public_key"
        )


# ---------------------------------------------------------------------------
# Property 125: Non-Attest Attestation Excludes Server Public Key
# ---------------------------------------------------------------------------

# Feature: github-actions-remote-executor, Property 125: Non-Attest Attestation Excludes Server Public Key
@settings(max_examples=10, deadline=None)
@given(
    repo_url=st.just("https://github.com/owner/repo"),
    commit_hash=st.text(alphabet="0123456789abcdef", min_size=40, max_size=40),
    script_path=st.text(min_size=1, max_size=50).filter(
        lambda x: ".." not in x and x.strip()
    ),
)
def test_non_attest_attestation_excludes_server_public_key(
    repo_url, commit_hash, script_path
):
    """
    **Validates: Requirements 37.9, 39.2**

    For any attestation document generated for the /execute endpoint,
    the document should NOT include the Server_Public_Key in the
    `public_key` field.
    """
    from tests.encryption_test_helpers import EncryptionTestContext, make_encrypted_execute_request

    ctx = EncryptionTestContext()
    app = create_app(get_test_config(), encryption_manager=ctx.encryption_manager)
    client = TestClient(app)

    req_data = {
        "repository_url": repo_url,
        "commit_hash": commit_hash,
        "script_path": script_path,
        "github_token": "ghp_testtoken1234567890",
        "oidc_token": "valid.oidc.token",
    }

    with patch.object(
        app.state.request_validator, "validate_oidc_token_from_body", return_value=VALID_OIDC_RESULT
    ), patch.object(
        app.state.request_validator, "validate_execution_request"
    ) as mock_validate, patch.object(
        app.state.repository_client, "authenticate"
    ) as mock_auth, patch.object(
        app.state.repository_client, "clone_repo"
    ) as mock_clone, patch.object(
        app.state.repository_client, "validate_script_exists", return_value=True
    ), patch(
        "os.path.getsize", return_value=100
    ), patch.object(
        app.state.attestation_generator, "generate_attestation"
    ) as mock_attest, patch.object(
        app.state.script_executor, "execute_async"
    ):
        mock_validate.return_value = Mock(valid=True, errors=[])
        mock_auth.return_value = Mock(success=True, error_message=None)
        mock_clone.return_value = CloneResult(
            clone_path="/tmp/clone_test", script_path=script_path
        )
        mock_attest.return_value = (
            AttestationDocument(
                repository_url=repo_url,
                commit_hash=commit_hash,
                script_path=script_path,
                timestamp=datetime.now(timezone.utc),
                signature=b"test_signature",
            ),
            None,
        )

        body = make_encrypted_execute_request(req_data, ctx)
        response = client.post("/execute", json=body)

        assert response.status_code == 200, (
            f"Expected 200 but got {response.status_code}: {response.text}"
        )

        # Verify generate_attestation was called WITHOUT public_key
        mock_attest.assert_called_once()
        call_kwargs = mock_attest.call_args
        # public_key should either not be present or be None
        pk_value = call_kwargs.kwargs.get("public_key")
        if pk_value is None:
            # Also check positional args – public_key is not a positional arg
            # in the /execute call path, so it should not appear.
            pass
        assert pk_value is None, (
            f"generate_attestation for /execute must NOT include public_key, "
            f"but got public_key={pk_value!r}"
        )


# ---------------------------------------------------------------------------
# Property 136: Attest Attestation Excludes User Data
# ---------------------------------------------------------------------------

# Feature: github-actions-remote-executor, Property 136: Attest Attestation Excludes User Data
@settings(max_examples=100, deadline=None)
@given(nonce=st.one_of(st.none(), st.text(min_size=1, max_size=64)))
def test_attest_attestation_excludes_user_data(nonce):
    """
    **Validates: Requirements 37.10**

    For any request to /attest, the generate_attestation call does NOT
    include user_data (no --user-data flag passed to nitro-tpm-attest).
    The attestation document should only contain public_key and optionally nonce.
    """
    app = create_app(get_test_config())
    client = TestClient(app)

    with patch("subprocess.run") as mock_subprocess:
        mock_subprocess.return_value = Mock(
            returncode=0,
            stdout=b"fake_attestation_cbor_data",
            stderr=b"",
        )

        params = {}
        if nonce is not None:
            params["nonce"] = nonce

        response = client.get("/attest", params=params)

        assert response.status_code == 200, (
            f"Expected 200 but got {response.status_code}: {response.text}"
        )

        # Verify subprocess.run was called (attestation generation happened)
        mock_subprocess.assert_called_once()

        # Extract the command that was passed to subprocess.run
        call_args = mock_subprocess.call_args
        cmd = call_args[0][0]  # First positional arg is the command list

        # Verify --user-data flag is NOT in the command
        assert "--user-data" not in cmd, (
            f"Expected --user-data flag to NOT be in the command for /attest, "
            f"but found it in: {cmd}"
        )
