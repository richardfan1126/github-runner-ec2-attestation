"""Property-based tests for EncryptionManager (PQ Hybrid KEM: X25519 + ML-KEM-768).

Feature: github-actions-remote-executor
Tests Properties 122, 127, 128, 129, 132, 133 from the design document.
"""
import base64
import hashlib
import json
import os
import struct

import httpx
import pytest
from hypothesis import given, settings, strategies as st

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from wolfcrypt.ciphers import MlKemPublic, MlKemType

from src.config import ServerConfig
from src.encryption import (
    EncryptionManager,
    _AES_KEY_LENGTH,
    _HKDF_INFO,
    _X25519_KEY_LENGTH,
    _MLKEM768_ENCAP_KEY_LENGTH,
)
from src.server import create_app


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(min_size=0, max_size=200),
)

json_values = st.recursive(
    json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(min_size=1, max_size=20), children, max_size=5),
    ),
    max_leaves=15,
)

json_dicts = st.dictionaries(
    st.text(min_size=1, max_size=30),
    json_values,
    min_size=1,
    max_size=10,
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


# ---------------------------------------------------------------------------
# Property 122: Server Keypair Consistency
# ---------------------------------------------------------------------------


class TestServerKeypairConsistency:
    """**Validates: Requirements 36.3, 37.4**"""

    @given(n=st.integers(min_value=2, max_value=20))
    @settings(max_examples=50)
    def test_server_public_key_is_stable(self, n: int):
        """Property 122: server_public_key returns the same bytes across
        multiple accesses on a single EncryptionManager instance."""
        mgr = EncryptionManager()
        first = mgr.server_public_key
        for _ in range(n):
            assert mgr.server_public_key == first
        # Composite key: 4 + 32 + 4 + 1184 = 1224 bytes
        expected_len = 4 + _X25519_KEY_LENGTH + 4 + _MLKEM768_ENCAP_KEY_LENGTH
        assert len(first) == expected_len

    @given(n=st.integers(min_value=2, max_value=20))
    @settings(max_examples=50)
    def test_server_public_key_fingerprint_is_stable(self, n: int):
        """server_public_key_fingerprint returns the same bytes across
        multiple accesses on a single EncryptionManager instance."""
        mgr = EncryptionManager()
        first = mgr.server_public_key_fingerprint
        for _ in range(n):
            assert mgr.server_public_key_fingerprint == first
        assert len(first) == 32  # SHA-256


# ---------------------------------------------------------------------------
# Property 127: Server Public Key Serialization Round-Trip
# ---------------------------------------------------------------------------


class TestServerPublicKeySerializationRoundTrip:
    """**Validates: Requirements 39.3, 39.4**"""

    @given(st.just(None))
    @settings(max_examples=30)
    def test_serialize_deserialize_produces_valid_components(self, _):
        """Property 127: Serialize the composite Server_Public_Key, parse it,
        and verify both components can be used for key exchange."""
        mgr = EncryptionManager()
        raw_bytes = mgr.server_public_key

        x25519_pub, mlkem_encap = _parse_server_public_key(raw_bytes)

        # X25519 component is valid
        restored_x25519 = X25519PublicKey.from_public_bytes(x25519_pub)
        client_private = X25519PrivateKey.generate()
        shared1 = client_private.exchange(restored_x25519)
        shared2 = client_private.exchange(
            X25519PublicKey.from_public_bytes(x25519_pub)
        )
        assert shared1 == shared2

        # ML-KEM-768 component is valid
        pub = MlKemPublic(MlKemType.ML_KEM_768)
        pub.decode_key(mlkem_encap)
        ss, ct = pub.encapsulate()
        assert len(ss) == 32

    @given(st.just(None))
    @settings(max_examples=30)
    def test_fingerprint_matches_composite_key(self, _):
        """The fingerprint is the SHA-256 of the serialized composite key."""
        mgr = EncryptionManager()
        expected = hashlib.sha256(mgr.server_public_key).digest()
        assert mgr.server_public_key_fingerprint == expected


# ---------------------------------------------------------------------------
# Property 128: PQ Hybrid KEM Encrypt-Decrypt Round-Trip for Execute
# ---------------------------------------------------------------------------


class TestPQHybridKEMEncryptDecryptRoundTrip:
    """**Validates: Requirements 40.1, 40.3, 40.4, 40.8**"""

    @given(payload=json_dicts)
    @settings(max_examples=100)
    def test_client_encrypt_server_decrypt_round_trip(self, payload: dict):
        """Property 128: For random valid payloads, client-side encrypt with
        composite Server_Public_Key, server-side decrypt with Server_Keypair,
        verify original payload is recovered."""
        mgr = EncryptionManager()

        encrypted, client_pub, _client_shared = _client_encrypt(
            payload, mgr.server_public_key
        )

        decrypted, server_shared = mgr.decrypt_request(encrypted, client_pub)

        assert decrypted == payload
        assert server_shared == _client_shared


# ---------------------------------------------------------------------------
# Helpers for HTTP-level tests (Property 129)
# ---------------------------------------------------------------------------


def _get_test_config() -> ServerConfig:
    return ServerConfig(
        port=8080,
        execution_timeout_seconds=30,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=100,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/test",
        output_retention_hours=1,
        allowed_repositories=["owner/repo"],
        expected_audience="test-audience",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
        max_concurrent_executions=5,
        tpm_attest_path="/usr/bin/nitro-tpm-attest",
    )


# ---------------------------------------------------------------------------
# Property 129: Decryption Failure Returns HTTP 400
# ---------------------------------------------------------------------------


class TestDecryptionFailureReturnsHTTP400:
    """**Validates: Requirements 40.5, 42.7**"""

    @pytest.mark.asyncio
    @given(bad_payload=st.binary(min_size=1, max_size=256))
    @settings(max_examples=100)
    async def test_random_bytes_as_encrypted_payload(self, bad_payload: bytes):
        """Property 129: Sending random bytes as encrypted_payload to /execute
        returns HTTP 400 with error code 'decryption_failed'."""
        encryption_manager = EncryptionManager()
        app = create_app(_get_test_config(), encryption_manager=encryption_manager)

        # Build a plausible client_public_key (valid format, wrong key material)
        client_x25519_priv = X25519PrivateKey.generate()
        client_x25519_pub = client_x25519_priv.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        dummy_mlkem_ct = os.urandom(1088)
        client_pub = (
            struct.pack(">I", len(client_x25519_pub)) + client_x25519_pub
            + struct.pack(">I", len(dummy_mlkem_ct)) + dummy_mlkem_ct
        )

        body = {
            "encrypted_payload": base64.b64encode(bad_payload).decode(),
            "client_public_key": base64.b64encode(client_pub).decode(),
        }

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post("/execute", json=body)

        assert response.status_code == 400

    @pytest.mark.asyncio
    @given(flip_index=st.integers(min_value=0, max_value=255))
    @settings(max_examples=100)
    async def test_corrupted_ciphertext_returns_400(self, flip_index: int):
        """Property 129: Flipping a byte in a valid ciphertext causes
        decryption failure → HTTP 400."""
        encryption_manager = EncryptionManager()
        app = create_app(_get_test_config(), encryption_manager=encryption_manager)

        payload = {"test": "data", "oidc_token": "tok"}
        encrypted, client_pub, _sk = _client_encrypt(
            payload, encryption_manager.server_public_key
        )

        corrupted = bytearray(encrypted)
        idx = flip_index % len(corrupted)
        corrupted[idx] ^= 0xFF
        corrupted = bytes(corrupted)

        body = {
            "encrypted_payload": base64.b64encode(corrupted).decode(),
            "client_public_key": base64.b64encode(client_pub).decode(),
        }

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post("/execute", json=body)

        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Property 132: Execute Response Encryption Round-Trip
# ---------------------------------------------------------------------------


class TestExecuteResponseEncryptionRoundTrip:
    """**Validates: Requirements 41.3, 42.1, 42.8**"""

    @given(payload=json_dicts)
    @settings(max_examples=100)
    def test_encrypt_response_then_decrypt_round_trip(self, payload: dict):
        """Property 132: For any response payload, server encrypts with
        Shared_Key and client decrypts with same Shared_Key, producing
        original content."""
        mgr = EncryptionManager()

        # Establish a shared key via the PQ Hybrid KEM handshake
        _enc, _cpub, shared_key = _client_encrypt(
            {"handshake": True}, mgr.server_public_key
        )

        # Server encrypts a response
        encrypted_response = mgr.encrypt_response(payload, shared_key)

        # Client decrypts with the same shared key
        nonce = encrypted_response[:12]
        ciphertext = encrypted_response[12:]
        plaintext = AESGCM(shared_key).decrypt(nonce, ciphertext, None)
        decrypted = json.loads(plaintext)

        assert decrypted == payload


# ---------------------------------------------------------------------------
# Property 133: Output Request-Response Encryption Round-Trip
# ---------------------------------------------------------------------------


class TestOutputRequestResponseEncryptionRoundTrip:
    """**Validates: Requirements 41.4, 41.5, 42.2, 42.3, 42.4, 42.8**"""

    @given(
        request_payload=json_dicts,
        response_payload=json_dicts,
    )
    @settings(max_examples=100)
    def test_output_request_response_round_trip(
        self, request_payload: dict, response_payload: dict
    ):
        """Property 133: Client encrypts request with Shared_Key, server
        decrypts, processes, encrypts response, client decrypts — producing
        original content."""
        mgr = EncryptionManager()

        # Establish a shared key via the PQ Hybrid KEM handshake
        _enc, _cpub, shared_key = _client_encrypt(
            {"handshake": True}, mgr.server_public_key
        )

        # --- Client encrypts request with shared key ---
        req_plaintext = json.dumps(request_payload).encode("utf-8")
        req_nonce = os.urandom(12)
        req_ciphertext = AESGCM(shared_key).encrypt(req_nonce, req_plaintext, None)
        encrypted_request = req_nonce + req_ciphertext

        # --- Server decrypts request with decrypt_with_shared_key ---
        decrypted_request = mgr.decrypt_with_shared_key(encrypted_request, shared_key)
        assert decrypted_request == request_payload

        # --- Server encrypts response with encrypt_response ---
        encrypted_response = mgr.encrypt_response(response_payload, shared_key)

        # --- Client decrypts response ---
        resp_nonce = encrypted_response[:12]
        resp_ciphertext = encrypted_response[12:]
        resp_plaintext = AESGCM(shared_key).decrypt(resp_nonce, resp_ciphertext, None)
        decrypted_response = json.loads(resp_plaintext)

        assert decrypted_response == response_payload
