"""Property-based tests for output collector

Feature: github-actions-remote-executor
Tests Properties 23, 24, 31, 32 from the design document
"""
import pytest
import threading
from hypothesis import given, strategies as st, assume, settings
from src.output_collector import OutputCollector
from src.models import OutputData


# Custom strategies for generating test data
@st.composite
def execution_id_strategy(draw):
    """Generate execution ID strings (UUID format)"""
    # Generate UUID-like strings
    hex_chars = '0123456789abcdef'
    parts = [
        draw(st.text(alphabet=hex_chars, min_size=8, max_size=8)),
        draw(st.text(alphabet=hex_chars, min_size=4, max_size=4)),
        draw(st.text(alphabet=hex_chars, min_size=4, max_size=4)),
        draw(st.text(alphabet=hex_chars, min_size=4, max_size=4)),
        draw(st.text(alphabet=hex_chars, min_size=12, max_size=12)),
    ]
    return '-'.join(parts)


@st.composite
def output_data_strategy(draw):
    """Generate output data (stdout/stderr content)"""
    # Generate various types of output including:
    # - ASCII text
    # - Unicode text
    # - Binary-safe content
    # - Empty strings
    # - Multi-line content
    content_type = draw(st.sampled_from(['ascii', 'unicode', 'multiline', 'empty']))
    
    if content_type == 'empty':
        return b''
    elif content_type == 'ascii':
        text = draw(st.text(
            alphabet=st.characters(min_codepoint=32, max_codepoint=126),
            min_size=0,
            max_size=1000
        ))
        return text.encode('utf-8')
    elif content_type == 'unicode':
        text = draw(st.text(min_size=0, max_size=500))
        return text.encode('utf-8')
    else:  # multiline
        lines = draw(st.lists(
            st.text(min_size=0, max_size=100),
            min_size=1,
            max_size=20
        ))
        text = '\n'.join(lines)
        return text.encode('utf-8')


@st.composite
def stream_name_strategy(draw):
    """Generate valid stream names"""
    return draw(st.sampled_from(['stdout', 'stderr']))


# Property 23: Output Stream Capture
# Feature: github-actions-remote-executor, Property 23: Output Stream Capture
@given(
    execution_id=execution_id_strategy(),
    stdout_data=output_data_strategy(),
    stderr_data=output_data_strategy()
)
@settings(max_examples=20)
def test_property_23_output_stream_capture(execution_id, stdout_data, stderr_data):
    """
    Property 23: For any script execution, both stdout and stderr streams
    should be captured completely.
    
    Validates: Requirements 5.3
    """
    collector = OutputCollector()
    
    # Create buffer for execution
    collector.create_buffer(execution_id)
    
    # Capture stdout data
    if stdout_data:
        collector.capture_output(execution_id, 'stdout', stdout_data)
    
    # Capture stderr data
    if stderr_data:
        collector.capture_output(execution_id, 'stderr', stderr_data)
    
    # Retrieve output
    output = collector.get_output(execution_id)
    
    # Verify stdout was captured completely
    expected_stdout = stdout_data.decode('utf-8', errors='replace')
    assert output.stdout == expected_stdout, \
        "Captured stdout should match input data"
    
    # Verify stderr was captured completely
    expected_stderr = stderr_data.decode('utf-8', errors='replace')
    assert output.stderr == expected_stderr, \
        "Captured stderr should match input data"
    
    # Verify offsets reflect total captured data
    assert output.stdout_offset == len(stdout_data), \
        "stdout_offset should equal total bytes captured"
    assert output.stderr_offset == len(stderr_data), \
        "stderr_offset should equal total bytes captured"


@given(
    execution_id=execution_id_strategy(),
    data_chunks=st.lists(output_data_strategy(), min_size=1, max_size=20),
    stream=stream_name_strategy()
)
def test_property_23_incremental_capture(execution_id, data_chunks, stream):
    """
    Property 23 (variant): Output should be captured incrementally as it arrives.
    
    Validates: Requirements 5.3
    """
    collector = OutputCollector()
    collector.create_buffer(execution_id)
    
    # Capture data in chunks
    total_data = b''
    for chunk in data_chunks:
        if chunk:  # Skip empty chunks
            collector.capture_output(execution_id, stream, chunk)
            total_data += chunk
    
    # Retrieve output
    output = collector.get_output(execution_id)
    
    # Verify all chunks were captured
    expected_text = total_data.decode('utf-8', errors='replace')
    if stream == 'stdout':
        assert output.stdout == expected_text, \
            "Incremental stdout capture should preserve all data"
        assert output.stdout_offset == len(total_data), \
            "stdout_offset should reflect total captured bytes"
    else:
        assert output.stderr == expected_text, \
            "Incremental stderr capture should preserve all data"
        assert output.stderr_offset == len(total_data), \
            "stderr_offset should reflect total captured bytes"


