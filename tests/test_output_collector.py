"""Unit tests for output collector

Feature: github-actions-remote-executor
Tests specific scenarios for OutputCollector including:
- Large output handling
- Concurrent output capture
- Offset edge cases (0, beyond end, negative)

Requirements: 5.3, 5.4, 6.3, 6.4, 6.5, 6.6
"""
import pytest
import threading
import time
from src.output_collector import OutputCollector
from src.models import OutputData


class TestLargeOutputHandling:
    """Test handling of large output volumes"""
    
    def test_large_stdout_capture(self):
        """Test capturing large stdout output (10MB)"""
        collector = OutputCollector()
        execution_id = "test-large-stdout"
        collector.create_buffer(execution_id)
        
        # Generate 10MB of data
        chunk_size = 1024 * 1024  # 1MB chunks
        num_chunks = 10
        total_captured = 0
        
        for i in range(num_chunks):
            # Create 1MB chunk with identifiable pattern
            pattern = f"Line {i} ".encode('utf-8')
            # Calculate how many repetitions needed
            repetitions = chunk_size // len(pattern)
            chunk = pattern * repetitions
            # Pad to exact size if needed
            if len(chunk) < chunk_size:
                chunk += b'X' * (chunk_size - len(chunk))
            chunk = chunk[:chunk_size]  # Ensure exact size
            
            collector.capture_output(execution_id, 'stdout', chunk)
            total_captured += len(chunk)
        
        # Retrieve and verify
        output = collector.get_output(execution_id)
        
        assert output.stdout_offset == total_captured, \
            f"Should capture all {total_captured} bytes of stdout"
        assert "Line 0" in output.stdout, "Should contain first chunk"
        assert "Line 9" in output.stdout, "Should contain last chunk"
    
    def test_large_stderr_capture(self):
        """Test capturing large stderr output (5MB)"""
        collector = OutputCollector()
        execution_id = "test-large-stderr"
        collector.create_buffer(execution_id)
        
        # Generate 5MB of error data
        chunk_size = 1024 * 1024  # 1MB
        num_chunks = 5
        total_captured = 0
        
        for i in range(num_chunks):
            pattern = f"Error {i} ".encode('utf-8')
            repetitions = chunk_size // len(pattern)
            chunk = pattern * repetitions
            if len(chunk) < chunk_size:
                chunk += b'X' * (chunk_size - len(chunk))
            chunk = chunk[:chunk_size]
            
            collector.capture_output(execution_id, 'stderr', chunk)
            total_captured += len(chunk)
        
        output = collector.get_output(execution_id)
        
        assert output.stderr_offset == total_captured, \
            f"Should capture all {total_captured} bytes of stderr"
        assert "Error 0" in output.stderr, "Should contain first error chunk"
        assert "Error 4" in output.stderr, "Should contain last error chunk"
    
    def test_large_mixed_output(self):
        """Test capturing large mixed stdout and stderr (20MB total)"""
        collector = OutputCollector()
        execution_id = "test-large-mixed"
        collector.create_buffer(execution_id)
        
        chunk_size = 1024 * 1024  # 1MB
        total_stdout = 0
        total_stderr = 0
        
        # Interleave 10MB stdout and 10MB stderr
        for i in range(10):
            stdout_pattern = f"OUT{i}:".encode('utf-8')
            stdout_reps = chunk_size // len(stdout_pattern)
            stdout_chunk = stdout_pattern * stdout_reps
            if len(stdout_chunk) < chunk_size:
                stdout_chunk += b'X' * (chunk_size - len(stdout_chunk))
            stdout_chunk = stdout_chunk[:chunk_size]
            collector.capture_output(execution_id, 'stdout', stdout_chunk)
            total_stdout += len(stdout_chunk)
            
            stderr_pattern = f"ERR{i}:".encode('utf-8')
            stderr_reps = chunk_size // len(stderr_pattern)
            stderr_chunk = stderr_pattern * stderr_reps
            if len(stderr_chunk) < chunk_size:
                stderr_chunk += b'X' * (chunk_size - len(stderr_chunk))
            stderr_chunk = stderr_chunk[:chunk_size]
            collector.capture_output(execution_id, 'stderr', stderr_chunk)
            total_stderr += len(stderr_chunk)
        
        output = collector.get_output(execution_id)
        
        assert output.stdout_offset == total_stdout, \
            f"Should capture {total_stdout} bytes stdout"
        assert output.stderr_offset == total_stderr, \
            f"Should capture {total_stderr} bytes stderr"
        assert "OUT0:" in output.stdout and "OUT9:" in output.stdout
        assert "ERR0:" in output.stderr and "ERR9:" in output.stderr
    
    def test_large_output_with_offset_retrieval(self):
        """Test retrieving large output from various offsets"""
        collector = OutputCollector()
        execution_id = "test-large-offset"
        collector.create_buffer(execution_id)
        
        # Create 5MB of data
        total_size = 5 * 1024 * 1024
        chunk = b'X' * total_size
        collector.capture_output(execution_id, 'stdout', chunk)
        
        # Test retrieving from middle
        offset = 2 * 1024 * 1024  # 2MB offset
        output = collector.get_output(execution_id, offset=offset)
        
        expected_size = total_size - offset
        assert len(output.stdout.encode('utf-8')) == expected_size, \
            f"Should retrieve {expected_size} bytes from offset {offset}"
        assert output.stdout_offset == total_size, \
            "Offset should reflect total data size"
    
    def test_memory_efficiency_with_large_output(self):
        """Test that large output doesn't cause memory issues"""
        collector = OutputCollector()
        execution_id = "test-memory"
        collector.create_buffer(execution_id)
        
        # Write 50MB in small chunks to simulate streaming
        chunk_size = 64 * 1024  # 64KB chunks
        num_chunks = 800  # 50MB total
        
        for i in range(num_chunks):
            chunk = bytes([i % 256]) * chunk_size
            collector.capture_output(execution_id, 'stdout', chunk)
        
        # Verify total captured
        output = collector.get_output(execution_id)
        expected_total = chunk_size * num_chunks
        
        assert output.stdout_offset == expected_total, \
            f"Should capture all {expected_total} bytes"


