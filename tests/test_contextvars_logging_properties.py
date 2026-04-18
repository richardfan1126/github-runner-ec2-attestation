"""Property-based tests for contextvars-based log isolation

Feature: github-actions-remote-executor
Tests Property 148 from the design document
"""
import asyncio
import logging
import threading
from io import StringIO

from hypothesis import given, strategies as st, settings

from src.logging_config import (
    _log_context_var,
    set_log_context,
    clear_log_context,
    ContextFilter,
)


# ---------------------------------------------------------------------------
# Property 148: Contextvars Log Isolation
#
# *For any* two concurrent requests or tasks, the log context for one should
# not be visible to or modifiable by the other, ensuring per-request isolation
# via contextvars.ContextVar.
#
# **Validates: Requirements 7.9, 7.10**
# ---------------------------------------------------------------------------

@given(
    exec_id_a=st.uuids().map(str),
    exec_id_b=st.uuids().map(str),
    req_id_a=st.uuids().map(str),
    req_id_b=st.uuids().map(str),
)
@settings(max_examples=100)
def test_property_148_contextvars_log_isolation(
    exec_id_a, exec_id_b, req_id_a, req_id_b
):
    """
    Property 148: Contextvars Log Isolation

    Simulate two concurrent async tasks each setting their own log context.
    Verify that the context set by one task is never visible to the other.

    **Validates: Requirements 7.9, 7.10**
    """
    observed_a: dict = {}
    observed_b: dict = {}
    barrier = threading.Barrier(2, timeout=5)

    def task_a():
        # Start with a clean slate
        clear_log_context()
        set_log_context(execution_id=exec_id_a, request_id=req_id_a)
        # Wait for task_b to also set its context
        barrier.wait()
        # Read back our own context — it must still be ours
        ctx = _log_context_var.get()
        observed_a.update(ctx)
        clear_log_context()

    def task_b():
        clear_log_context()
        set_log_context(execution_id=exec_id_b, request_id=req_id_b)
        barrier.wait()
        ctx = _log_context_var.get()
        observed_b.update(ctx)
        clear_log_context()

    # Run both tasks in separate threads (each thread gets its own
    # contextvars.Context copy by default in Python ≥ 3.7).
    t_a = threading.Thread(target=task_a)
    t_b = threading.Thread(target=task_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    # Assert isolation: each task saw only its own context
    assert observed_a.get("execution_id") == exec_id_a, (
        f"Task A saw execution_id={observed_a.get('execution_id')}, expected {exec_id_a}"
    )
    assert observed_a.get("request_id") == req_id_a, (
        f"Task A saw request_id={observed_a.get('request_id')}, expected {req_id_a}"
    )
    assert observed_b.get("execution_id") == exec_id_b, (
        f"Task B saw execution_id={observed_b.get('execution_id')}, expected {exec_id_b}"
    )
    assert observed_b.get("request_id") == req_id_b, (
        f"Task B saw request_id={observed_b.get('request_id')}, expected {req_id_b}"
    )

    # Cross-check: task A must NOT have seen task B's values and vice versa
    if exec_id_a != exec_id_b:
        assert observed_a.get("execution_id") != exec_id_b
        assert observed_b.get("execution_id") != exec_id_a