@given(
    execution_id=execution_id_strategy(),
    data=output_data_strategy()
)
def test_property_23_invalid_stream_rejection(execution_id, data):
    """
    Property 23 (variant): Invalid stream names should be rejected.
    
    Validates: Requirements 5.3
    """
    collector = OutputCollector()
    collector.create_buffer(execution_id)
    
    # Try to capture with invalid stream name
    with pytest.raises(ValueError, match="Invalid stream"):
        collector.capture_output(execution_id, 'invalid_stream', data)


# Property 24: Output Storage Round-Trip
# Feature: github-actions-remote-executor, Property 24: Output Storage Round-Trip
@given(
    execution_id=execution_id_strategy(),
    stdout_data=output_data_strategy(),
    stderr_data=output_data_strategy(),
    exit_code=st.integers(min_value=-128, max_value=255)
)
@settings(max_examples=20)
def test_property_24_output_storage_round_trip(execution_id, stdout_data, stderr_data, exit_code):
    """
    Property 24: For any script execution with captured output, storing the output
    by execution ID and then retrieving it should return the same output content.
    
    Validates: Requirements 5.4, 6.3, 6.4
    """
    collector = OutputCollector()
    
    # Create buffer and capture output
    collector.create_buffer(execution_id)
    
    if stdout_data:
        collector.capture_output(execution_id, 'stdout', stdout_data)
    
    if stderr_data:
        collector.capture_output(execution_id, 'stderr', stderr_data)
    
    # Mark as complete
    collector.mark_complete(execution_id, exit_code)
    
    # Retrieve output
    output = collector.get_output(execution_id)
    
    # Verify round-trip: stored data matches retrieved data
    expected_stdout = stdout_data.decode('utf-8', errors='replace')
    expected_stderr = stderr_data.decode('utf-8', errors='replace')
    
    assert output.stdout == expected_stdout, \
        "Retrieved stdout should match stored stdout"
    assert output.stderr == expected_stderr, \
        "Retrieved stderr should match stored stderr"
    assert output.exit_code == exit_code, \
        "Retrieved exit_code should match stored exit_code"
    assert output.complete is True, \
        "Retrieved complete flag should be True"


@given(
    execution_id=execution_id_strategy(),
    data_chunks=st.lists(output_data_strategy(), min_size=1, max_size=10),
    stream=stream_name_strategy()
)
def test_property_24_incremental_round_trip(execution_id, data_chunks, stream):
    """
    Property 24 (variant): Incremental storage and retrieval should preserve data order.
    
    Validates: Requirements 5.4, 6.3, 6.4
    """
    collector = OutputCollector()
    collector.create_buffer(execution_id)
    
    # Store data incrementally
    total_data = b''
    for chunk in data_chunks:
        if chunk:
            collector.capture_output(execution_id, stream, chunk)
            total_data += chunk
    
    # Retrieve and verify
    output = collector.get_output(execution_id)
    expected_text = total_data.decode('utf-8', errors='replace')
    
    if stream == 'stdout':
        assert output.stdout == expected_text, \
            "Incremental stdout round-trip should preserve data and order"
    else:
        assert output.stderr == expected_text, \
            "Incremental stderr round-trip should preserve data and order"


@given(
    execution_id=execution_id_strategy()
)
def test_property_24_nonexistent_execution_error(execution_id):
    """
    Property 24 (variant): Retrieving output for non-existent execution should fail.
    
    Validates: Requirements 6.3, 6.4
    """
    collector = OutputCollector()
    
    # Try to retrieve output without creating buffer
    with pytest.raises(ValueError, match="Execution ID not found"):
        collector.get_output(execution_id)


