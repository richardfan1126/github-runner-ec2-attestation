"""Property-based tests for encrypted /execute endpoint.

Feature: github-actions-remote-executor
Tests Properties 129, 130, 132, 134 from the design document.
"""
import base64
import json
import os
import struct
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from hypothesis import given, settings, strategies as st
from wolfcrypt.ciphers import MlKemPublic, MlKemType

from src.config import ServerConfig
from src.encryption import EncryptionManager, _AES_KEY_LENGTH, _HKDF_INFO
from src.models import (
    AttestationDocument,
    CloneResult,
    ExecutionRecord,
    ExecutionStatus,
    OIDCValidationResult,
)
from src.server import create_app
from tests.encryption_test_helpers import EncryptionTestContext, make_encrypted_execute_request


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def get_test_config():
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


def _parse_server_public_key(composite_key: bytes) -> tuple[bytes, bytes]:
    """Parse the length-prefixed composite Server_Public_Key."""
    offset = 0
    x25519_len = struct.unpack(">I", composite_key[offset:offset + 4])[0]
    offset += 4
    x25519_pub = composite_key[offset:offset + x25519_len]
    offset += x25519_len
    mlkem_len = struct.unpack(">I", composite_key[offset:offset + 4])[0]
    offset += 4
    mlkem_encap_key = composite_key[offset:offset + mlkem_len]
    return x25519_pub, mlkem_encap_key


def _client_encrypt(payload_dict: dict, server_composite_key: bytes) -> tuple[bytes, bytes, bytes]:
    """Simulate client-side PQ Hybrid KEM encryption.

    Returns (encrypted_payload, client_public_key, shared_key).
    """
    x25519_pub_bytes, mlkem_encap_key_bytes = _parse_server_public_key(server_composite_key)

    # X25519 ECDH
    client_x25519_priv = X25519PrivateKey.generate()
    client_x25519_pub = client_x25519_priv.public_key()
    server_x25519_pub = X25519PublicKey.from_public_bytes(x25519_pub_bytes)
    x25519_shared_secret = client_x25519_priv.exchange(server_x25519_pub)

    # ML-KEM-768 encapsulation
    mlkem_pub = MlKemPublic(MlKemType.ML_KEM_768)
    mlkem_pub.decode_key(mlkem_encap_key_bytes)
    mlkem_shared_secret, mlkem_ciphertext = mlkem_pub.encapsulate()

    # Combine via HKDF
    combined = x25519_shared_secret + mlkem_shared_secret
    shared_key = HKDF(
        algorithm=SHA256(), length=_AES_KEY_LENGTH, salt=None, info=_HKDF_INFO,
    ).derive(combined)

    # Encrypt payload
    plaintext = json.dumps(payload_dict).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = AESGCM(shared_key).encrypt(nonce, plaintext, None)
    encrypted_payload = nonce + ciphertext

    # Build length-prefixed client_public_key
    client_x25519_bytes = client_x25519_pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    client_public_key = (
        struct.pack(">I", len(client_x25519_bytes)) + client_x25519_bytes
        + struct.pack(">I", len(mlkem_ciphertext)) + mlkem_ciphertext
    )

    return encrypted_payload, client_public_key, shared_key


def _make_encrypted_request_body(
    payload_dict: dict, server_pub_bytes: bytes
) -> tuple[dict, bytes]:
    """Build the outer JSON envelope for an encrypted /execute request.

    Returns (outer_body_dict, shared_key).
    """
    encrypted_payload, client_pub_bytes, shared_key = _client_encrypt(
        payload_dict, server_pub_bytes
    )
    outer = {
        "encrypted_payload": base64.b64encode(encrypted_payload).decode(),
        "client_public_key": base64.b64encode(client_pub_bytes).decode(),
    }
    return outer, shared_key


def _valid_decrypted_payload(oidc_token: str = "valid.oidc.token") -> dict:
    return {
        "repository_url": "https://github.com/owner/repo",
        "commit_hash": "a" * 40,
        "script_path": "scripts/build.sh",
        "github_token": "ghp_testtoken123",
        "oidc_token": oidc_token,
    }


