"""Anti-replay nonce cache for encrypted request protection.

Tracks recently seen nonces from encrypted requests to prevent replay attacks.
Thread-safe for concurrent access.
"""
import time
from threading import Lock
from typing import Dict


class NonceCache:
    """Thread-safe nonce cache with configurable TTL for anti-replay protection.

    Tracks recently seen nonces and rejects duplicates within the TTL window.
    Entries expire after the configured TTL (matching OIDC token lifetime).
    """

    def __init__(self, ttl_seconds: int):
        """
        Initialize the nonce cache.

        Args:
            ttl_seconds: Time-to-live in seconds for nonce entries.
                         Must be >= 1.
        """
        if ttl_seconds < 1:
            raise ValueError(f"ttl_seconds must be >= 1, got {ttl_seconds}")
        self._ttl_seconds = ttl_seconds
        self._cache: Dict[str, float] = {}  # nonce -> timestamp
        self._lock = Lock()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def check_and_store(self, nonce: str) -> bool:
        """Check if a nonce is new and store it.

        Args:
            nonce: The nonce string to check.

        Returns:
            True if the nonce is new (not seen before or expired),
            False if the nonce is a duplicate (replay detected).
        """
        now = time.monotonic()
        with self._lock:
            # Cleanup expired entries opportunistically
            self._cleanup_expired_locked(now)

            if nonce in self._cache:
                return False  # Duplicate nonce

            self._cache[nonce] = now
            return True

    def cleanup_expired(self) -> int:
        """Remove expired nonce entries.

        Returns:
            Number of entries removed.
        """
        now = time.monotonic()
        with self._lock:
            return self._cleanup_expired_locked(now)

    def _cleanup_expired_locked(self, now: float) -> int:
        """Remove expired entries (must be called with lock held).

        Args:
            now: Current monotonic time.

        Returns:
            Number of entries removed.
        """
        cutoff = now - self._ttl_seconds
        expired = [nonce for nonce, ts in self._cache.items() if ts <= cutoff]
        for nonce in expired:
            del self._cache[nonce]
        return len(expired)

    def __len__(self) -> int:
        """Return the number of entries currently in the cache."""
        with self._lock:
            return len(self._cache)