# Property 31: Output Structure Separation
# Feature: github-actions-remote-executor, Property 31: Output Structure Separation
@given(
    execution_id=execution_id_strategy(),
    stdout_data=output_data_strategy(),
    stderr_data=output_data_strategy()
)
@settings(max_examples=20)
def test_property_31_output_structure_separation(execution_id, stdout_data, stderr_data):
    """
    Property 31: For any output endpoint response, stdout and stderr should be
    in separate, distinguishable fields.
    
    Validates: Requirements 6.5
    """
    collector = OutputCollector()
    collector.create_buffer(execution_id)
    
    # Capture different data to stdout and stderr
    if stdout_data:
        collector.capture_output(execution_id, 'stdout', stdout_data)
    
    if stderr_data:
        collector.capture_output(execution_id, 'stderr', stderr_data)
    
    # Retrieve output
    output = collector.get_output(execution_id)
    
    # Verify stdout and stderr are separate fields
    assert hasattr(output, 'stdout'), "Output should have stdout field"
    assert hasattr(output, 'stderr'), "Output should have stderr field"
    
    # Verify they contain different data
    expected_stdout = stdout_data.decode('utf-8', errors='replace')
    expected_stderr = stderr_data.decode('utf-8', errors='replace')
    
    assert output.stdout == expected_stdout, \
        "stdout field should contain only stdout data"
    assert output.stderr == expected_stderr, \
        "stderr field should contain only stderr data"
    
    # Verify offsets are also separate
    assert hasattr(output, 'stdout_offset'), "Output should have stdout_offset field"
    assert hasattr(output, 'stderr_offset'), "Output should have stderr_offset field"
    assert output.stdout_offset == len(stdout_data), \
        "stdout_offset should track stdout bytes only"
    assert output.stderr_offset == len(stderr_data), \
        "stderr_offset should track stderr bytes only"


@given(
    execution_id=execution_id_strategy(),
    stdout_chunks=st.lists(output_data_strategy(), min_size=1, max_size=10),
    stderr_chunks=st.lists(output_data_strategy(), min_size=1, max_size=10)
)
def test_property_31_interleaved_capture_separation(execution_id, stdout_chunks, stderr_chunks):
    """
    Property 31 (variant): Interleaved stdout/stderr capture should maintain separation.
    
    Validates: Requirements 6.5
    """
    collector = OutputCollector()
    collector.create_buffer(execution_id)
    
    # Interleave stdout and stderr captures
    total_stdout = b''
    total_stderr = b''
    
    max_len = max(len(stdout_chunks), len(stderr_chunks))
    for i in range(max_len):
        if i < len(stdout_chunks) and stdout_chunks[i]:
            collector.capture_output(execution_id, 'stdout', stdout_chunks[i])
            total_stdout += stdout_chunks[i]
        
        if i < len(stderr_chunks) and stderr_chunks[i]:
            collector.capture_output(execution_id, 'stderr', stderr_chunks[i])
            total_stderr += stderr_chunks[i]
    
    # Retrieve and verify separation
    output = collector.get_output(execution_id)
    
    expected_stdout = total_stdout.decode('utf-8', errors='replace')
    expected_stderr = total_stderr.decode('utf-8', errors='replace')
    
    assert output.stdout == expected_stdout, \
        "Interleaved capture should preserve stdout separation"
    assert output.stderr == expected_stderr, \
        "Interleaved capture should preserve stderr separation"


