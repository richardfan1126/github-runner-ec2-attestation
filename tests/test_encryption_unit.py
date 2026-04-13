"""Unit tests for EncryptionManager (PQ Hybrid KEM: X25519 + ML-KEM-768).

Feature: github-actions-remote-executor
Requirements: 36.1, 36.2, 36.3, 36.4, 36.5, 36.6, 39.1, 39.3, 40.1-40.6, 40.11, 41.1, 41.6, 41.7
"""
import hashlib
import json
import os
import struct
import threading

import pytest

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from wolfcrypt.ciphers import MlKemPublic, MlKemType

from src.encryption import (
    EncryptionManager,
    _AES_KEY_LENGTH,
    _HKDF_INFO,
    _MLKEM768_CIPHERTEXT_LENGTH,
    _MLKEM768_ENCAP_KEY_LENGTH,
    _X25519_KEY_LENGTH,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_server_public_key(composite_key: bytes) -> tuple[bytes, bytes]:
    """Parse the length-prefixed composite Server_Public_Key.

    Returns (x25519_pub_bytes, mlkem_encap_key_bytes).
    """
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
# Test composite keypair generation at startup
# ---------------------------------------------------------------------------

class TestKeypairGeneration:
    def test_composite_key_has_correct_structure(self):
        mgr = EncryptionManager()
        key = mgr.server_public_key
        x25519_pub, mlkem_encap = _parse_server_public_key(key)
        assert len(x25519_pub) == _X25519_KEY_LENGTH
        assert len(mlkem_encap) == _MLKEM768_ENCAP_KEY_LENGTH

    def test_composite_key_total_length(self):
        mgr = EncryptionManager()
        # 4 + 32 + 4 + 1184 = 1224
        expected = 4 + _X25519_KEY_LENGTH + 4 + _MLKEM768_ENCAP_KEY_LENGTH
        assert len(mgr.server_public_key) == expected

    def test_x25519_component_is_valid(self):
        mgr = EncryptionManager()
        x25519_pub, _ = _parse_server_public_key(mgr.server_public_key)
        key = X25519PublicKey.from_public_bytes(x25519_pub)
        assert key is not None

    def test_mlkem_component_is_valid(self):
        mgr = EncryptionManager()
        _, mlkem_encap = _parse_server_public_key(mgr.server_public_key)
        pub = MlKemPublic(MlKemType.ML_KEM_768)
        pub.decode_key(mlkem_encap)
        # Should be able to encapsulate without error
        ss, ct = pub.encapsulate()
        assert len(ss) == 32
        assert len(ct) == _MLKEM768_CIPHERTEXT_LENGTH

    def test_different_instances_have_different_keys(self):
        a = EncryptionManager()
        b = EncryptionManager()
        assert a.server_public_key != b.server_public_key


# ---------------------------------------------------------------------------
# Test server_public_key_fingerprint
# ---------------------------------------------------------------------------

class TestServerPublicKeyFingerprint:
    def test_fingerprint_is_sha256(self):
        mgr = EncryptionManager()
        fp = mgr.server_public_key_fingerprint
        assert len(fp) == 32  # SHA-256 is 32 bytes

    def test_fingerprint_matches_key(self):
        mgr = EncryptionManager()
        expected = hashlib.sha256(mgr.server_public_key).digest()
        assert mgr.server_public_key_fingerprint == expected

    def test_fingerprint_is_deterministic(self):
        mgr = EncryptionManager()
        assert mgr.server_public_key_fingerprint == mgr.server_public_key_fingerprint

    def test_different_instances_have_different_fingerprints(self):
        a = EncryptionManager()
        b = EncryptionManager()
        assert a.server_public_key_fingerprint != b.server_public_key_fingerprint


# ---------------------------------------------------------------------------
# Test PQ Hybrid KEM encrypt/decrypt round-trip
# ---------------------------------------------------------------------------

class TestEncryptDecryptRoundTrip:
    def test_round_trip_simple_payload(self):
        mgr = EncryptionManager()
        payload = {"repository_url": "https://github.com/owner/repo", "commit_hash": "a" * 40}
        encrypted, client_pub, client_shared = _client_encrypt(payload, mgr.server_public_key)

        decrypted, server_shared = mgr.decrypt_request(encrypted, client_pub)
        assert decrypted == payload
        assert server_shared == client_shared

    def test_encrypt_response_decrypt_round_trip(self):
        mgr = EncryptionManager()
        payload = {"status": "queued", "execution_id": "abc-123"}
        encrypted, client_pub, shared_key = _client_encrypt({"init": True}, mgr.server_public_key)
        _, server_shared = mgr.decrypt_request(encrypted, client_pub)

        response_encrypted = mgr.encrypt_response(payload, server_shared)
        decrypted = mgr.decrypt_with_shared_key(response_encrypted, server_shared)
        assert decrypted == payload

    def test_decrypt_with_shared_key_round_trip(self):
        mgr = EncryptionManager()
        payload = {"oidc_token": "tok", "offset": 0}
        encrypted, client_pub, shared_key = _client_encrypt({"init": True}, mgr.server_public_key)
        _, server_shared = mgr.decrypt_request(encrypted, client_pub)

        # Simulate client encrypting a follow-up request with the shared key
        follow_up = json.dumps(payload).encode("utf-8")
        nonce = os.urandom(12)
        ct = AESGCM(server_shared).encrypt(nonce, follow_up, None)
        encrypted_follow_up = nonce + ct

        result = mgr.decrypt_with_shared_key(encrypted_follow_up, server_shared)
        assert result == payload

    def test_large_payload_round_trip(self):
        mgr = EncryptionManager()
        payload = {"data": "x" * 10000, "nested": {"key": list(range(100))}}
        encrypted, client_pub, client_shared = _client_encrypt(payload, mgr.server_public_key)
        decrypted, server_shared = mgr.decrypt_request(encrypted, client_pub)
        assert decrypted == payload
        assert server_shared == client_shared


# ---------------------------------------------------------------------------
# Test decryption failure cases
# ---------------------------------------------------------------------------

class TestDecryptionFailure:
    def test_wrong_client_x25519_key_raises_value_error(self):
        mgr = EncryptionManager()
        payload = {"data": "secret"}
        encrypted, client_pub, _ = _client_encrypt(payload, mgr.server_public_key)

        # Replace X25519 component with a different key
        wrong_x25519 = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        _, mlkem_ct = _parse_length_prefixed_raw(client_pub)
        wrong_client_pub = (
            struct.pack(">I", len(wrong_x25519)) + wrong_x25519
            + struct.pack(">I", len(mlkem_ct)) + mlkem_ct
        )
        with pytest.raises(ValueError):
            mgr.decrypt_request(encrypted, wrong_client_pub)

    def test_corrupted_ciphertext_raises_value_error(self):
        mgr = EncryptionManager()
        payload = {"data": "secret"}
        encrypted, client_pub, _ = _client_encrypt(payload, mgr.server_public_key)

        corrupted = bytearray(encrypted)
        corrupted[-1] ^= 0xFF
        corrupted[-5] ^= 0xFF
        with pytest.raises(ValueError):
            mgr.decrypt_request(bytes(corrupted), client_pub)

    def test_truncated_payload_raises_value_error(self):
        mgr = EncryptionManager()
        # Build a valid-looking client_public_key
        _, client_pub, _ = _client_encrypt({"x": 1}, mgr.server_public_key)
        with pytest.raises(ValueError):
            mgr.decrypt_request(b"short", client_pub)

    def test_invalid_client_public_key_format_raises_value_error(self):
        mgr = EncryptionManager()
        with pytest.raises(ValueError):
            mgr.decrypt_request(b"\x00" * 50, b"bad-key")

    def test_invalid_mlkem_ciphertext_raises_value_error(self):
        mgr = EncryptionManager()
        payload = {"data": "secret"}
        encrypted, _, _ = _client_encrypt(payload, mgr.server_public_key)

        # Valid X25519 key but garbage ML-KEM ciphertext
        x25519_bytes = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        bad_mlkem_ct = os.urandom(_MLKEM768_CIPHERTEXT_LENGTH)
        bad_client_pub = (
            struct.pack(">I", len(x25519_bytes)) + x25519_bytes
            + struct.pack(">I", len(bad_mlkem_ct)) + bad_mlkem_ct
        )
        # This will derive a wrong shared key, so decryption should fail
        with pytest.raises(ValueError):
            mgr.decrypt_request(encrypted, bad_client_pub)

    def test_decrypt_with_wrong_shared_key_raises_value_error(self):
        mgr = EncryptionManager()
        payload = {"data": "secret"}
        encrypted, client_pub, shared_key = _client_encrypt(payload, mgr.server_public_key)
        _, server_shared = mgr.decrypt_request(encrypted, client_pub)

        response_encrypted = mgr.encrypt_response(payload, server_shared)
        wrong_key = os.urandom(32)
        with pytest.raises(ValueError):
            mgr.decrypt_with_shared_key(response_encrypted, wrong_key)

    def test_single_component_client_key_raises_value_error(self):
        mgr = EncryptionManager()
        # Only one length-prefixed component instead of two
        single = struct.pack(">I", 32) + os.urandom(32)
        with pytest.raises(ValueError):
            mgr.decrypt_request(b"\x00" * 50, single)


# ---------------------------------------------------------------------------
# Helper for parsing raw length-prefixed data in tests
# ---------------------------------------------------------------------------

def _parse_length_prefixed_raw(data: bytes) -> tuple[bytes, bytes]:
    """Parse two length-prefixed components from data."""
    offset = 0
    len1 = struct.unpack(">I", data[offset:offset + 4])[0]
    offset += 4
    comp1 = data[offset:offset + len1]
    offset += len1
    len2 = struct.unpack(">I", data[offset:offset + 4])[0]
    offset += 4
    comp2 = data[offset:offset + len2]
    return comp1, comp2


# ---------------------------------------------------------------------------
# Test Encryption_Context store/get/remove lifecycle
# ---------------------------------------------------------------------------

class TestEncryptionContext:
    def test_store_and_get(self):
        mgr = EncryptionManager()
        key = os.urandom(32)
        mgr.store_encryption_context("exec-1", key)
        assert mgr.get_shared_key("exec-1") == key

    def test_get_nonexistent_returns_none(self):
        mgr = EncryptionManager()
        assert mgr.get_shared_key("nonexistent") is None

    def test_remove_context(self):
        mgr = EncryptionManager()
        key = os.urandom(32)
        mgr.store_encryption_context("exec-2", key)
        mgr.remove_encryption_context("exec-2")
        assert mgr.get_shared_key("exec-2") is None

    def test_remove_nonexistent_does_not_raise(self):
        mgr = EncryptionManager()
        mgr.remove_encryption_context("nope")  # should not raise

    def test_overwrite_context(self):
        mgr = EncryptionManager()
        key1 = os.urandom(32)
        key2 = os.urandom(32)
        mgr.store_encryption_context("exec-3", key1)
        mgr.store_encryption_context("exec-3", key2)
        assert mgr.get_shared_key("exec-3") == key2


# ---------------------------------------------------------------------------
# Test thread-safety of context operations
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_store_and_get(self):
        mgr = EncryptionManager()
        errors = []
        num_threads = 20

        def worker(idx: int):
            try:
                eid = f"exec-{idx}"
                key = os.urandom(32)
                mgr.store_encryption_context(eid, key)
                retrieved = mgr.get_shared_key(eid)
                if retrieved != key:
                    errors.append(f"Mismatch for {eid}")
                mgr.remove_encryption_context(eid)
                if mgr.get_shared_key(eid) is not None:
                    errors.append(f"Not removed for {eid}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread-safety errors: {errors}"


# ---------------------------------------------------------------------------
# Test HKDF info label
# ---------------------------------------------------------------------------

class TestHKDFInfoLabel:
    def test_hkdf_info_is_pq_hybrid(self):
        assert _HKDF_INFO == b"pq-hybrid-shared-key"
