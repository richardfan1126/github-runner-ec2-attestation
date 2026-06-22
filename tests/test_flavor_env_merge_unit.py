"""Unit tests for the flavor env merge tool (.github/scripts/merge_env.py).

Tests the fixed-precedence merge chain (D7, D8):
  code defaults ◀ flavors/default/env ◀ flavors/<f>/env ◀ bucket-③ inject

Covers task 8.1 (precedence merge) and 8.4 (deny-all build-time gate).
"""
import importlib.util
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "merge_env", REPO_ROOT / ".github" / "scripts" / "merge_env.py"
)
merge_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(merge_env)


_DEFAULT_ENV_VARS = {
    "SERVER_PORT": "8080",
    "MAX_CONCURRENT_EXECUTIONS": "10",
    "EXECUTION_TIMEOUT_SECONDS": "300",
    "MAX_SCRIPT_SIZE_BYTES": "1048576",
    "RATE_LIMIT_PER_IP": "100",
    "RATE_LIMIT_WINDOW_SECONDS": "60",
    "TEMP_STORAGE_PATH": "/var/lib/gha-executor",
    "OUTPUT_RETENTION_HOURS": "24",
    "TPM_ATTEST_PATH": "/usr/bin/nitro-tpm-attest",
    "CONTAINER_MEMORY_LIMIT": "4g",
    "CONTAINER_CPU_LIMIT": "1.0",
}
_FLAVOR_AUTH_VARS = {
    "ALLOWED_REPOSITORIES": "owner/repo",
    "EXPECTED_AUDIENCE": "test-workflow",
}
_CONTAINER_IMAGE = "ghcr.io/owner/repo/myflav"
_CONTAINER_IMAGE_DIGEST = "sha256:" + "b" * 64


def _write_env(path: Path, mapping: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in mapping.items()))


class TestParseEnvFile:
    def test_parses_simple_key_value(self, tmp_path):
        f = tmp_path / "env"
        f.write_text("FOO=bar\nBAZ=123\n")
        assert merge_env.parse_env_file(f) == {"FOO": "bar", "BAZ": "123"}

    def test_skips_hash_comments_and_blank_lines(self, tmp_path):
        f = tmp_path / "env"
        f.write_text("# comment\n\nFOO=bar\n; semicolon comment\n")
        assert merge_env.parse_env_file(f) == {"FOO": "bar"}

    def test_strips_surrounding_double_quotes(self, tmp_path):
        f = tmp_path / "env"
        f.write_text('FOO="bar baz"\n')
        assert merge_env.parse_env_file(f)["FOO"] == "bar baz"

    def test_strips_surrounding_single_quotes(self, tmp_path):
        f = tmp_path / "env"
        f.write_text("FOO='bar baz'\n")
        assert merge_env.parse_env_file(f)["FOO"] == "bar baz"

    def test_unbalanced_quote_kept_verbatim(self, tmp_path):
        f = tmp_path / "env"
        f.write_text('FOO="unbalanced\n')
        assert merge_env.parse_env_file(f)["FOO"] == '"unbalanced'


