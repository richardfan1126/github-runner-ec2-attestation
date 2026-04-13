"""Encryption test helpers for PQ Hybrid KEM (X25519 + ML-KEM-768).

Provides utilities to create encrypted requests and decrypt responses
for testing the encrypted /execute and /execution/{id}/output endpoints.
"""
import base64
import json
import os
import struct

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from wolfcrypt.ciphers import MlKemPublic, MlKemType

from src.encryption import EncryptionManager, _AES_KEY_LENGTH, _HKDF_INFO


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


class EncryptionTestContext:
    """Holds an EncryptionManager and a client keypair for test use."""

    def __init__(self):
        self.encryption_manager = EncryptionManager()

        # Parse server composite public key
        server_key = self.encryption_manager.server_public_key
        x25519_pub_bytes, mlkem_encap_key_bytes = _parse_server_public_key(server_key)

        # X25519 client key exchange
        self._client_x25519_private = X25519PrivateKey.generate()
        self._client_x25519_public = self._client_x25519_private.public_key()
        self._client_x25519_pub_bytes = self._client_x25519_public.public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        server_x25519_pub = X25519PublicKey.from_public_bytes(x25519_pub_bytes)
        x25519_shared_secret = self._client_x25519_private.exchange(server_x25519_pub)

        # ML-KEM-768 encapsulation
        mlkem_pub = MlKemPublic(MlKemType.ML_KEM_768)
        mlkem_pub.decode_key(mlkem_encap_key_bytes)
        mlkem_shared_secret, self._mlkem_ciphertext = mlkem_pub.encapsulate()

        # Derive hybrid shared key
        combined = x25519_shared_secret + mlkem_shared_secret
        self.shared_key = HKDF(
            algorithm=SHA256(), length=_AES_KEY_LENGTH, salt=None, info=_HKDF_INFO,
        ).derive(combined)

        # Build length-prefixed client_public_key
        self.client_public = (
            struct.pack(">I", len(self._client_x25519_pub_bytes))
            + self._client_x25519_pub_bytes
            + struct.pack(">I", len(self._mlkem_ciphertext))
            + self._mlkem_ciphertext
        )

    def _encrypt_with_shared_key(self, payload_dict: dict) -> bytes:
        """Encrypt a dict payload using the shared key. Returns nonce||ciphertext."""
        plaintext = json.dumps(payload_dict).encode()
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.shared_key).encrypt(nonce, plaintext, None)
        return nonce + ciphertext


def make_encrypted_execute_request(
    payload_dict: dict, ctx: EncryptionTestContext
) -> dict:
    """Build the outer JSON body for POST /execute.

    Returns {"encrypted_payload": "...", "client_public_key": "..."}.
    """
    encrypted = ctx._encrypt_with_shared_key(payload_dict)
    return {
        "encrypted_payload": base64.b64encode(encrypted).decode(),
        "client_public_key": base64.b64encode(ctx.client_public).decode(),
    }


def decrypt_execute_response(response_json: dict, shared_key: bytes) -> dict:
    """Decrypt the encrypted_response field from a /execute 200 response."""
    raw = base64.b64decode(response_json["encrypted_response"])
    nonce, ciphertext = raw[:12], raw[12:]
    plaintext = AESGCM(shared_key).decrypt(nonce, ciphertext, None)
    return json.loads(plaintext)


def make_encrypted_output_request(
    payload_dict: dict, shared_key: bytes
) -> dict:
    """Build the outer JSON body for POST /execution/{id}/output.

    Returns {"encrypted_payload": "..."}.
    """
    plaintext = json.dumps(payload_dict).encode()
    nonce = os.urandom(12)
    ciphertext = AESGCM(shared_key).encrypt(nonce, plaintext, None)
    encrypted = nonce + ciphertext
    return {"encrypted_payload": base64.b64encode(encrypted).decode()}


def decrypt_output_response(response_json: dict, shared_key: bytes) -> dict:
    """Decrypt the encrypted_response field from an /output 200 response."""
    return decrypt_execute_response(response_json, shared_key)
