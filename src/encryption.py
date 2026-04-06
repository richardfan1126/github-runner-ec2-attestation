"""Encryption manager for HPKE-based request/response encryption.

Uses X25519 for key agreement, HKDF-SHA256 for key derivation,
and AES-256-GCM for symmetric encryption.
"""
import json
import logging
import os
import threading
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

logger = logging.getLogger(__name__)

# Constants
_AES_KEY_LENGTH = 32  # 256-bit AES key
_NONCE_LENGTH = 12  # 96-bit nonce for AES-GCM
_HKDF_INFO = b"hpke-shared-key"


class EncryptionManager:
    """Manages HPKE-based encryption for request/response payloads.

    Generates an X25519 keypair at initialization (held in memory only),
    derives shared keys via ECDH + HKDF, and encrypts/decrypts payloads
    using AES-256-GCM.
    """

    def __init__(self) -> None:
        """Generate Server_Keypair at initialization and hold in memory."""
        self._private_key = X25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()
        self._serialized_public_key = self._public_key.public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self._contexts: dict[str, bytes] = {}
        self._lock = threading.Lock()
        logger.info("Server keypair generated for HPKE key exchange")

    @property
    def server_public_key(self) -> bytes:
        """Return the serialized Server_Public_Key (32 bytes, raw X25519)."""
        return self._serialized_public_key

    # ------------------------------------------------------------------
    # Key derivation helpers
    # ------------------------------------------------------------------

    def _derive_shared_key(self, peer_public_key: X25519PublicKey) -> bytes:
        """Derive a 256-bit AES key from ECDH shared secret via HKDF-SHA256."""
        shared_secret = self._private_key.exchange(peer_public_key)
        return HKDF(
            algorithm=SHA256(),
            length=_AES_KEY_LENGTH,
            salt=None,
            info=_HKDF_INFO,
        ).derive(shared_secret)

    @staticmethod
    def _encrypt(payload_bytes: bytes, key: bytes) -> bytes:
        """Encrypt *payload_bytes* with AES-256-GCM.

        Returns ``nonce || ciphertext`` (12 + len(payload) + 16 bytes).
        """
        nonce = os.urandom(_NONCE_LENGTH)
        ciphertext = AESGCM(key).encrypt(nonce, payload_bytes, None)
        return nonce + ciphertext

    @staticmethod
    def _decrypt(data: bytes, key: bytes) -> bytes:
        """Decrypt ``nonce || ciphertext`` produced by :meth:`_encrypt`.

        Raises :class:`ValueError` on any decryption failure.
        """
        if len(data) <= _NONCE_LENGTH:
            raise ValueError("Encrypted payload too short")
        nonce = data[:_NONCE_LENGTH]
        ciphertext = data[_NONCE_LENGTH:]
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise ValueError(f"Decryption failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Public API – request / response encryption
    # ------------------------------------------------------------------

    def decrypt_request(
        self, encrypted_payload: bytes, client_public_key: bytes
    ) -> tuple[dict, bytes]:
        """Derive Shared_Key via HPKE and decrypt the request payload.

        Args:
            encrypted_payload: ``nonce || ciphertext`` bytes.
            client_public_key: Raw 32-byte X25519 public key from the client.

        Returns:
            Tuple of ``(decrypted_dict, shared_key_bytes)``.

        Raises:
            ValueError: On decryption or deserialisation failure.
        """
        try:
            peer_key = X25519PublicKey.from_public_bytes(client_public_key)
        except Exception as exc:
            raise ValueError(f"Invalid client public key: {exc}") from exc

        shared_key = self._derive_shared_key(peer_key)
        plaintext = self._decrypt(encrypted_payload, shared_key)

        try:
            payload_dict = json.loads(plaintext)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Decrypted payload is not valid JSON: {exc}") from exc

        return payload_dict, shared_key

    def encrypt_response(self, payload: dict, shared_key: bytes) -> bytes:
        """Encrypt a response payload dict using the given Shared_Key.

        Returns ``nonce || ciphertext`` bytes.
        """
        plaintext = json.dumps(payload).encode("utf-8")
        return self._encrypt(plaintext, shared_key)

    def decrypt_with_shared_key(
        self, encrypted_payload: bytes, shared_key: bytes
    ) -> dict:
        """Decrypt a request payload using a previously stored Shared_Key.

        Used for ``/execution/{id}/output`` requests.

        Raises:
            ValueError: On decryption or deserialisation failure.
        """
        plaintext = self._decrypt(encrypted_payload, shared_key)
        try:
            return json.loads(plaintext)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Decrypted payload is not valid JSON: {exc}") from exc

    # ------------------------------------------------------------------
    # Encryption context management (thread-safe)
    # ------------------------------------------------------------------

    def store_encryption_context(self, execution_id: str, shared_key: bytes) -> None:
        """Store Shared_Key in Encryption_Context keyed by *execution_id*."""
        with self._lock:
            self._contexts[execution_id] = shared_key

    def get_shared_key(self, execution_id: str) -> Optional[bytes]:
        """Retrieve Shared_Key for *execution_id*, or ``None`` if not found."""
        with self._lock:
            return self._contexts.get(execution_id)

    def remove_encryption_context(self, execution_id: str) -> None:
        """Remove Encryption_Context when execution is cleaned up."""
        with self._lock:
            self._contexts.pop(execution_id, None)