def _mock_successful_execution(app):
    """Return a context manager that mocks all downstream calls for a successful execution."""
    from unittest.mock import patch as _patch

    class _Ctx:
        def __enter__(self_ctx):
            self_ctx._patches = []

            p1 = _patch.object(app.state.request_validator, "validate_oidc_token_from_body", return_value=VALID_OIDC_RESULT)
            self_ctx._patches.append(p1)
            p1.start()

            validate_mock = Mock(valid=True, errors=[])
            p2 = _patch.object(app.state.request_validator, "validate_execution_request", return_value=validate_mock)
            self_ctx._patches.append(p2)
            p2.start()

            p3 = _patch.object(app.state.repository_client, "authenticate", return_value=Mock(success=True, error_message=None))
            self_ctx._patches.append(p3)
            p3.start()

            p4 = _patch.object(
                app.state.repository_client,
                "clone_repo",
                return_value=CloneResult(clone_path="/tmp/clone_test", script_path=""),
            )
            self_ctx._patches.append(p4)
            p4.start()

            p5 = _patch.object(app.state.repository_client, "validate_script_exists", return_value=True)
            self_ctx._patches.append(p5)
            p5.start()

            p6 = _patch.object(
                app.state.attestation_generator,
                "generate_attestation",
                return_value=(
                    AttestationDocument(
                        repository_url="https://github.com/owner/repo",
                        commit_hash="a" * 40,
                        script_path="scripts/build.sh",
                        timestamp=datetime.now(timezone.utc),
                        signature=b"test_signature",
                    ),
                    None,
                ),
            )
            self_ctx._patches.append(p6)
            p6.start()

            p7 = _patch.object(app.state.script_executor, "execute_async")
            self_ctx._patches.append(p7)
            p7.start()

            return self_ctx

        def __exit__(self_ctx, *args):
            for p in reversed(self_ctx._patches):
                p.stop()

    return _Ctx()


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

random_bytes_strategy = st.binary(min_size=1, max_size=256)


# ---------------------------------------------------------------------------
# Property 129: Decryption Failure Returns HTTP 400
# ---------------------------------------------------------------------------


class TestDecryptionFailureReturnsHTTP400:
    """**Validates: Requirements 40.5, 42.7**"""

    @given(bad_payload=random_bytes_strategy)
    @settings(max_examples=50)
    def test_random_bytes_as_encrypted_payload(self, bad_payload: bytes):
        """Property 129: Sending random bytes as encrypted_payload returns HTTP 400."""
        encryption_manager = EncryptionManager()
        app = create_app(get_test_config(), encryption_manager=encryption_manager)
        client = TestClient(app)

        # Build a valid-looking client_public_key in the new format
        client_x25519_priv = X25519PrivateKey.generate()
        client_x25519_pub = client_x25519_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        # Use a dummy ML-KEM ciphertext (wrong, but format is valid)
        dummy_mlkem_ct = os.urandom(1088)
        client_pub = (
            struct.pack(">I", len(client_x25519_pub)) + client_x25519_pub
            + struct.pack(">I", len(dummy_mlkem_ct)) + dummy_mlkem_ct
        )

        body = {
            "encrypted_payload": base64.b64encode(bad_payload).decode(),
            "client_public_key": base64.b64encode(client_pub).decode(),
        }

        response = client.post("/execute", json=body)
        assert response.status_code == 400

    @given(st.just(None))
    @settings(max_examples=20)
    def test_wrong_client_key_returns_400(self, _):
        """Property 129: Using a different client key than the one used for
        encryption causes decryption failure → HTTP 400."""
        encryption_manager = EncryptionManager()
        app = create_app(get_test_config(), encryption_manager=encryption_manager)
        client = TestClient(app)

        payload = _valid_decrypted_payload()

        # Encrypt with one client key
        encrypted_payload, _correct_pub, _sk = _client_encrypt(
            payload, encryption_manager.server_public_key
        )

        # Send a *different* client public key (different X25519 + different ML-KEM ct)
        wrong_x25519_priv = X25519PrivateKey.generate()
        wrong_x25519_pub = wrong_x25519_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        wrong_mlkem_ct = os.urandom(1088)
        wrong_pub = (
            struct.pack(">I", len(wrong_x25519_pub)) + wrong_x25519_pub
            + struct.pack(">I", len(wrong_mlkem_ct)) + wrong_mlkem_ct
        )

        body = {
            "encrypted_payload": base64.b64encode(encrypted_payload).decode(),
            "client_public_key": base64.b64encode(wrong_pub).decode(),
        }

        response = client.post("/execute", json=body)
        assert response.status_code == 400

    @given(flip_index=st.integers(min_value=0, max_value=255))
    @settings(max_examples=30)
    def test_corrupted_ciphertext_returns_400(self, flip_index: int):
        """Property 129: Flipping a byte in the ciphertext causes decryption
        failure → HTTP 400."""
        encryption_manager = EncryptionManager()
        app = create_app(get_test_config(), encryption_manager=encryption_manager)
        client = TestClient(app)

        payload = _valid_decrypted_payload()
        encrypted_payload, client_pub, _sk = _client_encrypt(
            payload, encryption_manager.server_public_key
        )

        # Corrupt a byte
        corrupted = bytearray(encrypted_payload)
        idx = flip_index % len(corrupted)
        corrupted[idx] ^= 0xFF
        corrupted = bytes(corrupted)

        body = {
            "encrypted_payload": base64.b64encode(corrupted).decode(),
            "client_public_key": base64.b64encode(client_pub).decode(),
        }

        response = client.post("/execute", json=body)
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Property 130: OIDC Token Extracted from Decrypted Body
# ---------------------------------------------------------------------------


