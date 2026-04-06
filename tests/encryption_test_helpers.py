"""Encryption test helpers for HPKE-based request/response encryption.

Provides utilities to create encrypted requests and decrypt responses
for testing the encrypted /execute and /execution/{id}/output endpoints.
"""
import base64
import json
import os

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.encryption import EncryptionManager


class EncryptionTestContext:
    """Holds an EncryptionManager and a client keypair for test use."""

    def __init__(self):
        self.encryption_manager = EncryptionManager()
        self._client_private = X25519PrivateKey.generate()
        self.client_public = self._client_private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        # Derive shared key (same derivation the server does)
        shared_secret = self._client_private.exchange(
            self.encryption_manager._public_key
        )
        self.shared_key = HKDF(
            algorithm=SHA256(), length=32, salt=None, info=b"hpke-shared-key"
        ).derive(shared_secret)

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
