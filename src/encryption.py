"""Encryption manager for PQ Hybrid KEM (X25519 + ML-KEM-768) request/response encryption.

Uses X25519 ECDH + ML-KEM-768 for hybrid key agreement, HKDF-SHA256 for key
derivation, and AES-256-GCM for symmetric encryption.  The hybrid approach
combines a classical (X25519) and post-quantum (ML-KEM-768, FIPS 203) KEM so
that the system remains secure even if one component is broken.
"""
import hashlib
import json
import logging
import os
import struct
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
from wolfcrypt.ciphers import MlKemPrivate, MlKemPublic, MlKemType

logger = logging.getLogger(__name__)

# Constants
_AES_KEY_LENGTH = 32  # 256-bit AES key
_NONCE_LENGTH = 12  # 96-bit nonce for AES-GCM
_HKDF_INFO = b"pq-hybrid-shared-key"
_X25519_KEY_LENGTH = 32
_MLKEM768_ENCAP_KEY_LENGTH = 1184
_MLKEM768_CIPHERTEXT_LENGTH = 1088


class EncryptionManager:
    """Manages PQ Hybrid KEM encryption for request/response payloads.

    Generates a composite Server_Keypair (X25519 + ML-KEM-768) at
    initialization (held in memory only), derives shared keys via hybrid
    KEM + HKDF, and encrypts/decrypts payloads using AES-256-GCM.
    """

    def __init__(self) -> None:
        """Generate composite Server_Keypair at initialization and hold in memory."""
        # X25519 keypair
        self._x25519_private_key = X25519PrivateKey.generate()
        self._x25519_public_key = self._x25519_private_key.public_key()
        self._x25519_public_bytes = self._x25519_public_key.public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )

        # ML-KEM-768 keypair
        self._mlkem_private_key = MlKemPrivate.make_key(MlKemType.ML_KEM_768)
        self._mlkem_encap_key_bytes = self._mlkem_private_key.encode_pub_key()

        # Serialize composite Server_Public_Key: length-prefixed concatenation
        self._serialized_public_key = (
            struct.pack(">I", len(self._x25519_public_bytes))
            + self._x25519_public_bytes
            + struct.pack(">I", len(self._mlkem_encap_key_bytes))
            + self._mlkem_encap_key_bytes
        )

        # Pre-compute fingerprint
        self._public_key_fingerprint = hashlib.sha256(
            self._serialized_public_key
        ).digest()

        self._contexts: dict[str, bytes] = {}
        self._lock = threading.Lock()
        logger.info(
            "Server composite keypair generated (X25519 + ML-KEM-768) for PQ hybrid key exchange"
        )

    @property
    def server_public_key(self) -> bytes:
        """Return the serialized composite Server_Public_Key.

        Format: 4-byte big-endian length + X25519 pubkey (32 bytes)
              + 4-byte big-endian length + ML-KEM-768 encapsulation key (1184 bytes).
        """
        return self._serialized_public_key

    @property
    def server_public_key_fingerprint(self) -> bytes:
        """Return the SHA-256 fingerprint of the serialized Server_Public_Key.

        Used in the attestation document's public_key field because the
        composite key (1224 bytes) exceeds the 1024-byte field limit.
        """
        return self._public_key_fingerprint

    # ------------------------------------------------------------------
    # Key derivation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_length_prefixed(data: bytes) -> list[bytes]:
        """Parse a sequence of length-prefixed components from *data*.

        Each component is preceded by a 4-byte big-endian length.
        Returns a list of the extracted byte strings.
        """
        components: list[bytes] = []
        offset = 0
        while offset < len(data):
            if offset + 4 > len(data):
                raise ValueError("Truncated length prefix")
            (length,) = struct.unpack(">I", data[offset : offset + 4])
            offset += 4
            if offset + length > len(data):
                raise ValueError(
                    f"Component length {length} exceeds remaining data "
                    f"({len(data) - offset} bytes)"
                )
            components.append(data[offset : offset + length])
            offset += length
        return components

    def _derive_hybrid_shared_key(
        self, x25519_shared_secret: bytes, mlkem_shared_secret: bytes
    ) -> bytes:
        """Derive a 256-bit AES key by combining both shared secrets via HKDF-SHA256."""
        combined = x25519_shared_secret + mlkem_shared_secret
        return HKDF(
            algorithm=SHA256(),
            length=_AES_KEY_LENGTH,
            salt=None,
            info=_HKDF_INFO,
        ).derive(combined)

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
        """Derive Shared_Key via PQ Hybrid KEM and decrypt the request payload.

        Args:
            encrypted_payload: ``nonce || ciphertext`` bytes.
            client_public_key: Length-prefixed concatenation of the client's
                X25519 public key and ML-KEM-768 ciphertext.

        Returns:
            Tuple of ``(decrypted_dict, shared_key_bytes)``.

        Raises:
            ValueError: On decryption or deserialisation failure.
        """
        # Parse client_public_key into X25519 pubkey + ML-KEM-768 ciphertext
        try:
            components = self._parse_length_prefixed(client_public_key)
        except ValueError as exc:
            raise ValueError(f"Invalid client public key format: {exc}") from exc

        if len(components) != 2:
            raise ValueError(
                f"Expected 2 components in client public key, got {len(components)}"
            )

        client_x25519_bytes, client_mlkem_ct = components

        # X25519 ECDH
        try:
            peer_x25519_key = X25519PublicKey.from_public_bytes(client_x25519_bytes)
        except Exception as exc:
            raise ValueError(f"Invalid client X25519 public key: {exc}") from exc

        x25519_shared_secret = self._x25519_private_key.exchange(peer_x25519_key)

        # ML-KEM-768 decapsulation
        try:
            mlkem_shared_secret = self._mlkem_private_key.decapsulate(client_mlkem_ct)
        except Exception as exc:
            raise ValueError(f"ML-KEM-768 decapsulation failed: {exc}") from exc

        # Combine via HKDF
        shared_key = self._derive_hybrid_shared_key(
            x25519_shared_secret, mlkem_shared_secret
        )

        # Decrypt payload
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
