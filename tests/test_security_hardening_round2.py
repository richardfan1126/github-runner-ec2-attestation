"""Tests for security hardening round 2 changes (Task 184).

Covers:
- Digest pinning (184.1): artifact refs without @sha256: rejected
- Credential isolation (184.3): git clone uses GIT_ASKPASS, no token in argv
- Symlink validation (184.4): symlink script paths rejected
- Strict nonce validation (184.5): type, length, format checks
- Strict base64 validation (184.6): malformed base64 rejected
- CI pinning (184.7, 184.8): workflow actions SHA-pinned, Dockerfile digest-pinned
- Script_env deny-list (184.10): dangerous env vars rejected
- Package minimization (184.11): removed packages not in appliance.kiwi
- Log sanitization (184.12): tokens, paths, control chars redacted

Requirements: 3.20, 7.19, 11.17, 15.23, 45.14, 52.11, 54.9
"""
import os
import re
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.config import ServerConfig
from src.logging_config import LogSanitizer, sanitize_log_message, truncate_field, sanitize_nonce_for_logging
from src.models import OIDCValidationResult
from src.repository import RepositoryClient, GitHubAPIError
from src.server import create_app, _validate_nonce_strict
from tests.encryption_test_helpers import (
    EncryptionTestContext,
    make_encrypted_execute_request,
    decrypt_execute_response,
    assert_encrypted_error,
)
from tests.mock_docker import create_mock_docker_client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_OIDC_RESULT = OIDCValidationResult(
    valid=True,
    status_code=200,
    error_message=None,
    claims={
        "repository": "owner/repo",
        "iss": "https://token.actions.githubusercontent.com",
        "aud": "https://example.com",
    },
)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def test_config(temp_dir):
    return ServerConfig(
        port=8080,
        max_concurrent_executions=10,
        execution_timeout_seconds=5,
        max_script_size_bytes=1024 * 1024,
        rate_limit_per_ip=100,
        rate_limit_window_seconds=60,
        temp_storage_path=temp_dir,
        output_retention_hours=1,
        tpm_attest_path="/usr/bin/nitro-tpm-attest",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )


@pytest.fixture
def encryption_ctx():
    return EncryptionTestContext()


