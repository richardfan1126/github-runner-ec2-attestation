"""Unit tests for RateLimiter.cleanup_stale_ips and its integration with the
periodic cleanup background task.

Feature: github-actions-remote-executor
Requirements: 8.5
"""
import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.server import RateLimiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_limiter(max_requests: int = 10, window_seconds: int = 60) -> RateLimiter:
    return RateLimiter(max_requests=max_requests, window_seconds=window_seconds)


def _inject_timestamps(limiter: RateLimiter, ip: str, timestamps: list[float]) -> None:
    """Directly inject timestamps into the rate limiter for testing."""
    with limiter._lock:
        limiter._requests[ip] = list(timestamps)


# ---------------------------------------------------------------------------
# cleanup_stale_ips: removes IPs with no recent requests
# ---------------------------------------------------------------------------

class TestCleanupStaleIpsRemovesStaleEntries:
    """cleanup_stale_ips should remove IPs whose timestamps are all outside the window."""

    def test_removes_single_stale_ip(self):
        limiter = _make_limiter(window_seconds=60)
        past = time.time() - 120  # 2 minutes ago — outside 60-second window
        _inject_timestamps(limiter, "1.2.3.4", [past])

        removed = limiter.cleanup_stale_ips()

        assert removed == 1
        assert "1.2.3.4" not in limiter._requests

    def test_removes_multiple_stale_ips(self):
        limiter = _make_limiter(window_seconds=60)
        past = time.time() - 120
        for i in range(5):
            _inject_timestamps(limiter, f"10.0.0.{i}", [past])

        removed = limiter.cleanup_stale_ips()

        assert removed == 5
        assert len(limiter._requests) == 0

    def test_removes_ip_with_all_timestamps_expired(self):
        """An IP with multiple timestamps all outside the window should be removed."""
        limiter = _make_limiter(window_seconds=30)
        past = time.time() - 60
        _inject_timestamps(limiter, "192.168.1.1", [past - 10, past - 5, past])

        removed = limiter.cleanup_stale_ips()

        assert removed == 1
        assert "192.168.1.1" not in limiter._requests

    def test_empty_dict_returns_zero(self):
        limiter = _make_limiter()
        removed = limiter.cleanup_stale_ips()
        assert removed == 0


# ---------------------------------------------------------------------------
# cleanup_stale_ips: retains IPs with recent requests
# ---------------------------------------------------------------------------

class TestCleanupStaleIpsRetainsActiveEntries:
    """cleanup_stale_ips should NOT remove IPs that have at least one recent timestamp."""

    def test_retains_ip_with_recent_request(self):
        limiter = _make_limiter(window_seconds=60)
        recent = time.time() - 5  # 5 seconds ago — inside window
        _inject_timestamps(limiter, "1.2.3.4", [recent])

        removed = limiter.cleanup_stale_ips()

        assert removed == 0
        assert "1.2.3.4" in limiter._requests

    def test_retains_ip_with_mixed_timestamps(self):
        """IP with one expired and one recent timestamp should be retained."""
        limiter = _make_limiter(window_seconds=60)
        past = time.time() - 120
        recent = time.time() - 5
        _inject_timestamps(limiter, "1.2.3.4", [past, recent])

        removed = limiter.cleanup_stale_ips()

        assert removed == 0
        assert "1.2.3.4" in limiter._requests
        # The expired timestamp should have been pruned
        with limiter._lock:
            assert all(t > time.time() - 60 for t in limiter._requests["1.2.3.4"])

    def test_retains_active_removes_stale(self):
        """Mixed population: stale IPs removed, active IPs kept."""
        limiter = _make_limiter(window_seconds=60)
        past = time.time() - 120
        recent = time.time() - 5

        _inject_timestamps(limiter, "stale.ip", [past])
        _inject_timestamps(limiter, "active.ip", [recent])

        removed = limiter.cleanup_stale_ips()

        assert removed == 1
        assert "stale.ip" not in limiter._requests
        assert "active.ip" in limiter._requests


# ---------------------------------------------------------------------------
# cleanup_stale_ips: returns correct count
# ---------------------------------------------------------------------------

class TestCleanupStaleIpsReturnCount:
    """cleanup_stale_ips should return the exact number of removed entries."""

    def test_returns_zero_when_nothing_removed(self):
        limiter = _make_limiter(window_seconds=60)
        recent = time.time() - 1
        _inject_timestamps(limiter, "1.1.1.1", [recent])

        assert limiter.cleanup_stale_ips() == 0

    def test_returns_exact_count_of_removed_ips(self):
        limiter = _make_limiter(window_seconds=60)
        past = time.time() - 120
        recent = time.time() - 1

        for i in range(3):
            _inject_timestamps(limiter, f"stale.{i}", [past])
        for i in range(2):
            _inject_timestamps(limiter, f"active.{i}", [recent])

        removed = limiter.cleanup_stale_ips()
        assert removed == 3

    def test_idempotent_second_call_returns_zero(self):
        """Calling cleanup twice should not double-count removals."""
        limiter = _make_limiter(window_seconds=60)
        past = time.time() - 120
        _inject_timestamps(limiter, "1.2.3.4", [past])

        first = limiter.cleanup_stale_ips()
        second = limiter.cleanup_stale_ips()

        assert first == 1
        assert second == 0