class TestPrecedenceMerge:
    """8.1: flavor delta overrides shared default; unset key falls through."""

    def test_flavor_delta_overrides_shared_default(self, tmp_path):
        """A key set in flavor/env wins over the same key in default/env."""
        _write_env(tmp_path / "default" / "env",
                   {**_DEFAULT_ENV_VARS, "MAX_CONCURRENT_EXECUTIONS": "5"})
        _write_env(tmp_path / "myflav" / "env",
                   {**_FLAVOR_AUTH_VARS, "MAX_CONCURRENT_EXECUTIONS": "20"})

        out = tmp_path / "effective.env"
        rc = merge_env.main([
            "--flavor", "myflav",
            "--flavors-dir", str(tmp_path),
            "--output", str(out),
        ])
        assert rc == 0
        assert merge_env.parse_env_file(out)["MAX_CONCURRENT_EXECUTIONS"] == "20"

    def test_key_absent_from_flavor_falls_through_to_default(self, tmp_path):
        """A key absent from flavor/env is taken from default/env."""
        _write_env(tmp_path / "default" / "env",
                   {**_DEFAULT_ENV_VARS, "OUTPUT_RETENTION_HOURS": "48"})
        _write_env(tmp_path / "myflav" / "env", _FLAVOR_AUTH_VARS)

        out = tmp_path / "effective.env"
        merge_env.main([
            "--flavor", "myflav",
            "--flavors-dir", str(tmp_path),
            "--output", str(out),
        ])
        assert merge_env.parse_env_file(out)["OUTPUT_RETENTION_HOURS"] == "48"

    def test_bucket1_key_absent_uses_code_default(self, tmp_path, monkeypatch):
        """Bucket-① key absent from both env files → load_config() uses the hardened code default.

        The merged file won't contain the key; the ServerConfig dataclass default fills it in.
        """
        _write_env(tmp_path / "default" / "env", _DEFAULT_ENV_VARS)
        _write_env(tmp_path / "myflav" / "env", _FLAVOR_AUTH_VARS)

        out = tmp_path / "effective.env"
        merge_env.main([
            "--flavor", "myflav",
            "--flavors-dir", str(tmp_path),
            "--output", str(out),
        ])
        merged = merge_env.parse_env_file(out)
        assert "CONTAINER_USER" not in merged  # not set in either file

        # When the merged env is loaded through load_config(), the hardened code defaults apply.
        for k, v in merged.items():
            monkeypatch.setenv(k, v)
        for var in ("CONTAINER_USER", "CONTAINER_ALLOW_ROOT", "CONTAINER_CAP_ADD",
                    "NO_NEW_PRIVILEGES", "CONTAINER_READ_ONLY_ROOTFS", "CONTAINER_TMPFS_SIZE",
                    "CONTAINER_TMPFS_EXEC", "WORKSPACE_MOUNT_MODE", "CONTAINER_NETWORK_MODE"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("CONTAINER_IMAGE", _CONTAINER_IMAGE)
        monkeypatch.setenv("CONTAINER_IMAGE_DIGEST", _CONTAINER_IMAGE_DIGEST)

        from src.config import load_config
        config = load_config()
        assert config.container_user == "65534:65534"
        assert config.no_new_privileges is True
        assert config.container_network_mode == "none"
        assert config.container_read_only_rootfs is True

    def test_bucket3_injection_wins_last(self, tmp_path):
        """--container-image and --container-image-digest are injected last, overriding any prior value."""
        _write_env(tmp_path / "default" / "env", _DEFAULT_ENV_VARS)
        _write_env(tmp_path / "myflav" / "env", _FLAVOR_AUTH_VARS)

        out = tmp_path / "effective.env"
        rc = merge_env.main([
            "--flavor", "myflav",
            "--flavors-dir", str(tmp_path),
            "--output", str(out),
            "--container-image", _CONTAINER_IMAGE,
            "--container-image-digest", _CONTAINER_IMAGE_DIGEST,
        ])
        assert rc == 0
        result = merge_env.parse_env_file(out)
        assert result["CONTAINER_IMAGE"] == _CONTAINER_IMAGE
        assert result["CONTAINER_IMAGE_DIGEST"] == _CONTAINER_IMAGE_DIGEST

    def test_effective_env_reconstructible_from_committed_inputs(self, tmp_path):
        """The effective env is deterministic given default/env + flavor/env + bucket-③ values.

        Running merge twice with the same inputs produces identical output (task 8.1).
        """
        _write_env(tmp_path / "default" / "env", _DEFAULT_ENV_VARS)
        _write_env(tmp_path / "myflav" / "env", _FLAVOR_AUTH_VARS)

        out1 = tmp_path / "run1.env"
        out2 = tmp_path / "run2.env"
        for out in (out1, out2):
            merge_env.main([
                "--flavor", "myflav",
                "--flavors-dir", str(tmp_path),
                "--output", str(out),
                "--container-image", _CONTAINER_IMAGE,
                "--container-image-digest", _CONTAINER_IMAGE_DIGEST,
            ])
        assert out1.read_text() == out2.read_text()


class TestDenyAllBuildGate:
    """8.4: A flavor with no ALLOWED_REPOSITORIES fails the build-time config gate."""

    def test_no_allowed_repositories_fails_when_bucket3_provided(self, tmp_path, monkeypatch):
        _write_env(tmp_path / "default" / "env", _DEFAULT_ENV_VARS)
        _write_env(tmp_path / "myflav" / "env", {})  # no auth keys

        for k in ("ALLOWED_REPOSITORIES", "EXPECTED_AUDIENCE"):
            monkeypatch.delenv(k, raising=False)

        out = tmp_path / "effective.env"
        rc = merge_env.main([
            "--flavor", "myflav",
            "--flavors-dir", str(tmp_path),
            "--output", str(out),
            "--container-image", _CONTAINER_IMAGE,
            "--container-image-digest", _CONTAINER_IMAGE_DIGEST,
        ])
        assert rc != 0, "build-time gate must reject a flavor with no ALLOWED_REPOSITORIES"

    def test_no_expected_audience_fails_when_bucket3_provided(self, tmp_path, monkeypatch):
        _write_env(tmp_path / "default" / "env", _DEFAULT_ENV_VARS)
        _write_env(tmp_path / "myflav" / "env", {"ALLOWED_REPOSITORIES": "owner/repo"})

        for k in ("ALLOWED_REPOSITORIES", "EXPECTED_AUDIENCE"):
            monkeypatch.delenv(k, raising=False)

        out = tmp_path / "effective.env"
        rc = merge_env.main([
            "--flavor", "myflav",
            "--flavors-dir", str(tmp_path),
            "--output", str(out),
            "--container-image", _CONTAINER_IMAGE,
            "--container-image-digest", _CONTAINER_IMAGE_DIGEST,
        ])
        assert rc != 0

    def test_gate_skipped_without_bucket3_args(self, tmp_path, monkeypatch):
        """Without --container-image/--container-image-digest, load_config() is not called
        and the merge exits 0 even without ALLOWED_REPOSITORIES (transitional builds).
        """
        _write_env(tmp_path / "default" / "env", _DEFAULT_ENV_VARS)
        _write_env(tmp_path / "myflav" / "env", {})  # no auth keys at all

        for k in ("ALLOWED_REPOSITORIES", "EXPECTED_AUDIENCE",
                  "CONTAINER_IMAGE", "CONTAINER_IMAGE_DIGEST"):
            monkeypatch.delenv(k, raising=False)

        out = tmp_path / "effective.env"
        rc = merge_env.main([
            "--flavor", "myflav",
            "--flavors-dir", str(tmp_path),
            "--output", str(out),
        ])
        assert rc == 0


class TestDenyAllRuntime:
    """8.4: An executor configured with an empty allowlist refuses to start."""

    def test_empty_allowed_repositories_refuses_startup(self, monkeypatch):
        """ALLOWED_REPOSITORIES='' produces an empty list; load_config() rejects it
        so the executor never accepts any request (deny-all by refusing to start).
        """
        from tests.test_config import _set_base_env
        _set_base_env(monkeypatch)
        monkeypatch.setenv("ALLOWED_REPOSITORIES", "")

        from src.config import ConfigurationError, load_config
        with pytest.raises((ConfigurationError, ValueError)):
            load_config()

    def test_whitespace_only_allowed_repositories_refuses_startup(self, monkeypatch):
        """ALLOWED_REPOSITORIES=' , ,' (only whitespace/commas) → empty list → startup rejected."""
        from tests.test_config import _set_base_env
        _set_base_env(monkeypatch)
        monkeypatch.setenv("ALLOWED_REPOSITORIES", " , , ")

        from src.config import ConfigurationError, load_config
        with pytest.raises((ConfigurationError, ValueError)):
            load_config()
