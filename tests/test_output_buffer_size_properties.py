"""Property-based tests for output buffer size enforcement

Feature: github-actions-remote-executor
Tests Property 146: Output Buffer Size Enforcement from the design document

Validates: Requirements 5.15, 5.16
"""
import pytest
from hypothesis import given, strategies as st, settings
from src.output_collector import OutputCollector


# Strategy that generates (max_size, stdout_data, stderr_data) where combined > max_size
@st.composite
def exceeding_output(draw):
    """Generate output data that is guaranteed to exceed the limit."""
    max_size = draw(st.integers(min_value=10, max_value=1000))
    # Generate data whose combined size exceeds max_size
    # At least max_size + 1 total bytes
    extra = draw(st.integers(min_value=1, max_value=2000))
    total = max_size + extra
    # Split between stdout and stderr
    stdout_len = draw(st.integers(min_value=0, max_value=total))
    stderr_len = total - stdout_len
    stdout_data = draw(st.binary(min_size=stdout_len, max_size=stdout_len))
    stderr_data = draw(st.binary(min_size=stderr_len, max_size=stderr_len))
    return max_size, stdout_data, stderr_data


# Strategy that generates (max_size, stdout_data, stderr_data) where combined <= max_size
@st.composite
def within_limit_output(draw):
    """Generate output data that is guaranteed to be within the limit."""
    max_size = draw(st.integers(min_value=10, max_value=2000))
    total = draw(st.integers(min_value=0, max_value=max_size))
    stdout_len = draw(st.integers(min_value=0, max_value=total))
    stderr_len = total - stdout_len
    stdout_data = draw(st.binary(min_size=stdout_len, max_size=stdout_len))
    stderr_data = draw(st.binary(min_size=stderr_len, max_size=stderr_len))
    return max_size, stdout_data, stderr_data


@given(data=exceeding_output())
@settings(max_examples=50)
def test_property_146_combined_output_never_exceeds_limit(data):
    """
    Property 146: Output Buffer Size Enforcement

    For any script execution whose combined stdout and stderr output exceeds
    MAX_OUTPUT_SIZE_BYTES, the Output_Collector should truncate the output
    and the combined size should never exceed the configured limit.

    **Validates: Requirements 5.15, 5.16**
    """
    max_size, stdout_data, stderr_data = data

    collector = OutputCollector(max_output_size_bytes=max_size)
    collector.create_buffer("test-exec")

    collector.capture_output("test-exec", "stdout", stdout_data)
    collector.capture_output("test-exec", "stderr", stderr_data)

    output = collector.get_output("test-exec")

    combined_size = output.stdout_offset + output.stderr_offset
    assert combined_size <= max_size, (
        f"Combined output {combined_size} exceeds limit {max_size}"
    )
    assert output.truncated is True, (
        "truncated flag must be True when output exceeds limit"
    )


@given(data=within_limit_output())
@settings(max_examples=50)
def test_property_146_output_within_limit_not_truncated(data):
    """
    Property 146 (variant): Output within the limit should NOT be truncated.

    For any script execution whose combined stdout and stderr output is within
    MAX_OUTPUT_SIZE_BYTES, the output should be captured fully and the truncated
    flag should remain False.

    **Validates: Requirements 5.15, 5.16**
    """
    max_size, stdout_data, stderr_data = data

    collector = OutputCollector(max_output_size_bytes=max_size)
    collector.create_buffer("test-exec")

    collector.capture_output("test-exec", "stdout", stdout_data)
    collector.capture_output("test-exec", "stderr", stderr_data)

    output = collector.get_output("test-exec")

    assert output.stdout_offset == len(stdout_data), (
        "stdout should be fully captured when within limit"
    )
    assert output.stderr_offset == len(stderr_data), (
        "stderr should be fully captured when within limit"
    )
    assert output.truncated is False, (
        "truncated flag must be False when output is within limit"
    )


# Strategy for chunks that exceed a limit
@st.composite
def exceeding_chunks(draw):
    """Generate a list of chunks whose total exceeds the limit."""
    max_size = draw(st.integers(min_value=10, max_value=500))
    # Generate enough chunks to exceed the limit
    total_needed = max_size + draw(st.integers(min_value=1, max_value=1000))
    chunks = []
    remaining = total_needed
    while remaining > 0:
        chunk_size = draw(st.integers(min_value=1, max_value=min(remaining, 500)))
        chunks.append(draw(st.binary(min_size=chunk_size, max_size=chunk_size)))
        remaining -= chunk_size
    stream = draw(st.sampled_from(["stdout", "stderr"]))
    return max_size, chunks, stream


@given(data=exceeding_chunks())
@settings(max_examples=50)
def test_property_146_truncated_flag_set_on_overflow(data):
    """
    Property 146 (variant): The truncated flag is set when any write causes overflow.

    For any sequence of output writes that collectively exceed the limit,
    the truncated flag should be set and combined size should not exceed the limit.

    **Validates: Requirements 5.15, 5.16**
    """
    max_size, chunks, stream = data

    collector = OutputCollector(max_output_size_bytes=max_size)
    collector.create_buffer("test-exec")

    for chunk in chunks:
        collector.capture_output("test-exec", stream, chunk)

    output = collector.get_output("test-exec")

    combined_size = output.stdout_offset + output.stderr_offset
    assert combined_size <= max_size, (
        f"Combined output {combined_size} exceeds limit {max_size}"
    )
    assert output.truncated is True, (
        "truncated flag must be True after overflow"
    )