@pytest.fixture
def app(test_config, temp_dir, encryption_ctx):
    with patch('requests.Session') as mock_session_class, \
         patch('src.repository.subprocess.run') as mock_git_run, \
         patch('src.attestation.subprocess.run') as mock_attest:

        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_session.headers = {}
        mock_session.get.return_value = Mock(status_code=200)

        mock_git_run.return_value = Mock(returncode=0, stdout="", stderr="")
        mock_attest.return_value = Mock(returncode=0, stdout=b'mock_attestation_cbor_data')

        application = create_app(
            test_config,
            docker_client=create_mock_docker_client(),
            encryption_manager=encryption_ctx.encryption_manager,
        )
        application.state.request_validator.validate_oidc_token_from_body = Mock(
            return_value=VALID_OIDC_RESULT
        )

        from src.models import CloneResult

        def mock_clone_repo(repo_url, commit, token):
            clone_dir = tempfile.mkdtemp(dir=temp_dir, prefix="clone_")
            return CloneResult(clone_path=clone_dir, script_path="")

        def mock_validate_script_exists(clone_path, script_path):
            full_path = os.path.join(clone_path, script_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write('#!/bin/bash\necho "hello"\nexit 0')
            os.chmod(full_path, 0o755)
            return True

        application.state.repository_client.clone_repo = Mock(side_effect=mock_clone_repo)
        application.state.repository_client.validate_script_exists = Mock(
            side_effect=mock_validate_script_exists
        )

        yield application


@pytest.fixture
def client(app):
    return TestClient(app)


def _base_request_data(**overrides):
    data = {
        "repository_url": "https://github.com/owner/repo",
        "commit_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "script_path": "scripts/test.sh",
        "github_token": "ghp_test_token_value",
        "oidc_token": "valid.oidc.token",
    }
    data.update(overrides)
    return data


def _post_execute(client, encryption_ctx, request_data):
    body = make_encrypted_execute_request(request_data, encryption_ctx)
    return client.post("/execute", json=body)


# ===========================================================================
# 184.1 – Digest Pinning Tests
# ===========================================================================

class TestDigestPinning:
    """Verify artifact refs without @sha256: are rejected; tag-only refs rejected."""

    def test_artifact_ref_without_digest_rejected(self):
        """Artifact reference without @sha256: raises ValueError."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        # Import the function directly from the script
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location(
            "build_ami",
            str(Path(__file__).parent.parent / "scripts" / "build-ami.py"),
        )
        build_ami = module_from_spec(spec)
        spec.loader.exec_module(build_ami)

        # Tag-only reference (no digest) must be rejected
        with pytest.raises(ValueError, match="digest-pinned"):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo/package:latest")

    def test_artifact_ref_with_digest_accepted(self):
        """Artifact reference with @sha256:<hex64> is accepted."""
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location(
            "build_ami",
            str(Path(__file__).parent.parent / "scripts" / "build-ami.py"),
        )
        build_ami = module_from_spec(spec)
        spec.loader.exec_module(build_ami)

        digest = "a" * 64
        ref = f"ghcr.io/owner/repo/package:v1.0@sha256:{digest}"
        # Should not raise
        build_ami.validate_artifact_reference(ref)

    def test_tag_only_ref_rejected(self):
        """Tag-only reference (no @sha256:) is rejected."""
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location(
            "build_ami",
            str(Path(__file__).parent.parent / "scripts" / "build-ami.py"),
        )
        build_ami = module_from_spec(spec)
        spec.loader.exec_module(build_ami)

        with pytest.raises(ValueError, match="digest-pinned"):
            build_ami.validate_artifact_reference("ghcr.io/owner/repo/package:v2.0")

    def test_verification_and_pull_use_digest(self):
        """extract_digest_from_artifact_ref and get_digest_pinned_ref use digest."""
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location(
            "build_ami",
            str(Path(__file__).parent.parent / "scripts" / "build-ami.py"),
        )
        build_ami = module_from_spec(spec)
        spec.loader.exec_module(build_ami)

        digest = "b" * 64
        ref = f"ghcr.io/owner/repo/package:v1.0@sha256:{digest}"

        extracted = build_ami.extract_digest_from_artifact_ref(ref)
        assert extracted == f"sha256:{digest}"

        pinned_ref = build_ami.get_digest_pinned_ref(ref)
        # Tag should be stripped, only digest remains
        assert ":v1.0" not in pinned_ref
        assert f"@sha256:{digest}" in pinned_ref
        assert pinned_ref == f"ghcr.io/owner/repo/package@sha256:{digest}"


# ===========================================================================
# 184.3 – Credential Isolation Tests
# ===========================================================================

class TestCredentialIsolation:
    """Verify git clone uses GIT_ASKPASS; token not in subprocess argv."""

    def test_clone_subprocess_args_do_not_contain_token(self, temp_dir):
        """git clone subprocess args must not contain the GitHub token."""
        repo_client = RepositoryClient(temp_dir)
        token = "ghp_secret_token_12345"

        with patch('src.repository.subprocess.run') as mock_run, \
             patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            mock_session.headers = {}
            mock_session.get.return_value = Mock(status_code=200)

            # Make clone succeed, fetch succeed, checkout succeed, set-url succeed
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            # Mock os.listdir to return non-empty
            with patch('os.listdir', return_value=['.git', 'script.sh']):
                with patch('shutil.rmtree'):
                    try:
                        repo_client.clone_repo(
                            "https://github.com/owner/repo",
                            "a" * 40,
                            token,
                        )
                    except Exception:
                        pass  # May fail due to mocking, but we can check calls

            # Check all subprocess.run calls — none should have the token in args
            for call in mock_run.call_args_list:
                args = call[0][0] if call[0] else call[1].get('args', [])
                args_str = " ".join(str(a) for a in args)
                assert token not in args_str, (
                    f"Token found in subprocess args: {args_str}"
                )

    def test_askpass_helper_is_cleaned_up(self, temp_dir):
        """GIT_ASKPASS helper script is removed after clone completes."""
        repo_client = RepositoryClient(temp_dir)
        token = "ghp_cleanup_test_token"

        created_helpers = []

        original_mkstemp = tempfile.mkstemp

        def tracking_mkstemp(*args, **kwargs):
            fd, path = original_mkstemp(*args, **kwargs)
            if "git_askpass_" in path:
                created_helpers.append(path)
            return fd, path

        with patch('src.repository.subprocess.run') as mock_run, \
             patch('requests.Session') as mock_session_class, \
             patch('src.repository.tempfile.mkstemp', side_effect=tracking_mkstemp):
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            mock_session.headers = {}
            mock_session.get.return_value = Mock(status_code=200)

            # Make clone fail to trigger cleanup
            mock_run.return_value = Mock(
                returncode=1, stdout="", stderr="authentication failed"
            )

            with pytest.raises(GitHubAPIError):
                repo_client.clone_repo(
                    "https://github.com/owner/repo",
                    "a" * 40,
                    token,
                )

        # Helper should have been created and then cleaned up
        assert len(created_helpers) > 0, "No GIT_ASKPASS helper was created"
        for helper_path in created_helpers:
            assert not os.path.exists(helper_path), (
                f"GIT_ASKPASS helper was not cleaned up: {helper_path}"
            )

    def test_clone_uses_git_askpass_env(self, temp_dir):
        """git clone subprocess receives GIT_ASKPASS in its environment."""
        repo_client = RepositoryClient(temp_dir)
        token = "ghp_env_test_token"

        with patch('src.repository.subprocess.run') as mock_run, \
             patch('requests.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session
            mock_session.headers = {}
            mock_session.get.return_value = Mock(status_code=200)

            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            with patch('os.listdir', return_value=['.git', 'file.py']):
                with patch('shutil.rmtree'):
                    try:
                        repo_client.clone_repo(
                            "https://github.com/owner/repo",
                            "a" * 40,
                            token,
                        )
                    except Exception:
                        pass

            # The first subprocess.run call (clone) should have GIT_ASKPASS in env
            if mock_run.call_args_list:
                clone_call = mock_run.call_args_list[0]
                env = clone_call[1].get('env') or (clone_call[0][1] if len(clone_call[0]) > 1 else None)
                if env is None and 'env' in (clone_call[1] or {}):
                    env = clone_call[1]['env']
                # env might be passed as keyword arg
                call_kwargs = clone_call[1] if clone_call[1] else {}
                env = call_kwargs.get('env', {})
                assert "GIT_ASKPASS" in env, (
                    f"GIT_ASKPASS not found in clone subprocess env: {list(env.keys())}"
                )


# ===========================================================================
# 184.4 – Symlink Validation Tests
# ===========================================================================

class TestSymlinkValidation:
    """Verify symlink script paths are rejected; paths escaping clone dir rejected."""

    def test_symlink_script_path_rejected(self, temp_dir):
        """Script path that is a symlink is rejected with 400."""
        repo_client = RepositoryClient(temp_dir)

        # Create a clone directory with a symlink script
        clone_dir = os.path.join(temp_dir, "clone")
        os.makedirs(clone_dir)

        # Create a real file outside clone dir
        outside_file = os.path.join(temp_dir, "outside_secret.sh")
        with open(outside_file, "w") as f:
            f.write("#!/bin/bash\necho secret")

        # Create a symlink inside clone dir pointing outside
        symlink_path = os.path.join(clone_dir, "evil.sh")
        os.symlink(outside_file, symlink_path)

        with pytest.raises(GitHubAPIError) as exc_info:
            repo_client.validate_script_exists(clone_dir, "evil.sh")

        assert exc_info.value.status_code == 400
        assert "symlink" in exc_info.value.message.lower()

    def test_path_escaping_clone_dir_via_symlink_rejected(self, temp_dir):
        """Script path that resolves outside clone dir via symlinked directory is rejected."""
        repo_client = RepositoryClient(temp_dir)

        # Create clone directory
        clone_dir = os.path.join(temp_dir, "clone")
        os.makedirs(clone_dir)

        # Create a directory outside clone
        outside_dir = os.path.join(temp_dir, "outside")
        os.makedirs(outside_dir)
        with open(os.path.join(outside_dir, "secret.sh"), "w") as f:
            f.write("#!/bin/bash\necho secret")

        # Create a symlinked directory inside clone pointing outside
        symlinked_dir = os.path.join(clone_dir, "subdir")
        os.symlink(outside_dir, symlinked_dir)

        # The script path goes through the symlinked directory
        with pytest.raises(GitHubAPIError) as exc_info:
            repo_client.validate_script_exists(clone_dir, "subdir/secret.sh")

        assert exc_info.value.status_code == 400

    def test_valid_script_path_accepted(self, temp_dir):
        """Non-symlink script path within clone dir is accepted."""
        repo_client = RepositoryClient(temp_dir)

        clone_dir = os.path.join(temp_dir, "clone")
        os.makedirs(os.path.join(clone_dir, "scripts"))
        script_path = os.path.join(clone_dir, "scripts", "build.sh")
        with open(script_path, "w") as f:
            f.write("#!/bin/bash\necho ok")

        result = repo_client.validate_script_exists(clone_dir, "scripts/build.sh")
        assert result is True

    def test_nonexistent_script_returns_404(self, temp_dir):
        """Non-existent script path raises 404."""
        repo_client = RepositoryClient(temp_dir)

        clone_dir = os.path.join(temp_dir, "clone")
        os.makedirs(clone_dir)

        with pytest.raises(GitHubAPIError) as exc_info:
            repo_client.validate_script_exists(clone_dir, "nonexistent.sh")

        assert exc_info.value.status_code == 404


# ===========================================================================
# 184.5 – Strict Nonce Validation Tests
# ===========================================================================

class TestStrictNonceValidation:
    """Verify non-string nonce rejected; length bounds enforced; invalid chars rejected."""

    def test_non_string_nonce_int_rejected(self, client, encryption_ctx):
        """Integer nonce is rejected with encrypted error."""
        request_data = _base_request_data(nonce=12345)
        response = _post_execute(client, encryption_ctx, request_data)
        assert_encrypted_error(response, encryption_ctx.shared_key, "invalid_nonce", 400)

    def test_non_string_nonce_bool_rejected(self, client, encryption_ctx):
        """Boolean nonce is rejected with encrypted error."""
        request_data = _base_request_data(nonce=True)
        response = _post_execute(client, encryption_ctx, request_data)
        assert_encrypted_error(response, encryption_ctx.shared_key, "invalid_nonce", 400)

    def test_non_string_nonce_list_rejected(self, client, encryption_ctx):
        """List nonce is rejected with encrypted error."""
        request_data = _base_request_data(nonce=["a", "b"])
        response = _post_execute(client, encryption_ctx, request_data)
        assert_encrypted_error(response, encryption_ctx.shared_key, "invalid_nonce", 400)

    def test_nonce_too_short_rejected(self, client, encryption_ctx):
        """Nonce shorter than 16 characters is rejected."""
        request_data = _base_request_data(nonce="short")  # 5 chars
        response = _post_execute(client, encryption_ctx, request_data)
        assert_encrypted_error(response, encryption_ctx.shared_key, "invalid_nonce", 400)

    def test_nonce_too_long_rejected(self, client, encryption_ctx):
        """Nonce longer than 256 characters is rejected."""
        request_data = _base_request_data(nonce="a" * 257)
        response = _post_execute(client, encryption_ctx, request_data)
        assert_encrypted_error(response, encryption_ctx.shared_key, "invalid_nonce", 400)

    def test_nonce_with_control_chars_rejected(self, client, encryption_ctx):
        """Nonce containing control characters is rejected."""
        # 20 chars but contains a null byte
        request_data = _base_request_data(nonce="valid-nonce-\x00-pad12")
        response = _post_execute(client, encryption_ctx, request_data)
        assert_encrypted_error(response, encryption_ctx.shared_key, "invalid_nonce", 400)

    def test_nonce_with_spaces_rejected(self, client, encryption_ctx):
        """Nonce containing spaces is rejected (not URL-safe)."""
        request_data = _base_request_data(nonce="nonce with spaces!!")
        response = _post_execute(client, encryption_ctx, request_data)
        assert_encrypted_error(response, encryption_ctx.shared_key, "invalid_nonce", 400)

    def test_valid_nonce_accepted(self, client, encryption_ctx):
        """Valid URL-safe nonce of proper length is accepted."""
        request_data = _base_request_data(nonce="valid-nonce_123.test~abc")
        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert "execution_id" in data

    def test_nonce_exactly_16_chars_accepted(self, client, encryption_ctx):
        """Nonce of exactly 16 characters (minimum) is accepted."""
        request_data = _base_request_data(nonce="a" * 16)
        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert "execution_id" in data

    def test_nonce_exactly_256_chars_accepted(self, client, encryption_ctx):
        """Nonce of exactly 256 characters (maximum) is accepted."""
        request_data = _base_request_data(nonce="b" * 256)
        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert "execution_id" in data


# ===========================================================================
# 184.6 – Strict Base64 Validation Tests
# ===========================================================================

class TestStrictBase64Validation:
    """Verify malformed base64 in encrypted_payload rejected with HTTP 400."""

    def test_malformed_base64_encrypted_payload_rejected(self, client, encryption_ctx):
        """Malformed base64 in encrypted_payload returns HTTP 400."""
        body = {
            "encrypted_payload": "not-valid-base64!!!@@@",
            "client_public_key": "dGVzdA==",  # valid base64
        }
        response = client.post("/execute", json=body)
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["error"] == "invalid_base64"

    def test_malformed_base64_client_public_key_rejected(self, client, encryption_ctx):
        """Malformed base64 in client_public_key returns HTTP 400."""
        import base64
        # Use valid base64 for payload but invalid for key
        body = {
            "encrypted_payload": base64.b64encode(b"test").decode(),
            "client_public_key": "invalid!!!base64@@@",
        }
        response = client.post("/execute", json=body)
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["error"] == "invalid_base64"

    def test_base64_with_whitespace_rejected(self, client, encryption_ctx):
        """Base64 with embedded whitespace (non-strict) is rejected with validate=True."""
        import base64
        # Standard base64 with newlines — validate=True rejects this
        valid_b64 = base64.b64encode(b"test data here").decode()
        # Insert whitespace to make it non-strict
        malformed = valid_b64[:4] + "\n" + valid_b64[4:]
        body = {
            "encrypted_payload": malformed,
            "client_public_key": base64.b64encode(b"key").decode(),
        }
        response = client.post("/execute", json=body)
        assert response.status_code == 400


# ===========================================================================
# 184.7, 184.8 – CI Pinning Tests
# ===========================================================================

class TestCIPinning:
    """Verify workflow actions are SHA-pinned; Dockerfile FROM has @sha256: digest."""

    WORKFLOW_PATH = Path(__file__).parent.parent / ".github" / "workflows" / "build-attestable-image.yml"
    DOCKERFILE_PATH = Path(__file__).parent.parent / ".github" / "docker" / "Dockerfile.kiwi-builder"

    def test_all_uses_directives_are_sha_pinned(self):
        """All 'uses:' directives in the workflow must reference a 40-char hex SHA."""
        content = self.WORKFLOW_PATH.read_text()
        # Find all uses: directives
        uses_pattern = re.compile(r'uses:\s*(\S+)')
        matches = uses_pattern.findall(content)

        assert len(matches) > 0, "No 'uses:' directives found in workflow"

        sha_pattern = re.compile(r'@[0-9a-f]{40}$')
        for action_ref in matches:
            assert sha_pattern.search(action_ref), (
                f"Action reference not SHA-pinned (must have @<40-hex-chars>): {action_ref}"
            )

    def test_no_mutable_tag_only_references(self):
        """No 'uses:' directive references only a mutable tag (e.g., @v4)."""
        content = self.WORKFLOW_PATH.read_text()
        uses_pattern = re.compile(r'uses:\s*(\S+)')
        matches = uses_pattern.findall(content)

        mutable_tag_pattern = re.compile(r'@v\d+(\.\d+)*$')
        for action_ref in matches:
            assert not mutable_tag_pattern.search(action_ref), (
                f"Action reference uses mutable tag (must use SHA): {action_ref}"
            )

    def test_dockerfile_from_contains_sha256_digest(self):
        """Dockerfile FROM directive must contain @sha256: digest."""
        content = self.DOCKERFILE_PATH.read_text()
        from_pattern = re.compile(r'^FROM\s+(.+)$', re.MULTILINE)
        matches = from_pattern.findall(content)

        assert len(matches) > 0, "No FROM directive found in Dockerfile"

        for from_ref in matches:
            assert "@sha256:" in from_ref, (
                f"Dockerfile FROM not digest-pinned (must have @sha256:): {from_ref}"
            )


# ===========================================================================
# 184.10 – Script_env Deny-list Tests
# ===========================================================================

class TestScriptEnvDenyList:
    """Verify dangerous env vars are rejected; safe vars accepted."""

    def test_bash_env_rejected(self, client, encryption_ctx):
        """BASH_ENV in script_env is rejected."""
        request_data = _base_request_data(
            script_env={"BASH_ENV": "/tmp/evil.sh"},
            nonce="deny-list-bash-env-test1",
        )
        response = _post_execute(client, encryption_ctx, request_data)
        error = assert_encrypted_error(response, encryption_ctx.shared_key, "denied_env_key", 400)
        assert "BASH_ENV" in error.get("error_details", {}).get("denied_keys", [])

    def test_path_rejected(self, client, encryption_ctx):
        """PATH in script_env is rejected."""
        request_data = _base_request_data(
            script_env={"PATH": "/tmp/evil:/usr/bin"},
            nonce="deny-list-path-test1234",
        )
        response = _post_execute(client, encryption_ctx, request_data)
        error = assert_encrypted_error(response, encryption_ctx.shared_key, "denied_env_key", 400)
        assert "PATH" in error.get("error_details", {}).get("denied_keys", [])

    def test_ld_preload_rejected(self, client, encryption_ctx):
        """LD_PRELOAD in script_env is rejected."""
        request_data = _base_request_data(
            script_env={"LD_PRELOAD": "/tmp/evil.so"},
            nonce="deny-list-ldpreload-t1",
        )
        response = _post_execute(client, encryption_ctx, request_data)
        error = assert_encrypted_error(response, encryption_ctx.shared_key, "denied_env_key", 400)
        assert "LD_PRELOAD" in error.get("error_details", {}).get("denied_keys", [])

    def test_bash_func_prefix_rejected(self, client, encryption_ctx):
        """BASH_FUNC_* prefix match in script_env is rejected."""
        request_data = _base_request_data(
            script_env={"BASH_FUNC_evil%%": "() { evil; }"},
            nonce="deny-list-bashfunc-t1",
        )
        response = _post_execute(client, encryption_ctx, request_data)
        error = assert_encrypted_error(response, encryption_ctx.shared_key, "denied_env_key", 400)
        assert "BASH_FUNC_evil%%" in error.get("error_details", {}).get("denied_keys", [])

    def test_ld_library_path_rejected(self, client, encryption_ctx):
        """LD_LIBRARY_PATH in script_env is rejected."""
        request_data = _base_request_data(
            script_env={"LD_LIBRARY_PATH": "/tmp/evil"},
            nonce="deny-list-ldlibpath-1",
        )
        response = _post_execute(client, encryption_ctx, request_data)
        assert_encrypted_error(response, encryption_ctx.shared_key, "denied_env_key", 400)

    def test_github_token_accepted(self, client, encryption_ctx):
        """GITHUB_TOKEN (not on deny-list) is accepted."""
        request_data = _base_request_data(
            script_env={"GITHUB_TOKEN": "ghp_test"},
            nonce="deny-list-accept-test1",
        )
        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert "execution_id" in data

    def test_custom_safe_var_accepted(self, client, encryption_ctx):
        """Custom safe environment variable is accepted."""
        request_data = _base_request_data(
            script_env={"MY_APP_CONFIG": "value123"},
            nonce="deny-list-safe-var-t1",
        )
        response = _post_execute(client, encryption_ctx, request_data)
        assert response.status_code == 200
        data = decrypt_execute_response(response.json(), encryption_ctx.shared_key)
        assert "execution_id" in data


# ===========================================================================
# 184.11 / 186.4 – Package Minimization Tests
# ===========================================================================

class TestPackageMinimization:
    """Verify package policy in appliance.kiwi.

    Policy:
    - awscli, pciutils: completely absent (no operational justification)
    - python3.11-pip: present in <packages type="image"> (build-time dep for config.sh)
      AND present in <packages type="uninstall"> (removed from final runtime image)
    - binutils: present in <packages type="image"> with justification comment
      (required by dracut --uefi for UKI assembly); must NOT be in uninstall section
    - git: present (required by Repository_Client)
    """

    APPLIANCE_PATH = Path(__file__).parent.parent / "kiwi-descriptions" / "appliance.kiwi"

    def test_awscli_not_in_packages(self):
        """awscli must not be in any packages section."""
        content = self.APPLIANCE_PATH.read_text()
        assert not re.search(r'<package\s+name="awscli"', content), (
            "awscli should be removed from appliance.kiwi"
        )

    def test_pciutils_not_in_packages(self):
        """pciutils must not be in any packages section."""
        content = self.APPLIANCE_PATH.read_text()
        assert not re.search(r'<package\s+name="pciutils"', content), (
            "pciutils should be removed from appliance.kiwi"
        )

    def test_python3_pip_in_image_packages(self):
        """python3.11-pip must be in <packages type="image"> for build-time use."""
        content = self.APPLIANCE_PATH.read_text()
        # Extract the <packages type="image"> section
        image_section = re.search(
            r'<packages\s+type="image">(.*?)</packages>',
            content,
            re.DOTALL,
        )
        assert image_section is not None, "No <packages type=\"image\"> section found"
        assert re.search(r'<package\s+name="python3\.11-pip"', image_section.group(1)), (
            "python3.11-pip must be in <packages type=\"image\"> (build-time dep for config.sh)"
        )

    def test_python3_pip_in_uninstall_packages(self):
        """python3.11-pip must be in <packages type="uninstall"> for runtime removal."""
        content = self.APPLIANCE_PATH.read_text()
        # Extract the <packages type="uninstall"> section
        uninstall_section = re.search(
            r'<packages\s+type="uninstall">(.*?)</packages>',
            content,
            re.DOTALL,
        )
        assert uninstall_section is not None, "No <packages type=\"uninstall\"> section found"
        assert re.search(r'<package\s+name="python3\.11-pip"', uninstall_section.group(1)), (
            "python3.11-pip must be in <packages type=\"uninstall\"> (removed from final image)"
        )

    def test_binutils_in_image_packages(self):
        """binutils must be in <packages type="image"> (required by dracut --uefi)."""
        content = self.APPLIANCE_PATH.read_text()
        image_section = re.search(
            r'<packages\s+type="image">(.*?)</packages>',
            content,
            re.DOTALL,
        )
        assert image_section is not None, "No <packages type=\"image\"> section found"
        assert re.search(r'<package\s+name="binutils"', image_section.group(1)), (
            "binutils must be in <packages type=\"image\"> (required by dracut --uefi for UKI assembly)"
        )

    def test_binutils_has_justification_comment(self):
        """binutils entry must have a comment documenting its justification."""
        content = self.APPLIANCE_PATH.read_text()
        # The comment should appear before the binutils package entry
        assert re.search(
            r'<!--[^>]*dracut[^>]*uefi[^>]*-->.*?<package\s+name="binutils"',
            content,
            re.DOTALL,
        ), (
            "binutils must have a justification comment mentioning dracut --uefi"
        )

    def test_binutils_not_in_uninstall(self):
        """binutils must NOT be in <packages type="uninstall">."""
        content = self.APPLIANCE_PATH.read_text()
        uninstall_section = re.search(
            r'<packages\s+type="uninstall">(.*?)</packages>',
            content,
            re.DOTALL,
        )
        if uninstall_section:
            assert not re.search(r'<package\s+name="binutils"', uninstall_section.group(1)), (
                "binutils must NOT be in <packages type=\"uninstall\"> — dracut needs it at create time"
            )

    def test_git_still_present(self):
        """git must still be present (required by Repository_Client)."""
        content = self.APPLIANCE_PATH.read_text()
        assert re.search(r'<package\s+name="git"', content), (
            "git must remain in appliance.kiwi (required by Repository_Client)"
        )

    def test_allow_list_policy_comment_present(self):
        """Package allow-list policy comment must be present."""
        content = self.APPLIANCE_PATH.read_text()
        assert "allow-list" in content.lower() or "allowlist" in content.lower(), (
            "Package allow-list policy comment not found in appliance.kiwi"
        )
        assert "allow-list" in content.lower() or "allowlist" in content.lower(), (
            "Package allow-list policy comment not found in appliance.kiwi"
        )


# ===========================================================================
# 184.12 – Log Sanitization Tests
# ===========================================================================

class TestLogSanitization:
    """Verify tokens, paths, control chars redacted from logs and error responses."""

    def test_github_token_ghp_redacted(self):
        """GitHub token (ghp_*) in message is redacted."""
        sanitizer = LogSanitizer()
        message = "Clone failed: token ghp_ABCdef123456789012345678901234567890 expired"
        result = sanitizer.sanitize(message)
        assert "ghp_" not in result
        assert "[REDACTED_TOKEN]" in result

    def test_github_token_ghs_redacted(self):
        """GitHub token (ghs_*) in message is redacted."""
        sanitizer = LogSanitizer()
        message = "Auth with ghs_InstallationToken12345678901234567890"
        result = sanitizer.sanitize(message)
        assert "ghs_" not in result
        assert "[REDACTED_TOKEN]" in result

    def test_github_pat_redacted(self):
        """GitHub PAT (github_pat_*) in message is redacted."""
        sanitizer = LogSanitizer()
        message = "Using github_pat_ABCdef123456789012345678901234567890_extra"
        result = sanitizer.sanitize(message)
        assert "github_pat_" not in result
        assert "[REDACTED_TOKEN]" in result

    def test_credentialed_url_redacted(self):
        """Credentialed URL (https://token@host) is redacted."""
        sanitizer = LogSanitizer()
        message = "Cloning https://ghp_secret@github.com/owner/repo.git"
        result = sanitizer.sanitize(message)
        assert "ghp_secret" not in result
        assert "[REDACTED" in result

    def test_absolute_paths_redacted(self):
        """Absolute file paths are redacted."""
        sanitizer = LogSanitizer()
        message = "Error reading /home/user/secrets/config.json"
        result = sanitizer.sanitize(message)
        assert "/home/user/secrets/config.json" not in result
        assert "[PATH]" in result

    def test_authorization_header_redacted(self):
        """Authorization header values are redacted."""
        sanitizer = LogSanitizer()
        message = "Header: Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig"
        result = sanitizer.sanitize(message)
        assert "eyJhbGciOiJSUzI1NiJ9" not in result
        assert "[REDACTED]" in result

    def test_control_characters_removed(self):
        """ASCII control characters (except \\n, \\t) are removed."""
        sanitizer = LogSanitizer()
        message = "Normal text\x00\x01\x02\x03hidden\x7fend"
        result = sanitizer.sanitize(message)
        assert "\x00" not in result
        assert "\x01" not in result
        assert "\x7f" not in result
        assert "Normal text" in result
        assert "hidden" in result

    def test_env_var_with_token_redacted(self):
        """Environment variable assignment containing token is redacted."""
        sanitizer = LogSanitizer()
        message = "GITHUB_TOKEN=ghp_SecretTokenValue12345678901234567890"
        result = sanitizer.sanitize(message)
        assert "ghp_SecretTokenValue" not in result

    def test_truncate_field_caps_length(self):
        """truncate_field caps user-controlled fields at 256 chars."""
        long_value = "x" * 500
        result = truncate_field(long_value)
        assert len(result) == 256 + len("[truncated]")
        assert result.endswith("[truncated]")

    def test_truncate_field_short_value_unchanged(self):
        """truncate_field does not modify values within limit."""
        short_value = "hello world"
        result = truncate_field(short_value)
        assert result == short_value

    def test_nonce_not_logged_verbatim(self):
        """sanitize_nonce_for_logging returns only prefix, not full nonce."""
        nonce = "abcdefghijklmnopqrstuvwxyz"
        result = sanitize_nonce_for_logging(nonce)
        assert result == "abcdefgh..."
        assert nonce not in result

    def test_sanitize_log_message_convenience_function(self):
        """Module-level sanitize_log_message works correctly."""
        message = "Error: ghp_TokenValue12345678901234567890123456 at /tmp/secret/file.py"
        result = sanitize_log_message(message)
        assert "ghp_" not in result
        assert "[REDACTED_TOKEN]" in result

    def test_multiline_stderr_sanitized(self):
        """Multi-line subprocess stderr is sanitized."""
        sanitizer = LogSanitizer()
        stderr = (
            "fatal: Authentication failed for 'https://ghp_secret@github.com/owner/repo.git'\n"
            "remote: Invalid credentials\n"
            "fatal: Could not read from remote repository."
        )
        result = sanitizer.sanitize(stderr)
        assert "ghp_secret" not in result
