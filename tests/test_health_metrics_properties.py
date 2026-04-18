"""Property-based tests for health endpoint"""
from hypothesis import given, strategies as st, settings
from fastapi.testclient import TestClient

from src.server import create_app
from src.config import ServerConfig


# Test configuration
def get_test_config(**overrides):
    """Create test configuration with optional overrides"""
    defaults = dict(
        port=8000,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,  # 1MB
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


# Feature: github-actions-remote-executor, Property 153: Health Endpoint Rate Limiting
@settings(max_examples=20, deadline=None)
@given(st.integers(min_value=1, max_value=20))
def test_health_endpoint_rate_limiting(rate_limit):
    """
    **Validates: Requirements 10.4**

    For any source IP address that exceeds the configured rate limit on the
    /health endpoint, subsequent requests should be rejected with HTTP 429.
    """
    config = get_test_config(rate_limit_per_ip=rate_limit, rate_limit_window_seconds=60)
    app = create_app(config)
    client = TestClient(app)

    # Make requests up to the rate limit — all should succeed
    for i in range(rate_limit):
        response = client.get("/health")
        assert response.status_code == 200, (
            f"Request {i+1}/{rate_limit} should succeed, got {response.status_code}"
        )

    # The next request should be rate-limited
    response = client.get("/health")
    assert response.status_code == 429, (
        f"Request after exceeding rate limit should return 429, got {response.status_code}"
    )
