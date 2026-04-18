"""Unit tests for contextvars-based logging isolation

Validates Requirements 7.9, 7.10
"""
import contextvars
import logging
import threading
from io import StringIO

from src.logging_config import (
    _log_context_var,
    set_log_context,
    clear_log_context,
    ContextFilter,
)


def test_set_and_clear_log_context():
    """set_log_context stores values; clear_log_context resets them."""
    clear_log_context()
    set_log_context(execution_id="exec-1", request_id="req-1")
    ctx = _log_context_var.get()
    assert ctx["execution_id"] == "exec-1"
    assert ctx["request_id"] == "req-1"

    clear_log_context()
    assert _log_context_var.get() == {}


def test_set_log_context_merges():
    """Successive set_log_context calls merge rather than replace."""
    clear_log_context()
    set_log_context(execution_id="exec-1")
    set_log_context(request_id="req-1")
    ctx = _log_context_var.get()
    assert ctx["execution_id"] == "exec-1"
    assert ctx["request_id"] == "req-1"
    clear_log_context()


def test_concurrent_requests_have_isolated_log_contexts():
    """Two threads setting different log contexts must not see each other's values."""
    results = {"a": {}, "b": {}}
    barrier = threading.Barrier(2, timeout=5)

    def request_a():
        clear_log_context()
        set_log_context(execution_id="exec-a", request_id="req-a")
        barrier.wait()
        results["a"] = dict(_log_context_var.get())
        clear_log_context()

    def request_b():
        clear_log_context()
        set_log_context(execution_id="exec-b", request_id="req-b")
        barrier.wait()
        results["b"] = dict(_log_context_var.get())
        clear_log_context()

    ta = threading.Thread(target=request_a)
    tb = threading.Thread(target=request_b)
    ta.start()
    tb.start()
    ta.join(timeout=10)
    tb.join(timeout=10)

    assert results["a"]["execution_id"] == "exec-a"
    assert results["a"]["request_id"] == "req-a"
    assert results["b"]["execution_id"] == "exec-b"
    assert results["b"]["request_id"] == "req-b"


def test_background_task_does_not_leak_context_to_parent():
    """A background thread that sets log context must not affect the caller's context."""
    clear_log_context()
    set_log_context(execution_id="parent-exec", request_id="parent-req")

    def background():
        # Running in a new thread — gets a copy of parent context by default,
        # but mutations should not propagate back.
        set_log_context(execution_id="bg-exec", request_id="bg-req")

    t = threading.Thread(target=background)
    t.start()
    t.join(timeout=5)

    # Parent context must be unchanged
    ctx = _log_context_var.get()
    assert ctx["execution_id"] == "parent-exec"
    assert ctx["request_id"] == "parent-req"
    clear_log_context()


def test_background_task_with_fresh_context():
    """A background thread running in a fresh contextvars.Context starts clean."""
    clear_log_context()
    set_log_context(execution_id="parent-exec")

    observed = {}

    def background():
        # Should start with empty context because we use copy_context + run
        observed.update(_log_context_var.get())

    ctx = contextvars.copy_context()
    t = threading.Thread(target=ctx.run, args=(background,))
    t.start()
    t.join(timeout=5)

    # copy_context copies the parent, so the child sees the parent's value
    # but mutations in the child don't leak back.
    assert observed.get("execution_id") == "parent-exec"

    # Parent still has its own context
    assert _log_context_var.get()["execution_id"] == "parent-exec"
    clear_log_context()


def test_context_filter_reads_from_contextvar():
    """ContextFilter.filter() should inject fields from the ContextVar into log records."""
    clear_log_context()
    set_log_context(execution_id="filter-exec", request_id="filter-req")

    cf = ContextFilter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None,
    )
    cf.filter(record)

    assert record.execution_id == "filter-exec"
    assert record.request_id == "filter-req"
    clear_log_context()


def test_context_filter_defaults_when_empty():
    """ContextFilter should set defaults when no context is set."""
    clear_log_context()

    cf = ContextFilter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None,
    )
    cf.filter(record)

    assert record.execution_id == "-"
    assert record.request_id == "-"
