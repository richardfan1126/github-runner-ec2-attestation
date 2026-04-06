"""Unit tests for EncryptionManager.

Feature: github-actions-remote-executor
Requirements: 36.1, 36.2, 40.3, 40.4, 40.5, 41.1, 41.6, 41.7
"""
import json
import os
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

from src.encryption import EncryptionManager, _AES_KEY_LENGTH, _HKDF_INFO


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client_encrypt(payload_dict: dict, server_pub_bytes: bytes) -> tuple[bytes, bytes, bytes]:
    """Simulate client-side encryption. Returns (encrypted, client_pub, shared_key)."""
    client_priv = X25519PrivateKey.generate()
    client_pub = client_priv.public_key()
    server_pub = X25519PublicKey.from_public_bytes(server_pub_bytes)
    shared_secret = client_priv.exchange(server_pub)
    shared_key = HKDF(
        algorithm=SHA256(), length=_AES_KEY_LENGTH, salt=None, info=_HKDF_INFO,
    ).derive(shared_secret)
    plaintext = json.dumps(payload_dict).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = AESGCM(shared_key).encrypt(nonce, plaintext, None)
    client_pub_bytes = client_pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return nonce + ciphertext, client_pub_bytes, shared_key


# ---------------------------------------------------------------------------
# Test keypair generation at startup
# ---------------------------------------------------------------------------

class TestKeypairGeneration:
    def test_generates_32_byte_public_key(self):
        mgr = EncryptionManager()
        assert isinstance(mgr.server_public_key, bytes)
        assert len(mgr.server_public_key) == 32

    def test_public_key_is_valid_x25519(self):
        mgr = EncryptionManager()
        key = X25519PublicKey.from_public_bytes(mgr.server_public_key)
        assert key is not None

    def test_different_instances_have_different_keys(self):
        a = EncryptionManager()
        b = EncryptionManager()
        assert a.server_public_key != b.server_public_key


# ---------------------------------------------------------------------------
# Test encrypt/decrypt round-trip with known payloads
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


# ---------------------------------------------------------------------------
# Test decryption failure cases
# ---------------------------------------------------------------------------

class TestDecryptionFailure:
    def test_wrong_client_key_raises_value_error(self):
        mgr = EncryptionManager()
        payload = {"data": "secret"}
        encrypted, _, _ = _client_encrypt(payload, mgr.server_public_key)

        # Use a completely different client public key
        wrong_key = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        with pytest.raises(ValueError):
            mgr.decrypt_request(encrypted, wrong_key)

    def test_corrupted_ciphertext_raises_value_error(self):
        mgr = EncryptionManager()
        payload = {"data": "secret"}
        encrypted, client_pub, _ = _client_encrypt(payload, mgr.server_public_key)

        # Flip some bytes in the ciphertext portion
        corrupted = bytearray(encrypted)
        corrupted[-1] ^= 0xFF
        corrupted[-5] ^= 0xFF
        with pytest.raises(ValueError):
            mgr.decrypt_request(bytes(corrupted), client_pub)

    def test_truncated_payload_raises_value_error(self):
        mgr = EncryptionManager()
        with pytest.raises(ValueError):
            mgr.decrypt_request(b"short", b"\x00" * 32)

    def test_invalid_client_public_key_raises_value_error(self):
        mgr = EncryptionManager()
        with pytest.raises(ValueError):
            mgr.decrypt_request(b"\x00" * 50, b"bad-key")

    def test_decrypt_with_wrong_shared_key_raises_value_error(self):
        mgr = EncryptionManager()
        payload = {"data": "secret"}
        encrypted, client_pub, shared_key = _client_encrypt(payload, mgr.server_public_key)
        _, server_shared = mgr.decrypt_request(encrypted, client_pub)

        response_encrypted = mgr.encrypt_response(payload, server_shared)
        wrong_key = os.urandom(32)
        with pytest.raises(ValueError):
            mgr.decrypt_with_shared_key(response_encrypted, wrong_key)


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
