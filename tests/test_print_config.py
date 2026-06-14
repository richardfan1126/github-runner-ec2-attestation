"""Tests for the build-time configuration summary helper (.github/scripts/print_config.py).

The helper resolves a baked-in EnvironmentFile through the application's own
load_config() (single source of truth) and prints every ServerConfig field as a
GitHub-Flavored Markdown table. These tests pin FR-030 / SC-007:

  (a) a minimal valid env file -> exit 0, with a table row for *every* field of
      ServerConfig (so a dropped field can't regress silently);
  (b) a missing required var and an invalid value -> non-zero exit, stderr names
      the offending variable;
  (c) the real baked env file shipped in the image resolves cleanly (exit 0).
"""
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.config import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / ".github" / "scripts" / "print_config.py"
BAKED_ENV = REPO_ROOT / "kiwi-descriptions" / "root" / "etc" / "github-actions-remote-executor" / "env"

# Minimal required vars (mirrors tests/test_config.py _set_base_env); digest-pinned so validate() passes.
_REQUIRED_ENV = {
    "SERVER_PORT": "8080",
    "MAX_CONCURRENT_EXECUTIONS": "10",
    "EXECUTION_TIMEOUT_SECONDS": "300",
    "MAX_SCRIPT_SIZE_BYTES": "1048576",
    "RATE_LIMIT_PER_IP": "100",
    "RATE_LIMIT_WINDOW_SECONDS": "60",
    "TEMP_STORAGE_PATH": "/var/lib/gha-executor",
    "OUTPUT_RETENTION_HOURS": "24",
    "TPM_ATTEST_PATH": "/usr/bin/nitro-tpm-attest",
    "ALLOWED_REPOSITORIES": "owner/repo",
    "EXPECTED_AUDIENCE": "test-workflow",
    "CONTAINER_IMAGE": "ubuntu:24.04",
    "CONTAINER_IMAGE_DIGEST": "sha256:" + "a" * 64,
    "CONTAINER_MEMORY_LIMIT": "4g",
    "CONTAINER_CPU_LIMIT": "1.0",
}


def _write_env(path: Path, mapping: dict) -> Path:
    path.write_text("".join(f"{k}={v}\n" for k, v in mapping.items()))
    return path


def _run(env_file: Path):
    """Invoke the helper in an isolated environment (only the env file should resolve)."""
    return subprocess.run(
        [sys.executable, str(HELPER), "--env-file", str(env_file)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        # Minimal environment so ambient config vars can't leak into the resolution;
        # the helper adds the repo root to sys.path itself, so PATH is enough.
        env={"PATH": os.environ.get("PATH", "")},
    )


def test_valid_env_file_prints_table_for_every_field(tmp_path):
    """(a) Minimal valid env -> exit 0 and a Markdown table row for every ServerConfig field."""
    env_file = _write_env(tmp_path / "good.env", _REQUIRED_ENV)
    result = _run(env_file)

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    out = result.stdout
    # Header row present.
    assert "| Setting | Value |" in out
    # Every field of ServerConfig must appear as a row label — a dropped field regresses loudly.
    for f in dataclasses.fields(ServerConfig):
        assert f"| {f.name} |" in out, f"missing table row for field {f.name!r}"
    # The eight container-security defaults fall back even though absent from the file.
    assert "| container_user | 65534:65534 |" in out
    assert "| container_network_mode | none |" in out


def test_missing_required_var_exits_nonzero_naming_variable(tmp_path):
    """(b) A missing required var -> non-zero exit, stderr names the offending variable."""
    incomplete = dict(_REQUIRED_ENV)
    del incomplete["SERVER_PORT"]
    env_file = _write_env(tmp_path / "missing.env", incomplete)
    result = _run(env_file)

    assert result.returncode != 0
    assert "SERVER_PORT" in result.stderr


def test_invalid_value_exits_nonzero_naming_variable(tmp_path):
    """(b) An invalid value -> non-zero exit, stderr names the offending variable."""
    bad = dict(_REQUIRED_ENV)
    bad["CONTAINER_NETWORK_MODE"] = "nat"
    env_file = _write_env(tmp_path / "bad.env", bad)
    result = _run(env_file)

    assert result.returncode != 0
    assert "CONTAINER_NETWORK_MODE" in result.stderr


def test_double_quoted_value_is_unquoted(tmp_path):
    """Phase 9: a double-quoted value resolves to the *unquoted* string (as systemd would set it)."""
    env = dict(_REQUIRED_ENV)
    env["EXPECTED_AUDIENCE"] = '"test-workflow"'  # operator wraps the value in quotes
    env_file = _write_env(tmp_path / "dquote.env", env)
    result = _run(env_file)

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    # The surrounding quotes are stripped — matching what real systemd EnvironmentFile= does.
    assert "| expected_audience | test-workflow |" in result.stdout
    assert '"test-workflow"' not in result.stdout


def test_single_quoted_value_is_unquoted(tmp_path):
    """Phase 9: a single-quoted value resolves to the *unquoted* string."""
    env = dict(_REQUIRED_ENV)
    env["CONTAINER_MEMORY_LIMIT"] = "'4g'"
    env_file = _write_env(tmp_path / "squote.env", env)
    result = _run(env_file)

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert "| container_memory_limit | 4g |" in result.stdout
    assert "'4g'" not in result.stdout


def test_unbalanced_quotes_kept_verbatim(tmp_path):
    """Phase 9: only a *matched* surrounding pair is stripped; a lone quote stays verbatim."""
    env = dict(_REQUIRED_ENV)
    env["EXPECTED_AUDIENCE"] = '"unbalanced'  # leading quote only
    env_file = _write_env(tmp_path / "unbalanced.env", env)
    result = _run(env_file)

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert '| expected_audience | "unbalanced |' in result.stdout


def test_semicolon_comment_line_ignored(tmp_path):
    """Phase 9: a line whose first non-whitespace char is ';' is a comment and is not parsed."""
    content = "".join(f"{k}={v}\n" for k, v in _REQUIRED_ENV.items())
    # systemd treats ';' lines as comments; this one carries an '=' to prove it is skipped,
    # not split into a variable.
    content += "  ; CONTAINER_NETWORK_MODE=nat (a note, not a setting)\n"
    env_file = tmp_path / "semicolon.env"
    env_file.write_text(content)
    result = _run(env_file)

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    # The commented line must not relax the network mode away from its default.
    assert "| container_network_mode | none |" in result.stdout


def test_simple_and_hash_comment_behavior_unchanged(tmp_path):
    """Phase 9: plain KEY=value and '#'-comments still behave as before."""
    content = "# leading comment\n"
    content += "".join(f"{k}={v}\n" for k, v in _REQUIRED_ENV.items())
    content += "# CONTAINER_NETWORK_MODE=nat (commented out)\n"
    env_file = tmp_path / "hash.env"
    env_file.write_text(content)
    result = _run(env_file)

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert "| expected_audience | test-workflow |" in result.stdout
    assert "| container_network_mode | none |" in result.stdout


@pytest.mark.skipif(not BAKED_ENV.exists(), reason="baked env file not present")
def test_real_baked_env_file_resolves_cleanly():
    """(c) The real env file shipped in the AMI resolves to a full table (exit 0)."""
    result = _run(BAKED_ENV)

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert "| Setting | Value |" in result.stdout
    for f in dataclasses.fields(ServerConfig):
        assert f"| {f.name} |" in result.stdout, f"missing table row for field {f.name!r}"
