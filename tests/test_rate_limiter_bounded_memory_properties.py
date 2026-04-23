"""Property-based tests for RateLimiter bounded memory (Property 173).

Feature: github-actions-remote-executor
Validates: Requirements 8.5

Property 173: RateLimiter Bounded Memory
  For any set of source IPs that each make exactly one request and then never
  make another, after the rate-limit window expires and cleanup_stale_ips() is
  called, the _requests dict should contain no entries for those IPs.
"""
import time
from unittest.mock import patch

import pytest
from hypothesis import given, settings, strategies as st

from src.server import RateLimiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unique_ips(n: int) -> list[str]:
    """Generate n distinct fake IP addresses."""
    return [f"10.0.{i // 256}.{i % 256}" for i in range(n)]


# ---------------------------------------------------------------------------
# Property 173: RateLimiter Bounded Memory
# ---------------------------------------------------------------------------

class TestRateLimiterBoundedMemoryProperty:
    """Property 173: After the window expires, stale IPs are evicted."""

    @given(
        n_ips=st.integers(min_value=1, max_value=50),
        window_seconds=st.integers(min_value=1, max_value=10),
        max_requests=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=40, deadline=5000)
    def test_stale_ips_removed_after_window_expires(
        self, n_ips: int, window_seconds: int, max_requests: int
    ):
        """
        Property 173: RateLimiter Bounded Memory

        For any set of source IPs that each make exactly one request and then
        never make another, after the rate-limit window expires and
        cleanup_stale_ips() is called, the _requests dict should contain no
        entries for those IPs.
        """
        rate_limiter = RateLimiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
        )
        ips = _unique_ips(n_ips)

        # Simulate each IP making exactly one request at a fixed "past" time
        # that is already outside the current window.
        past_time = time.time() - window_seconds - 1.0

        with rate_limiter._lock:
            for ip in ips:
                rate_limiter._requests[ip] = [past_time]

        # All IPs should be present before cleanup
        assert len(rate_limiter._requests) == n_ips

        # After cleanup, all stale IPs should be removed
        removed = rate_limiter.cleanup_stale_ips()

        assert removed == n_ips, (
            f"Expected {n_ips} stale IPs to be removed, got {removed}"
        )
        for ip in ips:
            assert ip not in rate_limiter._requests, (
                f"IP {ip} should have been evicted but is still present"
            )

    @given(
        n_stale=st.integers(min_value=1, max_value=30),
        n_active=st.integers(min_value=1, max_value=30),
        window_seconds=st.integers(min_value=5, max_value=30),
        max_requests=st.integers(min_value=5, max_value=100),
    )
    @settings(max_examples=30, deadline=5000)
    def test_active_ips_retained_stale_ips_removed(
        self,
        n_stale: int,
        n_active: int,
        window_seconds: int,
        max_requests: int,
    ):
        """
        Property 173 (mixed): IPs with recent requests are retained; IPs with
        only expired timestamps are evicted.
        """
        rate_limiter = RateLimiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
        )

        stale_ips = _unique_ips(n_stale)
        active_ips = _unique_ips(n_active + n_stale)[n_stale:]  # non-overlapping

        past_time = time.time() - window_seconds - 1.0
        recent_time = time.time() - 1.0  # well within the window

        with rate_limiter._lock:
            for ip in stale_ips:
                rate_limiter._requests[ip] = [past_time]
            for ip in active_ips:
                rate_limiter._requests[ip] = [recent_time]

        removed = rate_limiter.cleanup_stale_ips()

        # All stale IPs must be gone
        assert removed == n_stale
        for ip in stale_ips:
            assert ip not in rate_limiter._requests

        # All active IPs must still be present
        for ip in active_ips:
            assert ip in rate_limiter._requests