class TestConcurrentOutputCapture:
    """Test concurrent access to output collector"""
    
    def test_concurrent_writes_same_stream(self):
        """Test multiple threads writing to same stream"""
        collector = OutputCollector()
        execution_id = "test-concurrent-same"
        collector.create_buffer(execution_id)
        
        num_threads = 10
        writes_per_thread = 100
        errors = []
        
        def write_data(thread_id):
            try:
                for i in range(writes_per_thread):
                    data = f"Thread{thread_id}-Write{i}\n".encode('utf-8')
                    collector.capture_output(execution_id, 'stdout', data)
            except Exception as e:
                errors.append(e)
        
        threads = []
        for tid in range(num_threads):
            thread = threading.Thread(target=write_data, args=(tid,))
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        assert len(errors) == 0, f"No errors should occur: {errors}"
        
        output = collector.get_output(execution_id)
        
        # Verify all writes were captured
        for tid in range(num_threads):
            assert f"Thread{tid}-Write0" in output.stdout, \
                f"Should contain data from thread {tid}"
            assert f"Thread{tid}-Write{writes_per_thread-1}" in output.stdout, \
                f"Should contain last write from thread {tid}"
    
    def test_concurrent_writes_different_streams(self):
        """Test threads writing to stdout and stderr concurrently"""
        collector = OutputCollector()
        execution_id = "test-concurrent-diff"
        collector.create_buffer(execution_id)
        
        num_threads = 20
        writes_per_thread = 50
        errors = []
        
        def write_stdout(thread_id):
            try:
                for i in range(writes_per_thread):
                    data = f"OUT-T{thread_id}-{i}\n".encode('utf-8')
                    collector.capture_output(execution_id, 'stdout', data)
            except Exception as e:
                errors.append(e)
        
        def write_stderr(thread_id):
            try:
                for i in range(writes_per_thread):
                    data = f"ERR-T{thread_id}-{i}\n".encode('utf-8')
                    collector.capture_output(execution_id, 'stderr', data)
            except Exception as e:
                errors.append(e)
        
        threads = []
        for tid in range(num_threads // 2):
            threads.append(threading.Thread(target=write_stdout, args=(tid,)))
            threads.append(threading.Thread(target=write_stderr, args=(tid,)))
        
        for thread in threads:
            thread.start()
        
        for thread in threads:
            thread.join()
        
        assert len(errors) == 0, f"No errors should occur: {errors}"
        
        output = collector.get_output(execution_id)
        
        # Verify stdout and stderr are separate
        assert "OUT-T0-0" in output.stdout
        assert "ERR-T0-0" in output.stderr
        assert "OUT-T" not in output.stderr, "stdout data should not leak to stderr"
        assert "ERR-T" not in output.stdout, "stderr data should not leak to stdout"
    
    def test_concurrent_read_write(self):
        """Test concurrent reads and writes"""
        collector = OutputCollector()
        execution_id = "test-concurrent-rw"
        collector.create_buffer(execution_id)
        
        num_writers = 5
        num_readers = 5
        writes_per_writer = 100
        reads_per_reader = 50
        
        errors = []
        read_results = []
        
        def writer(writer_id):
            try:
                for i in range(writes_per_writer):
                    data = f"W{writer_id}-{i}\n".encode('utf-8')
                    collector.capture_output(execution_id, 'stdout', data)
                    time.sleep(0.001)  # Small delay to interleave
            except Exception as e:
                errors.append(e)
        
        def reader(reader_id):
            try:
                for i in range(reads_per_reader):
                    output = collector.get_output(execution_id)
                    read_results.append((reader_id, i, output.stdout_offset))
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)
        
        threads = []
        
        # Start writers
        for wid in range(num_writers):
            threads.append(threading.Thread(target=writer, args=(wid,)))
        
        # Start readers
        for rid in range(num_readers):
            threads.append(threading.Thread(target=reader, args=(rid,)))
        
        for thread in threads:
            thread.start()
        
        for thread in threads:
            thread.join()
        
        assert len(errors) == 0, f"No errors should occur: {errors}"
        assert len(read_results) == num_readers * reads_per_reader, \
            "All reads should complete"
        
        # Verify reads saw increasing offsets (monotonic growth)
        for reader_id in range(num_readers):
            reader_offsets = [offset for rid, _, offset in read_results if rid == reader_id]
            # Offsets should generally increase (allowing for some reads at same offset)
            assert reader_offsets[-1] >= reader_offsets[0], \
                f"Reader {reader_id} should see growing output"
    
    def test_concurrent_mark_complete(self):
        """Test marking complete while reads/writes are happening"""
        collector = OutputCollector()
        execution_id = "test-concurrent-complete"
        collector.create_buffer(execution_id)
        
        errors = []
        completion_results = []
        
        def writer():
            try:
                for i in range(100):
                    data = f"Line {i}\n".encode('utf-8')
                    collector.capture_output(execution_id, 'stdout', data)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)
        
        def reader():
            try:
                for _ in range(50):
                    output = collector.get_output(execution_id)
                    completion_results.append(output.complete)
                    time.sleep(0.002)
            except Exception as e:
                errors.append(e)
        
        def completer():
            try:
                time.sleep(0.05)  # Let some writes happen
                collector.mark_complete(execution_id, 0)
            except Exception as e:
                errors.append(e)
        
        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=completer)
        ]
        
        for thread in threads:
            thread.start()
        
        for thread in threads:
            thread.join()
        
        assert len(errors) == 0, f"No errors should occur: {errors}"
        
        # Should see transition from incomplete to complete
        assert False in completion_results, "Should see incomplete state"
        assert True in completion_results, "Should see complete state"
    
    def test_concurrent_multiple_executions(self):
        """Test concurrent operations on different execution IDs"""
        collector = OutputCollector()
        
        num_executions = 10
        writes_per_execution = 100
        errors = []
        
        def write_execution(exec_id):
            try:
                execution_id = f"exec-{exec_id}"
                collector.create_buffer(execution_id)
                
                for i in range(writes_per_execution):
                    data = f"Exec{exec_id}-Line{i}\n".encode('utf-8')
                    collector.capture_output(execution_id, 'stdout', data)
                
                collector.mark_complete(execution_id, exec_id)
            except Exception as e:
                errors.append(e)
        
        threads = []
        for eid in range(num_executions):
            thread = threading.Thread(target=write_execution, args=(eid,))
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        assert len(errors) == 0, f"No errors should occur: {errors}"
        
        # Verify each execution has correct data
        for eid in range(num_executions):
            execution_id = f"exec-{eid}"
            output = collector.get_output(execution_id)
            
            assert output.complete is True
            assert output.exit_code == eid
            assert f"Exec{eid}-Line0" in output.stdout
            assert f"Exec{eid}-Line{writes_per_execution-1}" in output.stdout
            
            # Verify no cross-contamination
            for other_eid in range(num_executions):
                if other_eid != eid:
                    assert f"Exec{other_eid}-" not in output.stdout, \
                        f"Execution {eid} should not contain data from {other_eid}"


