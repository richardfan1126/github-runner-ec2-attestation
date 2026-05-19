"""Output attestation rate limiter for GitHub Actions Remote Executor.

Prevents frequent polling from turning TPM attestation into a resource-exhaustion
path by limiting the number of attestation generations per execution within a
sliding time window.

Requirements: 55.1, 55.2, 55.3, 55.4, 55.5, 55.6, 55.7, 55.8
"""
import time
from threading import Lock
from typing import Dict, List


class OutputAttestationRateLimiter:
    """Rate limiter for output attestation generation per execution_id.

    Uses a sliding window approach: each execution_id has a budget of
    `max_per_window` attestation generations within `window_seconds`.
    Expired timestamps are pruned on each check.
    """

    def __init__(self, max_per_window: int, window_seconds: int) -> None:
        """
        Initialize the output attestation rate limiter.

        Args:
            max_per_window: Maximum attestation generations allowed per
                execution within the time window.
            window_seconds: Duration of the sliding window in seconds.
        """
        self._max_per_window = max_per_window
        self._window_seconds = window_seconds
        self._records: Dict[str, List[float]] = {}
        self._lock = Lock()

    @property
    def max_per_window(self) -> int:
        """Maximum attestations allowed per execution within the window."""
        return self._max_per_window

    @property
    def window_seconds(self) -> int:
        """Duration of the sliding window in seconds."""
        return self._window_seconds

    def check_and_record(self, execution_id: str) -> bool:
        """Check if an attestation is allowed and record it if so.

        Returns True if the attestation is within budget (allowed),
        False if the budget is exhausted for this execution_id within
        the current window.

        Thread-safe: uses a lock to protect the internal state.

        Args:
            execution_id: The execution identifier to check.

        Returns:
            True if attestation is allowed, False if rate-limited.
        """
        now = time.time()
        cutoff = now - self._window_seconds

        with self._lock:
            # Get or create the timestamp list for this execution_id
            timestamps = self._records.get(execution_id, [])

            # Prune expired timestamps
            timestamps = [t for t in timestamps if t > cutoff]

            # Check if within budget
            if len(timestamps) >= self._max_per_window:
                # Update pruned list even when rejecting
                self._records[execution_id] = timestamps
                return False

            # Record this attestation
            timestamps.append(now)
            self._records[execution_id] = timestamps
            return True
