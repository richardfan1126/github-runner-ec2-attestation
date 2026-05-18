"""Tests for lockfile-enforced dependency installation (Task 188).

Verifies that:
- build-kiwi-image.sh does NOT use pip3 download with version ranges from pyproject.toml
- build-kiwi-image.sh uses the exported requirements.txt (from uv export --frozen) for wheel downloads
- config.sh uses --require-hashes with the requirements file during installation
- config.sh does NOT use bare *.whl glob patterns without hash verification

Requirements: 12.24
"""
import re
from pathlib import Path

import pytest


class TestLockfileEnforcedDeps:
    """Verify lockfile-enforced dependency installation in build scripts."""

    BUILD_SCRIPT_PATH = Path(__file__).parent.parent / ".github" / "scripts" / "build-kiwi-image.sh"
    CONFIG_SCRIPT_PATH = Path(__file__).parent.parent / "kiwi-descriptions" / "config.sh"

    def test_build_script_does_not_read_deps_from_pyproject(self):
        """build-kiwi-image.sh must NOT extract dependency names from pyproject.toml for pip3 download.

        The old approach used `python3 -c "import tomllib..."` to read version ranges
        from pyproject.toml and passed them to `pip3 download`. This is now replaced
        by using the exported requirements.txt from uv export --frozen.
        """
        content = self.BUILD_SCRIPT_PATH.read_text()

        # Should not contain the tomllib-based dependency extraction
        assert "import tomllib" not in content, (
            "build-kiwi-image.sh should not read dependencies from pyproject.toml via tomllib"
        )

        # Should not have pip3 download with variable expansion from pyproject.toml parsing
        # (e.g., `pip3 download ... ${BINARY_DEPS}` where BINARY_DEPS comes from pyproject.toml)
        assert not re.search(r'pip3 download.*\$\{?BINARY_DEPS\}?', content), (
            "build-kiwi-image.sh should not use ${BINARY_DEPS} variable from pyproject.toml parsing"
        )

        # Should not have the old DEPS extraction pattern
        assert not re.search(r'DEPS=\$\(python3 -c', content), (
            "build-kiwi-image.sh should not extract DEPS from pyproject.toml via python3 -c"
        )

    def test_build_script_uses_exported_requirements_for_download(self):
        """build-kiwi-image.sh must use the exported requirements.txt for wheel downloads.

        The script should use `pip3 download --require-hashes ... -r <requirements-file>`
        to download wheels, ensuring hashes from uv.lock are verified during download.
        """
        content = self.BUILD_SCRIPT_PATH.read_text()

        # Must use uv export --frozen to generate requirements.txt
        assert "uv export --frozen" in content, (
            "build-kiwi-image.sh must use 'uv export --frozen' to export locked dependencies"
        )

        # Must use --no-emit-project to exclude the `-e .` line
        assert "--no-emit-project" in content, (
            "build-kiwi-image.sh must use '--no-emit-project' to exclude editable install line"
        )

        # Must use pip3 download with --require-hashes and -r flag for binary deps
        assert re.search(r'pip3 download.*--require-hashes.*-r', content, re.DOTALL), (
            "build-kiwi-image.sh must use 'pip3 download --require-hashes -r <file>' for binary deps"
        )

        # Must also use --require-hashes for wolfcrypt source download
        wolfcrypt_download_section = re.search(
            r'wolfcrypt source distribution.*?pip3 download(.*?)(?=-d)',
            content,
            re.DOTALL,
        )
        if wolfcrypt_download_section:
            assert "--require-hashes" in wolfcrypt_download_section.group(1), (
                "build-kiwi-image.sh must use --require-hashes for wolfcrypt source download"
            )

    def test_config_sh_uses_require_hashes(self):
        """config.sh must use --require-hashes with the requirements file during installation.

        This ensures that even the offline installation step verifies every wheel's
        integrity against known hashes from the lockfile.
        """
        content = self.CONFIG_SCRIPT_PATH.read_text()

        # Must use pip3.11 install with --require-hashes
        assert re.search(r'pip3\.11 install.*--require-hashes', content), (
            "config.sh must use 'pip3.11 install --require-hashes' for hash-verified installation"
        )

        # Must reference a requirements file with -r flag
        assert re.search(r'pip3\.11 install.*-r\s+\S*requirements', content), (
            "config.sh must use '-r requirements.txt' for installation"
        )

    def test_config_sh_does_not_use_bare_whl_glob(self):
        """config.sh must NOT use bare *.whl glob patterns without hash verification.

        The old approach was: pip3.11 install --no-index --find-links wheels/ wheels/*.whl
        This installed wheels without verifying their integrity. The new approach uses
        --require-hashes -r requirements.txt to verify each wheel against known hashes.
        """
        content = self.CONFIG_SCRIPT_PATH.read_text()

        # Find all pip3.11 install commands
        pip_install_lines = re.findall(r'pip3\.11 install[^\n]+', content)

        for line in pip_install_lines:
            # Should not have bare glob patterns like *.whl or wheels/*.whl
            # without --require-hashes
            if "*.whl" in line:
                assert "--require-hashes" in line, (
                    f"config.sh uses bare *.whl glob without --require-hashes: {line}"
                )

    def test_build_script_computes_wolfcrypt_wheel_hash(self):
        """build-kiwi-image.sh must compute SHA-256 of the built wolfcrypt wheel.

        After building wolfcrypt from source, the script must compute the wheel's
        hash and append it to the final requirements file so config.sh can verify it.
        """
        content = self.BUILD_SCRIPT_PATH.read_text()

        # Must compute sha256sum of the wolfcrypt wheel
        assert "sha256sum" in content and "wolfcrypt" in content, (
            "build-kiwi-image.sh must compute sha256sum of the built wolfcrypt wheel"
        )

        # Must append wolfcrypt hash to requirements file
        assert re.search(r'wolfcrypt==.*--hash=sha256:', content), (
            "build-kiwi-image.sh must append wolfcrypt with --hash=sha256: to requirements file"
        )

    def test_build_script_splits_requirements(self):
        """build-kiwi-image.sh must split requirements into binary and wolfcrypt.

        wolfcrypt needs special handling (source build) while other deps use pre-built wheels.
        """
        content = self.BUILD_SCRIPT_PATH.read_text()

        # Must create separate requirements files for binary and wolfcrypt
        assert "requirements-binary" in content, (
            "build-kiwi-image.sh must create a requirements-binary.txt for binary deps"
        )
        assert "requirements-wolfcrypt" in content or "REQUIREMENTS_WOLFCRYPT" in content, (
            "build-kiwi-image.sh must create a requirements-wolfcrypt.txt for wolfcrypt"
        )