class TestOffsetEdgeCases:
    """Test edge cases for offset-based retrieval"""
    
    def test_offset_zero(self):
        """Test offset=0 returns all output"""
        collector = OutputCollector()
        execution_id = "test-offset-zero"
        collector.create_buffer(execution_id)
        
        data = b"Hello, World!\nThis is a test.\n"
        collector.capture_output(execution_id, 'stdout', data)
        
        output = collector.get_output(execution_id, offset=0)
        
        assert output.stdout == data.decode('utf-8'), \
            "Offset 0 should return all data"
        assert output.stdout_offset == len(data), \
            "Offset should reflect total data"
    
    def test_offset_at_end(self):
        """Test offset exactly at end of data"""
        collector = OutputCollector()
        execution_id = "test-offset-end"
        collector.create_buffer(execution_id)
        
        data = b"Some output data"
        collector.capture_output(execution_id, 'stdout', data)
        
        # Request from exact end
        output = collector.get_output(execution_id, offset=len(data))
        
        assert output.stdout == '', "Offset at end should return empty string"
        assert output.stdout_offset == len(data), \
            "Offset should still reflect total data"
    
    def test_offset_beyond_end(self):
        """Test offset beyond end of data"""
        collector = OutputCollector()
        execution_id = "test-offset-beyond"
        collector.create_buffer(execution_id)
        
        data = b"Short output"
        collector.capture_output(execution_id, 'stdout', data)
        
        # Request from way beyond end
        output = collector.get_output(execution_id, offset=len(data) + 1000)
        
        assert output.stdout == '', "Offset beyond end should return empty"
        assert output.stderr == '', "Offset beyond end should return empty"
        assert output.stdout_offset == len(data), \
            "Offset should reflect actual data size"
    
    def test_offset_negative(self):
        """Test negative offset raises error"""
        collector = OutputCollector()
        execution_id = "test-offset-negative"
        collector.create_buffer(execution_id)
        
        collector.capture_output(execution_id, 'stdout', b"data")
        
        with pytest.raises(ValueError, match="Offset must be non-negative"):
            collector.get_output(execution_id, offset=-1)
        
        with pytest.raises(ValueError, match="Offset must be non-negative"):
            collector.get_output(execution_id, offset=-100)
    
    def test_offset_middle_of_data(self):
        """Test offset in middle returns correct substring"""
        collector = OutputCollector()
        execution_id = "test-offset-middle"
        collector.create_buffer(execution_id)
        
        data = b"0123456789ABCDEFGHIJ"
        collector.capture_output(execution_id, 'stdout', data)
        
        # Test various middle offsets
        test_cases = [
            (5, "56789ABCDEFGHIJ"),
            (10, "ABCDEFGHIJ"),
            (15, "FGHIJ"),
            (19, "J"),
        ]
        
        for offset, expected in test_cases:
            output = collector.get_output(execution_id, offset=offset)
            assert output.stdout == expected, \
                f"Offset {offset} should return '{expected}'"
            assert output.stdout_offset == len(data), \
                "Total offset should remain constant"
    
    def test_offset_with_empty_output(self):
        """Test offset behavior with no output captured"""
        collector = OutputCollector()
        execution_id = "test-offset-empty"
        collector.create_buffer(execution_id)
        
        # No data captured
        output = collector.get_output(execution_id, offset=0)
        
        assert output.stdout == ''
        assert output.stderr == ''
        assert output.stdout_offset == 0
        assert output.stderr_offset == 0
        
        # Offset beyond empty should also work
        output = collector.get_output(execution_id, offset=100)
        assert output.stdout == ''
        assert output.stderr == ''
    
    def test_offset_incremental_polling(self):
        """Test typical polling pattern with increasing offsets"""
        collector = OutputCollector()
        execution_id = "test-offset-polling"
        collector.create_buffer(execution_id)
        
        # Simulate incremental output
        chunks = [
            b"First line\n",
            b"Second line\n",
            b"Third line\n",
            b"Fourth line\n"
        ]
        
        current_offset = 0
        all_output = []
        
        for chunk in chunks:
            collector.capture_output(execution_id, 'stdout', chunk)
            
            # Poll from current offset
            output = collector.get_output(execution_id, offset=current_offset)
            
            # Should get only new data
            expected = chunk.decode('utf-8')
            assert output.stdout == expected, \
                f"Should get new chunk: {expected}"
            
            all_output.append(output.stdout)
            current_offset = output.stdout_offset
        
        # Verify we got all data incrementally
        full_output = ''.join(all_output)
        expected_full = b''.join(chunks).decode('utf-8')
        assert full_output == expected_full, \
            "Incremental polling should reconstruct full output"
    
    def test_offset_applies_to_both_streams(self):
        """Test that offset applies independently to stdout and stderr"""
        collector = OutputCollector()
        execution_id = "test-offset-both"
        collector.create_buffer(execution_id)
        
        stdout_data = b"STDOUT: Line 1\nSTDOUT: Line 2\n"
        stderr_data = b"STDERR: Error 1\nSTDERR: Error 2\n"
        
        collector.capture_output(execution_id, 'stdout', stdout_data)
        collector.capture_output(execution_id, 'stderr', stderr_data)
        
        # Request from offset 10
        output = collector.get_output(execution_id, offset=10)
        
        # Offset applies to both streams
        expected_stdout = stdout_data[10:].decode('utf-8')
        expected_stderr = stderr_data[10:].decode('utf-8')
        
        assert output.stdout == expected_stdout
        assert output.stderr == expected_stderr
        assert output.stdout_offset == len(stdout_data)
        assert output.stderr_offset == len(stderr_data)
    
    def test_offset_with_unicode_data(self):
        """Test offset handling with multi-byte UTF-8 characters"""
        collector = OutputCollector()
        execution_id = "test-offset-unicode"
        collector.create_buffer(execution_id)
        
        # Unicode data with multi-byte characters
        data = "Hello 世界 🌍 Test".encode('utf-8')
        collector.capture_output(execution_id, 'stdout', data)
        
        # Test offset at byte boundaries
        output_0 = collector.get_output(execution_id, offset=0)
        assert "Hello" in output_0.stdout
        assert "世界" in output_0.stdout
        assert "🌍" in output_0.stdout
        
        # Offset in middle (may split multi-byte char, should handle gracefully)
        output_mid = collector.get_output(execution_id, offset=10)
        # Should decode with replacement for broken chars
        assert isinstance(output_mid.stdout, str)
        assert output_mid.stdout_offset == len(data)


