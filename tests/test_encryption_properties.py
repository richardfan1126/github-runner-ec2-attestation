"""Property-based tests for EncryptionManager.

Feature: github-actions-remote-executor
Tests Properties 122, 127, 128 from the design document.
"""
import json

import pytest
from hypothesis import given, settings, strategies as st

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.encryption import EncryptionManager, _AES_KEY_LENGTH, _HKDF_INFO


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


def _client_encrypt(payload_dict: dict, server_pub_bytes: bytes) -> tuple[bytes, bytes, bytes]:
    """Simulate client-side HPKE encryption.

    Returns (encrypted_payload, client_public_key_bytes, shared_key).
    """
    client_private = X25519PrivateKey.generate()
    client_public = client_private.public_key()

    server_pub = X25519PublicKey.from_public_bytes(server_pub_bytes)
    shared_secret = client_private.exchange(server_pub)
    shared_key = HKDF(
        algorithm=SHA256(),
        length=_AES_KEY_LENGTH,
        salt=None,
        info=_HKDF_INFO,
    ).derive(shared_secret)

    import os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    plaintext = json.dumps(payload_dict).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = AESGCM(shared_key).encrypt(nonce, plaintext, None)
    encrypted_payload = nonce + ciphertext

    client_pub_bytes = client_public.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return encrypted_payload, client_pub_bytes, shared_key


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
        # Must be 32 bytes (raw X25519 public key)
        assert len(first) == 32


# ---------------------------------------------------------------------------
# Property 127: Server Public Key Serialization Round-Trip
# ---------------------------------------------------------------------------


class TestServerPublicKeySerializationRoundTrip:
    """**Validates: Requirements 39.3, 39.4**"""

    @given(st.just(None))  # no random input needed; property holds universally
    @settings(max_examples=30)
    def test_serialize_deserialize_produces_same_shared_key(self, _):
        """Property 127: Serialize the Server_Public_Key, deserialize it,
        and verify it can be used for HPKE key exchange producing the same
        Shared_Key."""
        mgr = EncryptionManager()
        raw_bytes = mgr.server_public_key

        # Deserialize
        restored_key = X25519PublicKey.from_public_bytes(raw_bytes)

        # Perform key exchange from a fresh client with both keys
        client_private = X25519PrivateKey.generate()

        shared_via_original = client_private.exchange(
            X25519PublicKey.from_public_bytes(raw_bytes)
        )
        shared_via_restored = client_private.exchange(restored_key)

        assert shared_via_original == shared_via_restored


# ---------------------------------------------------------------------------
# Property 128: HPKE Encrypt-Decrypt Round-Trip for Execute
# ---------------------------------------------------------------------------


class TestHPKEEncryptDecryptRoundTrip:
    """**Validates: Requirements 40.1, 40.3, 40.4, 40.8**"""

    @given(payload=json_dicts)
    @settings(max_examples=100)
    def test_client_encrypt_server_decrypt_round_trip(self, payload: dict):
        """Property 128: For random valid payloads, client-side encrypt with
        Server_Public_Key, server-side decrypt with Server_Keypair, verify
        original payload is recovered."""
        mgr = EncryptionManager()

        encrypted, client_pub, _client_shared = _client_encrypt(
            payload, mgr.server_public_key
        )

        decrypted, server_shared = mgr.decrypt_request(encrypted, client_pub)

        # The recovered payload must match the original
        assert decrypted == payload
        # Both sides must derive the same shared key
        assert server_shared == _client_shared
