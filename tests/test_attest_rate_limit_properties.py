"""Property-based tests for /attest endpoint rate limiting.

Feature: github-actions-remote-executor
Tests Property 155 from the design document.
"""
from hypothesis import given, strategies as st, settings
from fastapi.testclient import TestClient

from src.server import create_app
from src.config import ServerConfig


def get_test_config(**overrides):
    """Create test configuration with optional overrides"""
    defaults = dict(
        port=8000,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=10,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/test",
        output_retention_hours=24,
        tpm_attest_path="/usr/bin/nitro-tpm-attest",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )
    defaults.update(overrides)
    return ServerConfig(**defaults)


# Feature: github-actions-remote-executor, Property 155: Attest Endpoint Rate Limiting
@settings(max_examples=20, deadline=None)
@given(st.integers(min_value=1, max_value=20))
def test_attest_endpoint_rate_limiting(rate_limit):
    """
    **Validates: Requirements 37.12, 37.13**

    For any source IP that exceeds the configured rate limit on /attest,
    subsequent requests should return HTTP 429.
    """
    config = get_test_config(rate_limit_per_ip=rate_limit, rate_limit_window_seconds=60)
    app = create_app(config)
    client = TestClient(app)

    # Make requests up to the rate limit — none should return 429
    for i in range(rate_limit):
        response = client.get("/attest")
        assert response.status_code != 429, (
            f"Request {i+1}/{rate_limit} should not be rate limited, got 429"
        )

    # The next request should be rate-limited
    response = client.get("/attest")
    assert response.status_code == 429, (
        f"Request after exceeding rate limit should return 429, got {response.status_code}"
    )