# Property 32: Offset-Based Output Retrieval
# Feature: github-actions-remote-executor, Property 32: Offset-Based Output Retrieval
@given(
    execution_id=execution_id_strategy(),
    stdout_data=output_data_strategy(),
    stderr_data=output_data_strategy()
)
@settings(max_examples=20)
def test_property_32_offset_based_retrieval(execution_id, stdout_data, stderr_data):
    """
    Property 32: For any execution with captured output and a specified offset,
    the output endpoint should return only the output from that offset onward.
    
    Validates: Requirements 6.6
    """
    assume(len(stdout_data) > 0 or len(stderr_data) > 0)
    
    collector = OutputCollector()
    collector.create_buffer(execution_id)
    
    # Capture output
    if stdout_data:
        collector.capture_output(execution_id, 'stdout', stdout_data)
    
    if stderr_data:
        collector.capture_output(execution_id, 'stderr', stderr_data)
    
    # Test various offsets
    test_offsets = [0, len(stdout_data) // 2, len(stdout_data)]
    
    for offset in test_offsets:
        if offset < 0:
            continue
        
        output = collector.get_output(execution_id, offset=offset)
        
        # Verify stdout from offset
        expected_stdout = stdout_data[offset:].decode('utf-8', errors='replace')
        assert output.stdout == expected_stdout, \
            f"stdout from offset {offset} should match data[{offset}:]"
        
        # Verify stderr from offset
        expected_stderr = stderr_data[offset:].decode('utf-8', errors='replace')
        assert output.stderr == expected_stderr, \
            f"stderr from offset {offset} should match data[{offset}:]"
        
        # Verify offsets still reflect total data
        assert output.stdout_offset == len(stdout_data), \
            "stdout_offset should always reflect total bytes"
        assert output.stderr_offset == len(stderr_data), \
            "stderr_offset should always reflect total bytes"


@given(
    execution_id=execution_id_strategy(),
    data=output_data_strategy(),
    stream=stream_name_strategy()
)
def test_property_32_offset_beyond_end(execution_id, data, stream):
    """
    Property 32 (variant): Offset beyond data end should return empty output.
    
    Validates: Requirements 6.6
    """
    collector = OutputCollector()
    collector.create_buffer(execution_id)
    
    # Capture data
    if data:
        collector.capture_output(execution_id, stream, data)
    
    # Request with offset beyond end
    offset = len(data) + 100
    output = collector.get_output(execution_id, offset=offset)
    
    # Should return empty strings but valid offsets
    assert output.stdout == '', "stdout beyond offset should be empty"
    assert output.stderr == '', "stderr beyond offset should be empty"
    
    if stream == 'stdout':
        assert output.stdout_offset == len(data), \
            "stdout_offset should reflect actual data length"
    else:
        assert output.stderr_offset == len(data), \
            "stderr_offset should reflect actual data length"


@given(
    execution_id=execution_id_strategy(),
    data=output_data_strategy()
)
def test_property_32_zero_offset(execution_id, data):
    """
    Property 32 (variant): Zero offset should return all output.
    
    Validates: Requirements 6.6
    """
    collector = OutputCollector()
    collector.create_buffer(execution_id)
    
    # Capture data
    if data:
        collector.capture_output(execution_id, 'stdout', data)
    
    # Request with offset 0
    output = collector.get_output(execution_id, offset=0)
    
    # Should return all data
    expected = data.decode('utf-8', errors='replace')
    assert output.stdout == expected, \
        "Offset 0 should return all captured data"


@given(
    execution_id=execution_id_strategy(),
    negative_offset=st.integers(max_value=-1)
)
def test_property_32_negative_offset_error(execution_id, negative_offset):
    """
    Property 32 (variant): Negative offset should be rejected.
    
    Validates: Requirements 6.6
    """
    collector = OutputCollector()
    collector.create_buffer(execution_id)
    
    # Try to retrieve with negative offset
    with pytest.raises(ValueError, match="Offset must be non-negative"):
        collector.get_output(execution_id, offset=negative_offset)


# Thread Safety Tests
@given(
    execution_id=execution_id_strategy(),
    data_chunks=st.lists(output_data_strategy(), min_size=5, max_size=20)
)
@settings(max_examples=20)
def test_concurrent_capture_thread_safety(execution_id, data_chunks):
    """
    Verify that concurrent capture operations are thread-safe.
    
    Validates: Requirements 5.3, 5.4
    """
    collector = OutputCollector()
    collector.create_buffer(execution_id)
    
    # Capture data concurrently from multiple threads
    def capture_chunk(chunk, stream):
        if chunk:
            collector.capture_output(execution_id, stream, chunk)
    
    threads = []
    total_stdout = b''
    total_stderr = b''
    
    for i, chunk in enumerate(data_chunks):
        stream = 'stdout' if i % 2 == 0 else 'stderr'
        if stream == 'stdout':
            total_stdout += chunk
        else:
            total_stderr += chunk
        
        thread = threading.Thread(target=capture_chunk, args=(chunk, stream))
        threads.append(thread)
        thread.start()
    
    # Wait for all threads
    for thread in threads:
        thread.join()
    
    # Verify all data was captured (order may vary due to concurrency)
    output = collector.get_output(execution_id)
    
    # Check that total bytes match
    assert output.stdout_offset == len(total_stdout), \
        "Concurrent stdout capture should capture all bytes"
    assert output.stderr_offset == len(total_stderr), \
        "Concurrent stderr capture should capture all bytes"


@given(
    execution_id=execution_id_strategy(),
    data=output_data_strategy()
)
def test_concurrent_read_write_thread_safety(execution_id, data):
    """
    Verify that concurrent read and write operations are thread-safe.
    
    Validates: Requirements 5.3, 6.3, 6.4
    """
    collector = OutputCollector()
    collector.create_buffer(execution_id)
    
    # Capture initial data
    if data:
        collector.capture_output(execution_id, 'stdout', data)
    
    results = []
    errors = []
    
    def read_output():
        try:
            output = collector.get_output(execution_id)
            results.append(output)
        except Exception as e:
            errors.append(e)
    
    def write_output():
        try:
            collector.capture_output(execution_id, 'stderr', b'concurrent write')
        except Exception as e:
            errors.append(e)
    
    # Start concurrent reads and writes
    threads = []
    for _ in range(5):
        threads.append(threading.Thread(target=read_output))
        threads.append(threading.Thread(target=write_output))
    
    for thread in threads:
        thread.start()
    
    for thread in threads:
        thread.join()
    
    # Verify no errors occurred
    assert len(errors) == 0, f"Concurrent operations should not raise errors: {errors}"
    
    # Verify reads succeeded
    assert len(results) > 0, "Concurrent reads should succeed"
