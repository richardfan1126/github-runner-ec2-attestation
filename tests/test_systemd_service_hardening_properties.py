"""
Property-based tests for Systemd Service Hardening.

These tests validate that the systemd service unit for the
github-actions-remote-executor is hardened with security directives,
and that the env configuration uses a safe TEMP_STORAGE_PATH,
as specified in Requirement 49.
"""

import configparser
import io
from pathlib import Path


def _parse_systemd_unit(path: Path) -> dict[str, dict[str, str]]:
    """Parse a systemd unit file into a dict of {section: {key: value}}."""
    parser = configparser.ConfigParser(interpolation=None)
    # configparser requires at least one section header; systemd files have them.
    # Preserve case of keys (systemd keys are case-sensitive).
    parser.optionxform = str  # type: ignore[assignment]
    parser.read_string(path.read_text())
    return {section: dict(parser[section]) for section in parser.sections()}


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a shell-style KEY=VALUE env file, ignoring comments and blanks."""
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


SERVICE_PATH = Path(
    "kiwi-descriptions/root/etc/systemd/system/"
    "github-actions-remote-executor.service"
)
ENV_PATH = Path("flavors/default/env")


def test_systemd_service_hardening():
    """
    Property 165: Systemd Service Hardening

    For any KIWI image build, the systemd service unit for the
    github-actions-remote-executor must be hardened with security
    directives that limit the blast radius of a container breakout,
    and the env file must use a TEMP_STORAGE_PATH outside /tmp.

    **Validates: Requirements 49.1, 49.2, 49.3, 49.4, 49.5, 49.6, 49.7, 49.8**
    """
    assert SERVICE_PATH.exists(), (
        f"Systemd service file must exist at {SERVICE_PATH}"
    )
    assert ENV_PATH.exists(), (
        f"Env configuration file must exist at {ENV_PATH}"
    )

    unit = _parse_systemd_unit(SERVICE_PATH)
    service = unit.get("Service", {})

    # Requirement 49.1: NoNewPrivileges must be true
    assert service.get("NoNewPrivileges") == "true", (
        "NoNewPrivileges must be set to true to prevent privilege escalation"
    )

    # Requirement 48.6: Service must run as dedicated gha-executor user
    assert service.get("User") == "gha-executor", (
        "User must be set to gha-executor for rootless Docker"
    )
    assert service.get("Group") == "gha-executor", (
        "Group must be set to gha-executor for rootless Docker"
    )

    # Requirement 49.2: PrivateTmp must NOT be set to true
    private_tmp = service.get("PrivateTmp", "false").lower()
    assert private_tmp != "true", (
        "PrivateTmp must NOT be true — it would make TEMP_STORAGE_PATH "
        "invisible to the Docker daemon, breaking container bind mounts"
    )

    # Requirement 49.3: ProtectSystem must be strict
    assert service.get("ProtectSystem") == "strict", (
        "ProtectSystem must be set to strict"
    )

    # Requirement 49.4 / 48.6: ProtectHome must be read-only
    # (changed from true to read-only because the service runs as gha-executor
    # and needs to read ~gha-executor/.config/docker/daemon.json for rootless Docker)
    assert service.get("ProtectHome") == "read-only", (
        "ProtectHome must be set to read-only (service needs read access to "
        "gha-executor home for rootless Docker config)"
    )

    # Requirement 49.5: RestrictAddressFamilies
    restrict_af = service.get("RestrictAddressFamilies", "")
    for family in ("AF_INET", "AF_INET6", "AF_UNIX", "AF_NETLINK"):
        assert family in restrict_af, (
            f"RestrictAddressFamilies must include {family}"
        )

    # Requirement 49.6: ReadWritePaths includes only required directories
    # Requirement 48.7: /var/run/docker.sock must NOT be in ReadWritePaths
    # (rootless Docker uses /run/user/{uid}/docker.sock instead)
    rw_paths = service.get("ReadWritePaths", "")
    assert "/var/lib/gha-executor" in rw_paths, (
        "ReadWritePaths must include the TEMP_STORAGE_PATH (/var/lib/gha-executor)"
    )
    assert "/var/run/docker.sock" not in rw_paths, (
        "ReadWritePaths must NOT include /var/run/docker.sock — rootless Docker "
        "uses /run/user/{uid}/docker.sock instead"
    )

    # Requirement 49.7 & 49.8: env file TEMP_STORAGE_PATH
    env = _parse_env_file(ENV_PATH)
    temp_path = env.get("TEMP_STORAGE_PATH", "")

    assert not temp_path.startswith("/tmp"), (
        "TEMP_STORAGE_PATH must be outside /tmp to avoid PrivateTmp conflicts "
        "and ensure Docker bind mounts resolve correctly"
    )
    assert temp_path == "/var/lib/gha-executor", (
        "TEMP_STORAGE_PATH must be set to /var/lib/gha-executor"
    )