class TestOIDCTokenExtractedFromDecryptedBody:
    """**Validates: Requirements 40.6, 40.9, 2.1, 2.2**"""

    @given(oidc_token=st.text(min_size=10, max_size=200).filter(lambda t: t.strip()))
    @settings(max_examples=50)
    def test_oidc_token_from_body_not_header(self, oidc_token: str):
        """Property 130: The server extracts and validates the OIDC token from
        the decrypted body oidc_token field, not from the Authorization header."""
        encryption_manager = EncryptionManager()
        app = create_app(get_test_config(), encryption_manager=encryption_manager)
        client = TestClient(app)

        payload = _valid_decrypted_payload(oidc_token=oidc_token)
        outer_body, _shared_key = _make_encrypted_request_body(
            payload, encryption_manager.server_public_key
        )

        captured_calls = []

        def spy_validate(token_str):
            captured_calls.append(token_str)
            return VALID_OIDC_RESULT

        with _mock_successful_execution(app):
            with patch.object(
                app.state.request_validator,
                "validate_oidc_token_from_body",
                side_effect=spy_validate,
            ):
                # Send request WITHOUT Authorization header
                response = client.post("/execute", json=outer_body)

        # The validator should have been called with the raw token from the body
        assert len(captured_calls) >= 1
        assert captured_calls[0] == oidc_token

        # If the server tried to use the Authorization header (which is absent),
        # it would have passed None → 401. A 200 confirms body extraction worked.
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Property 132: Execute Response Encryption Round-Trip
# ---------------------------------------------------------------------------


class TestExecuteResponseEncryptionRoundTrip:
    """**Validates: Requirements 41.3, 42.1, 42.8**"""

    @given(st.just(None))
    @settings(max_examples=30)
    def test_encrypted_response_round_trip(self, _):
        """Property 132: Encrypt /execute response with Shared_Key, client
        decrypts with same key, verify original content recovered."""
        encryption_manager = EncryptionManager()
        app = create_app(get_test_config(), encryption_manager=encryption_manager)
        client = TestClient(app)

        payload = _valid_decrypted_payload()
        outer_body, shared_key = _make_encrypted_request_body(
            payload, encryption_manager.server_public_key
        )

        with _mock_successful_execution(app):
            response = client.post("/execute", json=outer_body)

        assert response.status_code == 200
        resp_json = response.json()

        # Response should contain encrypted_response field
        assert "encrypted_response" in resp_json

        # Decrypt the response using the shared key
        encrypted_resp_bytes = base64.b64decode(resp_json["encrypted_response"])
        nonce = encrypted_resp_bytes[:12]
        ciphertext = encrypted_resp_bytes[12:]
        plaintext = AESGCM(shared_key).decrypt(nonce, ciphertext, None)
        decrypted_response = json.loads(plaintext)

        # Verify the decrypted response contains expected fields
        assert "execution_id" in decrypted_response
        assert "attestation_document" in decrypted_response
        assert "status" in decrypted_response
        assert decrypted_response["status"] == "queued"

        # Verify attestation_document is valid base64
        base64.b64decode(decrypted_response["attestation_document"])


# ---------------------------------------------------------------------------
# Property 134: Missing Encryption Context Returns HTTP 400
# ---------------------------------------------------------------------------


class TestMissingEncryptionContextReturnsHTTP400:
    """**Validates: Requirements 42.6**"""

    @given(execution_id=st.uuids().map(str))
    @settings(max_examples=30)
    def test_no_encryption_context_returns_400(self, execution_id: str):
        """Property 134: Request /execution/{id}/output with an execution_id
        that has no Encryption_Context returns HTTP 400."""
        encryption_manager = EncryptionManager()
        app = create_app(get_test_config(), encryption_manager=encryption_manager)
        client = TestClient(app)

        # Ensure no encryption context exists
        encryption_manager.remove_encryption_context(execution_id)

        # Create a dummy execution record so the endpoint doesn't 404
        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="scripts/test.sh",
            status=ExecutionStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            started_at=None,
            completed_at=None,
            exit_code=None,
            timeout_seconds=300,
        )

        with patch.object(app.state.execution_manager, "get_execution", return_value=record):
            response = client.post(
                f"/execution/{execution_id}/output",
                json={"encrypted_payload": base64.b64encode(b"dummy").decode()},
            )

        assert response.status_code == 400, (
            f"Expected 400 for missing encryption context, got {response.status_code}"
        )
        data = response.json()
        assert data["detail"]["error"] == "no_encryption_context"
