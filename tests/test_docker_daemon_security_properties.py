"""
Property-based tests for Docker Daemon Security Configuration.

These tests validate that the KIWI image includes a hardened Docker daemon
configuration at ~gha-executor/.config/docker/daemon.json for rootless Docker,
with no-new-privileges enabled and no userns-remap (rootless Docker provides
user namespace isolation natively), as specified in Requirement 48.
"""

import json
from pathlib import Path


def test_docker_daemon_security_configuration():
    """
    Property 164: Docker Daemon Security Configuration

    For any KIWI image build, the image should include a daemon.json at
    ~gha-executor/.config/docker/daemon.json (rootless Docker config path)
    with no-new-privileges and without userns-remap (rootless Docker provides
    user namespace isolation natively).

    **Validates: Requirements 48.1, 48.2, 48.3, 48.4**
    """
    daemon_json_path = Path(
        "kiwi-descriptions/root/home/gha-executor/.config/docker/daemon.json"
    )

    # Requirement 48.1: daemon.json must exist at the rootless Docker config path
    assert daemon_json_path.exists(), (
        "daemon.json must exist at kiwi-descriptions/root/home/gha-executor/"
        ".config/docker/daemon.json for rootless Docker configuration"
    )

    content = daemon_json_path.read_text()
    config = json.loads(content)

    # Requirement 48.2: no-new-privileges must be set to true
    assert config.get("no-new-privileges") is True, (
        "daemon.json must set 'no-new-privileges' to true to prevent "
        "privilege escalation inside containers"
    )

    # Requirement 48.3: live-restore must be set to false
    assert config.get("live-restore") is False, (
        "daemon.json must set 'live-restore' to false to ensure containers "
        "stop when the daemon restarts"
    )

    # Requirement 48.4: userns-remap must NOT be present
    # Rootless Docker provides user namespace isolation natively
    assert "userns-remap" not in config, (
        "daemon.json must NOT include 'userns-remap' — rootless Docker "
        "provides user namespace isolation natively"
    )

    # Verify the old system-wide daemon.json does NOT exist
    old_daemon_json_path = Path("kiwi-descriptions/root/etc/docker/daemon.json")
    assert not old_daemon_json_path.exists(), (
        "System-wide /etc/docker/daemon.json must NOT exist — rootless Docker "
        "uses ~gha-executor/.config/docker/daemon.json instead"
    )


def test_docker_daemon_json_is_valid_json():
    """
    Verify that daemon.json is syntactically valid JSON that Docker can parse.

    **Validates: Requirements 48.1**
    """
    daemon_json_path = Path(
        "kiwi-descriptions/root/home/gha-executor/.config/docker/daemon.json"
    )
    assert daemon_json_path.exists()

    content = daemon_json_path.read_text()
    # This will raise json.JSONDecodeError if invalid
    config = json.loads(content)
    assert isinstance(config, dict), "daemon.json must be a JSON object"