class TestOutputCollectorEdgeCases:
    """Additional edge case tests"""
    
    def test_nonexistent_execution_id(self):
        """Test operations on non-existent execution ID"""
        collector = OutputCollector()
        
        with pytest.raises(ValueError, match="Execution ID not found"):
            collector.capture_output("nonexistent", 'stdout', b"data")
        
        with pytest.raises(ValueError, match="Execution ID not found"):
            collector.get_output("nonexistent")
        
        with pytest.raises(ValueError, match="Execution ID not found"):
            collector.mark_complete("nonexistent", 0)
    
    def test_invalid_stream_name(self):
        """Test invalid stream names are rejected"""
        collector = OutputCollector()
        execution_id = "test-invalid-stream"
        collector.create_buffer(execution_id)
        
        with pytest.raises(ValueError, match="Invalid stream"):
            collector.capture_output(execution_id, 'invalid', b"data")
        
        with pytest.raises(ValueError, match="Invalid stream"):
            collector.capture_output(execution_id, 'STDOUT', b"data")
        
        with pytest.raises(ValueError, match="Invalid stream"):
            collector.capture_output(execution_id, '', b"data")
    
    def test_empty_data_capture(self):
        """Test capturing empty data"""
        collector = OutputCollector()
        execution_id = "test-empty-data"
        collector.create_buffer(execution_id)
        
        # Capture empty bytes
        collector.capture_output(execution_id, 'stdout', b'')
        collector.capture_output(execution_id, 'stderr', b'')
        
        output = collector.get_output(execution_id)
        
        assert output.stdout == ''
        assert output.stderr == ''
        assert output.stdout_offset == 0
        assert output.stderr_offset == 0
    
    def test_multiple_mark_complete(self):
        """Test marking complete multiple times"""
        collector = OutputCollector()
        execution_id = "test-multi-complete"
        collector.create_buffer(execution_id)
        
        collector.capture_output(execution_id, 'stdout', b"output")
        
        # Mark complete multiple times with different exit codes
        collector.mark_complete(execution_id, 0)
        collector.mark_complete(execution_id, 1)
        collector.mark_complete(execution_id, 127)
        
        output = collector.get_output(execution_id)
        
        # Last one wins
        assert output.complete is True
        assert output.exit_code == 127
    
    def test_remove_output(self):
        """Test removing output buffer"""
        collector = OutputCollector()
        execution_id = "test-remove"
        collector.create_buffer(execution_id)
        
        collector.capture_output(execution_id, 'stdout', b"data")
        
        assert collector.has_output(execution_id) is True
        
        collector.remove_output(execution_id)
        
        assert collector.has_output(execution_id) is False
        
        # Should not be able to access after removal
        with pytest.raises(ValueError, match="Execution ID not found"):
            collector.get_output(execution_id)
    
    def test_has_output(self):
        """Test has_output method"""
        collector = OutputCollector()
        execution_id = "test-has"
        
        assert collector.has_output(execution_id) is False
        
        collector.create_buffer(execution_id)
        
        assert collector.has_output(execution_id) is True
        
        collector.remove_output(execution_id)
        
        assert collector.has_output(execution_id) is False
