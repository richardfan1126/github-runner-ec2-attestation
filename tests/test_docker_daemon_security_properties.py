"""
Property-based tests for Docker Daemon Security Configuration.

These tests validate that the KIWI image includes a hardened Docker daemon
configuration at /etc/docker/daemon.json with user-namespace remapping and
a restrictive seccomp profile, as specified in Requirement 48.
"""

import json
from pathlib import Path


def test_docker_daemon_security_configuration():
    """
    Property 164: Docker Daemon Security Configuration

    For any KIWI image build, the image should include a daemon.json at
    /etc/docker/daemon.json with user-namespace remapping and a restrictive
    seccomp profile.

    **Validates: Requirements 48.1, 48.2, 48.3**
    """
    daemon_json_path = Path("kiwi-descriptions/root/etc/docker/daemon.json")

    # Requirement 48.1: daemon.json must exist at /etc/docker/daemon.json
    assert daemon_json_path.exists(), (
        "daemon.json must exist at kiwi-descriptions/root/etc/docker/daemon.json "
        "so it is included in the KIWI image at /etc/docker/daemon.json"
    )

    content = daemon_json_path.read_text()
    config = json.loads(content)

    # Requirement 48.2: user-namespace remapping must be enabled
    assert "userns-remap" in config, (
        "daemon.json must include 'userns-remap' to isolate container root "
        "from host root"
    )
    assert config["userns-remap"] == "default", (
        "userns-remap must be set to 'default' to enable automatic "
        "user-namespace remapping"
    )

    # Requirement 48.3: restrictive seccomp profile must be set
    assert "seccomp-profile" in config, (
        "daemon.json must include 'seccomp-profile' to restrict system calls "
        "available to containers"
    )
    assert config["seccomp-profile"], (
        "seccomp-profile must reference a non-empty profile path"
    )

    # Verify no-new-privileges is set (defense in depth)
    assert config.get("no-new-privileges") is True, (
        "daemon.json must set 'no-new-privileges' to true to prevent "
        "privilege escalation inside containers"
    )


def test_docker_daemon_json_is_valid_json():
    """
    Verify that daemon.json is syntactically valid JSON that Docker can parse.

    **Validates: Requirements 48.1**
    """
    daemon_json_path = Path("kiwi-descriptions/root/etc/docker/daemon.json")
    assert daemon_json_path.exists()

    content = daemon_json_path.read_text()
    # This will raise json.JSONDecodeError if invalid
    config = json.loads(content)
    assert isinstance(config, dict), "daemon.json must be a JSON object"
