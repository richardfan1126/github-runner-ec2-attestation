"""Unit tests for output buffer size limits

Feature: github-actions-remote-executor
Tests output buffer size enforcement including:
- Output within limit (not truncated)
- Output exceeding limit (truncated, flag set)
- Exact boundary condition
- Truncation for both stdout and stderr
- Further writes ignored after truncation

Requirements: 5.15, 5.16
"""
import pytest
from src.output_collector import OutputCollector


class TestOutputWithinLimit:
    """Test that output within the limit is captured fully"""

    def test_small_output_not_truncated(self):
        collector = OutputCollector(max_output_size_bytes=1000)
        collector.create_buffer("exec-1")

        collector.capture_output("exec-1", "stdout", b"hello")
        collector.capture_output("exec-1", "stderr", b"world")

        output = collector.get_output("exec-1")
        assert output.stdout == "hello"
        assert output.stderr == "world"
        assert output.truncated is False

    def test_empty_output_not_truncated(self):
        collector = OutputCollector(max_output_size_bytes=100)
        collector.create_buffer("exec-1")

        output = collector.get_output("exec-1")
        assert output.stdout == ""
        assert output.stderr == ""
        assert output.truncated is False


class TestOutputExceedingLimit:
    """Test that output exceeding the limit is truncated"""

    def test_stdout_exceeds_limit(self):
        collector = OutputCollector(max_output_size_bytes=10)
        collector.create_buffer("exec-1")

        collector.capture_output("exec-1", "stdout", b"A" * 20)

        output = collector.get_output("exec-1")
        assert output.stdout_offset == 10
        assert output.truncated is True

    def test_stderr_exceeds_limit(self):
        collector = OutputCollector(max_output_size_bytes=10)
        collector.create_buffer("exec-1")

        collector.capture_output("exec-1", "stderr", b"E" * 20)

        output = collector.get_output("exec-1")
        assert output.stderr_offset == 10
        assert output.truncated is True

    def test_combined_stdout_stderr_exceeds_limit(self):
        collector = OutputCollector(max_output_size_bytes=15)
        collector.create_buffer("exec-1")

        collector.capture_output("exec-1", "stdout", b"A" * 10)
        collector.capture_output("exec-1", "stderr", b"E" * 10)

        output = collector.get_output("exec-1")
        combined = output.stdout_offset + output.stderr_offset
        assert combined <= 15
        assert output.truncated is True


class TestExactBoundary:
    """Test exact boundary condition"""

    def test_output_exactly_at_limit(self):
        collector = OutputCollector(max_output_size_bytes=20)
        collector.create_buffer("exec-1")

        collector.capture_output("exec-1", "stdout", b"A" * 10)
        collector.capture_output("exec-1", "stderr", b"E" * 10)

        output = collector.get_output("exec-1")
        assert output.stdout_offset == 10
        assert output.stderr_offset == 10
        assert output.truncated is False

    def test_one_byte_over_limit(self):
        collector = OutputCollector(max_output_size_bytes=20)
        collector.create_buffer("exec-1")

        collector.capture_output("exec-1", "stdout", b"A" * 10)
        collector.capture_output("exec-1", "stderr", b"E" * 11)

        output = collector.get_output("exec-1")
        combined = output.stdout_offset + output.stderr_offset
        assert combined <= 20
        assert output.truncated is True


class TestFurtherWritesIgnored:
    """Test that once truncated, further writes are ignored"""

    def test_writes_after_truncation_ignored(self):
        collector = OutputCollector(max_output_size_bytes=10)
        collector.create_buffer("exec-1")

        collector.capture_output("exec-1", "stdout", b"A" * 10)
        # Buffer is now full

        # These should be silently ignored
        collector.capture_output("exec-1", "stdout", b"B" * 5)
        collector.capture_output("exec-1", "stderr", b"C" * 5)

        output = collector.get_output("exec-1")
        assert output.stdout_offset == 10
        assert output.stderr_offset == 0
        assert "B" not in output.stdout
        assert output.stderr == ""

    def test_truncation_then_more_writes(self):
        collector = OutputCollector(max_output_size_bytes=5)
        collector.create_buffer("exec-1")

        # First write exceeds limit, gets truncated
        collector.capture_output("exec-1", "stdout", b"ABCDEFGHIJ")

        output_before = collector.get_output("exec-1")
        assert output_before.stdout_offset == 5
        assert output_before.truncated is True

        # Further writes should be ignored
        collector.capture_output("exec-1", "stderr", b"XYZ")

        output_after = collector.get_output("exec-1")
        assert output_after.stdout_offset == 5
        assert output_after.stderr_offset == 0
        assert output_after.truncated is True


class TestTruncationBothStreams:
    """Test truncation works correctly for both stdout and stderr"""

    def test_stdout_fills_then_stderr_truncated(self):
        collector = OutputCollector(max_output_size_bytes=10)
        collector.create_buffer("exec-1")

        collector.capture_output("exec-1", "stdout", b"A" * 8)
        collector.capture_output("exec-1", "stderr", b"E" * 5)

        output = collector.get_output("exec-1")
        assert output.stdout_offset == 8
        assert output.stderr_offset == 2  # only 2 bytes remaining
        assert output.truncated is True

    def test_stderr_fills_then_stdout_truncated(self):
        collector = OutputCollector(max_output_size_bytes=10)
        collector.create_buffer("exec-1")

        collector.capture_output("exec-1", "stderr", b"E" * 8)
        collector.capture_output("exec-1", "stdout", b"A" * 5)

        output = collector.get_output("exec-1")
        assert output.stderr_offset == 8
        assert output.stdout_offset == 2  # only 2 bytes remaining
        assert output.truncated is True