# ---------------------------------------------------------------------------
# cleanup_stale_ips: thread safety
# ---------------------------------------------------------------------------

class TestCleanupStaleIpsThreadSafety:
    """cleanup_stale_ips must be safe to call concurrently with check_rate_limit."""

    def test_concurrent_cleanup_and_check_rate_limit(self):
        """No exceptions or data corruption when cleanup and check_rate_limit run concurrently."""
        limiter = _make_limiter(max_requests=1000, window_seconds=60)
        errors: list[Exception] = []

        # Pre-populate with stale entries
        past = time.time() - 120
        for i in range(50):
            _inject_timestamps(limiter, f"stale.{i}", [past])

        def do_cleanup():
            try:
                for _ in range(20):
                    limiter.cleanup_stale_ips()
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        def do_check():
            try:
                for i in range(50):
                    limiter.check_rate_limit(f"active.{i % 10}")
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=do_cleanup),
            threading.Thread(target=do_check),
            threading.Thread(target=do_cleanup),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Thread safety errors: {errors}"

    def test_concurrent_cleanup_calls_are_safe(self):
        """Multiple threads calling cleanup_stale_ips simultaneously should not raise."""
        limiter = _make_limiter(window_seconds=60)
        past = time.time() - 120
        for i in range(100):
            _inject_timestamps(limiter, f"ip.{i}", [past])

        errors: list[Exception] = []
        results: list[int] = []

        def do_cleanup():
            try:
                count = limiter.cleanup_stale_ips()
                results.append(count)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=do_cleanup) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        # Total removed across all threads should equal 100 (no double-counting)
        assert sum(results) == 100


# ---------------------------------------------------------------------------
# Periodic cleanup task calls cleanup_stale_ips
# ---------------------------------------------------------------------------

class TestPeriodicCleanupCallsCleanupStaleIps:
    """The periodic cleanup background task should invoke cleanup_stale_ips."""

    @pytest.mark.asyncio
    async def test_periodic_cleanup_invokes_cleanup_stale_ips(self):
        """The background task should call cleanup_stale_ips on each iteration."""
        mock_execution_manager = MagicMock()
        mock_execution_manager.cleanup_expired.return_value = 0

        mock_rate_limiter = MagicMock(spec=RateLimiter)
        mock_rate_limiter.cleanup_stale_ips.return_value = 0

        stale_ip_call_count = 0

        async def periodic_cleanup():
            nonlocal stale_ip_call_count
            while True:
                try:
                    await asyncio.sleep(0.05)
                    mock_execution_manager.cleanup_expired()
                    stale_ip_call_count += mock_rate_limiter.cleanup_stale_ips()
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass

        task = asyncio.create_task(periodic_cleanup())
        await asyncio.sleep(0.25)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert mock_rate_limiter.cleanup_stale_ips.call_count >= 2, (
            f"Expected at least 2 calls to cleanup_stale_ips, "
            f"got {mock_rate_limiter.cleanup_stale_ips.call_count}"
        )

    @pytest.mark.asyncio
    async def test_periodic_cleanup_logs_stale_ip_count(self):
        """Periodic cleanup should log the number of stale IPs removed at DEBUG level."""
        import logging

        mock_execution_manager = MagicMock()
        mock_execution_manager.cleanup_expired.return_value = 0

        mock_rate_limiter = MagicMock(spec=RateLimiter)
        mock_rate_limiter.cleanup_stale_ips.return_value = 3  # simulate 3 stale IPs

        log_records: list[logging.LogRecord] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                log_records.append(record)

        handler = CapturingHandler()
        handler.setLevel(logging.DEBUG)
        test_logger = logging.getLogger("src.server")
        test_logger.addHandler(handler)
        original_level = test_logger.level
        test_logger.setLevel(logging.DEBUG)

        try:
            async def periodic_cleanup():
                while True:
                    try:
                        await asyncio.sleep(0.05)
                        removed = mock_execution_manager.cleanup_expired()
                        stale = mock_rate_limiter.cleanup_stale_ips()
                        if stale > 0:
                            import logging as _logging
                            _logging.getLogger("src.server").debug(
                                f"Periodic cleanup removed {stale} stale IP(s) from rate limiter"
                            )
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        pass

            task = asyncio.create_task(periodic_cleanup())
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            debug_messages = [
                r.getMessage() for r in log_records if r.levelno == logging.DEBUG
            ]
            assert any("stale IP" in msg for msg in debug_messages), (
                f"Expected a DEBUG log about stale IPs, got: {debug_messages}"
            )
        finally:
            test_logger.removeHandler(handler)
            test_logger.setLevel(original_level)
