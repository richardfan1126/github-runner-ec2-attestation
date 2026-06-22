"""Unit tests for the pre-bake env validator (.github/scripts/validate_env.py).

Tests task 8.2: the validator rejects
  (a) hand-set bucket-③ keys (CONTAINER_IMAGE / CONTAINER_IMAGE_DIGEST)
  (b) unknown / misspelled keys (e.g. NO_NEW_PRIVILEGE=true)
and accepts clean committed env files.
"""
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "validate_env", REPO_ROOT / ".github" / "scripts" / "validate_env.py"
)
validate_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_env)


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


class TestValidateFile:
    def test_clean_file_returns_no_errors(self, tmp_path):
        f = _write(tmp_path / "env", "SERVER_PORT=8080\nMAX_CONCURRENT_EXECUTIONS=10\n")
        assert validate_env.validate_file(f) == []

    # --- 8.2a: bucket-③ keys must be rejected ---

    def test_container_image_rejected(self, tmp_path):
        f = _write(tmp_path / "env", "CONTAINER_IMAGE=ubuntu:24.04\n")
        errors = validate_env.validate_file(f)
        assert len(errors) == 1
        assert "CONTAINER_IMAGE" in errors[0]
        assert "bucket-③" in errors[0]

    def test_container_image_digest_rejected(self, tmp_path):
        f = _write(tmp_path / "env", "CONTAINER_IMAGE_DIGEST=sha256:" + "a" * 64 + "\n")
        errors = validate_env.validate_file(f)
        assert len(errors) == 1
        assert "CONTAINER_IMAGE_DIGEST" in errors[0]

    def test_migration_leftover_in_default_env_caught(self, tmp_path):
        """A CONTAINER_IMAGE left in flavors/default/env after migration is flagged (task 8.2a)."""
        f = _write(tmp_path / "env", "SERVER_PORT=8080\nCONTAINER_IMAGE=ubuntu:24.04\n")
        errors = validate_env.validate_file(f)
        assert any("CONTAINER_IMAGE" in e for e in errors)

    # --- 8.2b: unknown / misspelled keys must be rejected ---

    def test_misspelled_key_no_new_privilege_rejected(self, tmp_path):
        """NO_NEW_PRIVILEGE (missing trailing S) is not in RECOGNIZED_ENV_KEYS → rejected."""
        f = _write(tmp_path / "env", "NO_NEW_PRIVILEGE=true\n")
        errors = validate_env.validate_file(f)
        assert len(errors) == 1
        assert "NO_NEW_PRIVILEGE" in errors[0]
        assert "unknown key" in errors[0]

    def test_arbitrary_unknown_key_rejected(self, tmp_path):
        f = _write(tmp_path / "env", "TOTALLY_UNKNOWN_KEY=value\n")
        errors = validate_env.validate_file(f)
        assert len(errors) == 1
        assert "TOTALLY_UNKNOWN_KEY" in errors[0]

    def test_correctly_spelled_key_passes(self, tmp_path):
        """NO_NEW_PRIVILEGES (correctly spelled) is in RECOGNIZED_ENV_KEYS and passes."""
        f = _write(tmp_path / "env", "NO_NEW_PRIVILEGES=true\n")
        assert validate_env.validate_file(f) == []

    def test_all_recognized_security_keys_pass(self, tmp_path):
        """Every bucket-① key in RECOGNIZED_ENV_KEYS (except bucket-③) passes."""
        content = (
            "CONTAINER_USER=65534:65534\n"
            "CONTAINER_ALLOW_ROOT=false\n"
            "NO_NEW_PRIVILEGES=true\n"
            "CONTAINER_READ_ONLY_ROOTFS=true\n"
            "CONTAINER_TMPFS_SIZE=256m\n"
            "CONTAINER_TMPFS_EXEC=false\n"
            "WORKSPACE_MOUNT_MODE=ro\n"
            "CONTAINER_NETWORK_MODE=none\n"
        )
        f = _write(tmp_path / "env", content)
        assert validate_env.validate_file(f) == []

    # --- Error reporting ---

    def test_all_errors_reported_in_one_pass(self, tmp_path):
        """All violations in a file are collected before returning (no early exit)."""
        f = _write(tmp_path / "env",
                   "CONTAINER_IMAGE=ubuntu:24.04\nBAD_KEY=1\nCONTAINER_IMAGE_DIGEST=sha256:abc\n")
        errors = validate_env.validate_file(f)
        assert len(errors) == 3

    def test_comment_lines_not_treated_as_keys(self, tmp_path):
        f = _write(tmp_path / "env", "# CONTAINER_IMAGE=ubuntu\n; CONTAINER_IMAGE=ubuntu\n")
        assert validate_env.validate_file(f) == []

    def test_nonexistent_file_returns_error(self, tmp_path):
        errors = validate_env.validate_file(tmp_path / "nonexistent")
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_error_includes_line_number(self, tmp_path):
        """Error messages include the file path and line number for fast diagnosis."""
        f = _write(tmp_path / "env", "SERVER_PORT=8080\nCONTAINER_IMAGE=ubuntu\n")
        errors = validate_env.validate_file(f)
        assert len(errors) == 1
        assert ":2:" in errors[0]  # line 2


class TestMain:
    def test_clean_file_exits_0(self, tmp_path):
        f = _write(tmp_path / "env", "SERVER_PORT=8080\n")
        assert validate_env.main([str(f)]) == 0

    def test_bucket3_key_exits_nonzero(self, tmp_path):
        f = _write(tmp_path / "env", "CONTAINER_IMAGE=ubuntu\n")
        assert validate_env.main([str(f)]) != 0

    def test_unknown_key_exits_nonzero(self, tmp_path):
        f = _write(tmp_path / "env", "NO_NEW_PRIVILEGE=true\n")
        assert validate_env.main([str(f)]) != 0

    def test_multiple_files_all_checked_before_failing(self, tmp_path):
        """A clean file and a bad file: both are checked, exit is still non-zero."""
        clean = _write(tmp_path / "clean.env", "SERVER_PORT=8080\n")
        bad = _write(tmp_path / "bad.env", "CONTAINER_IMAGE=ubuntu\n")
        assert validate_env.main([str(clean), str(bad)]) != 0

    def test_no_args_exits_nonzero(self):
        assert validate_env.main([]) != 0

    def test_both_files_clean_exits_0(self, tmp_path):
        a = _write(tmp_path / "a.env", "SERVER_PORT=8080\n")
        b = _write(tmp_path / "b.env", "NO_NEW_PRIVILEGES=false\n")
        assert validate_env.main([str(a), str(b)]) == 0
