"""Unit tests for the offline baked-image load path (verify -> derive -> load + bind).

These replace the former startup registry-pull tests. They validate that the
executor:

  * verifies the baked OCI-manifest sidecar offline against the expected manifest
    digest, failing closed on a missing/corrupt sidecar, a missing/empty expected
    digest, and a digest mismatch (§6.1);
  * derives the trusted image ID (config digest) from the verified manifest and
    binds container creation to it, and fails closed when the loaded image does
    not carry that derived ID (an unfaithful conversion) (§6.2).
"""
import hashlib
import json
import os
import tempfile
import time
from unittest.mock import MagicMock

import docker.errors
import pytest

from src.script_executor import ScriptExecutor
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus
from tests.mock_docker import create_mock_docker_client

CONFIG_DIGEST = "sha256:" + "a" * 64


def _manifest_bytes(config_digest: str = CONFIG_DIGEST) -> bytes:
    """A minimal OCI image manifest referencing a config blob by digest."""
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": config_digest,
            "size": 123,
        },
        "layers": [],
    }
    # Intentionally non-canonical spacing: the digest is over THESE bytes, so the
    # executor must hash the stored bytes, never a re-serialized form.
    return json.dumps(manifest, indent=2).encode()


def _expected_digest(manifest_bytes: bytes) -> str:
    return "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()


def _write_baked(tmp, manifest_bytes, archive_bytes=b"docker-archive-bytes",
                 write_manifest=True, write_archive=True):
    manifest_path = os.path.join(tmp, "manifest.json")
    archive_path = os.path.join(tmp, "image.tar")
    if write_manifest:
        with open(manifest_path, "wb") as f:
            f.write(manifest_bytes)
    if write_archive:
        with open(archive_path, "wb") as f:
            f.write(archive_bytes)
    return manifest_path, archive_path


def _mock_client(images_get_ok=True):
    client = MagicMock()
    client.images = MagicMock()
    if images_get_ok:
        client.images.get.return_value = MagicMock()
    else:
        client.images.get.side_effect = docker.errors.ImageNotFound("no such image")
    return client


def _executor(manifest_path, archive_path, expected_digest, client):
    return ScriptExecutor(
        docker_client=client,
        container_image="ubuntu:24.04",
        container_image_digest=expected_digest,
        baked_image_archive_path=archive_path,
        baked_image_manifest_path=manifest_path,
    )


# --------------------------------------------------------------------------- #
# §6.1 — offline verification (verify + derive)                               #
# --------------------------------------------------------------------------- #

def test_load_baked_image_success_verifies_derives_and_binds():
    """Happy path: verify the sidecar, derive the config digest, load, and bind to it."""
    with tempfile.TemporaryDirectory() as tmp:
        mb = _manifest_bytes()
        manifest_path, archive_path = _write_baked(tmp, mb)
        client = _mock_client(images_get_ok=True)
        ex = _executor(manifest_path, archive_path, _expected_digest(mb), client)

        ex.load_baked_image()

        # Bound to the config digest derived from the verified manifest — NOT the
        # manifest digest.
        assert ex.derived_image_id == CONFIG_DIGEST
        assert ex.execution_image_ref == CONFIG_DIGEST
        assert CONFIG_DIGEST != ex._container_image_digest
        # docker load was called and the derived ID was confirmed present.
        client.images.load.assert_called_once()
        client.images.get.assert_called_once_with(CONFIG_DIGEST)


def test_missing_sidecar_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        mb = _manifest_bytes()
        manifest_path, archive_path = _write_baked(tmp, mb, write_manifest=False)
        ex = _executor(manifest_path, archive_path, _expected_digest(mb), _mock_client())
        with pytest.raises(RuntimeError, match="sidecar"):
            ex.load_baked_image()


def test_corrupt_empty_sidecar_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        mb = _manifest_bytes()
        manifest_path, archive_path = _write_baked(tmp, b"")  # empty sidecar
        # archive present, manifest empty
        ex = _executor(manifest_path, archive_path, _expected_digest(mb), _mock_client())
        with pytest.raises(RuntimeError, match="empty or corrupt"):
            ex.load_baked_image()


def test_missing_expected_digest_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        mb = _manifest_bytes()
        manifest_path, archive_path = _write_baked(tmp, mb)
        for empty in (None, ""):
            ex = _executor(manifest_path, archive_path, empty, _mock_client())
            with pytest.raises(RuntimeError, match="no expected manifest digest"):
                ex.load_baked_image()


def test_manifest_digest_mismatch_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        mb = _manifest_bytes()
        manifest_path, archive_path = _write_baked(tmp, mb)
        wrong = "sha256:" + "b" * 64
        ex = _executor(manifest_path, archive_path, wrong, _mock_client())
        with pytest.raises(RuntimeError, match="manifest digest mismatch"):
            ex.load_baked_image()


def test_sidecar_hashed_as_stored_bytes_not_reserialized():
    """The verify step must hash the on-disk bytes, so indentation/whitespace matters."""
    with tempfile.TemporaryDirectory() as tmp:
        mb = _manifest_bytes()
        manifest_path, archive_path = _write_baked(tmp, mb)
        # Expected digest computed over a re-canonicalized (compact) form must NOT verify.
        compact = json.dumps(json.loads(mb), separators=(",", ":")).encode()
        assert compact != mb
        ex = _executor(manifest_path, archive_path, _expected_digest(compact), _mock_client())
        with pytest.raises(RuntimeError, match="manifest digest mismatch"):
            ex.load_baked_image()


# --------------------------------------------------------------------------- #
# §6.2 — image-ID binding and unfaithful-load fail-closed                      #
# --------------------------------------------------------------------------- #

def test_missing_archive_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        mb = _manifest_bytes()
        manifest_path, archive_path = _write_baked(tmp, mb, write_archive=False)
        ex = _executor(manifest_path, archive_path, _expected_digest(mb), _mock_client())
        with pytest.raises(RuntimeError, match="docker-archive"):
            ex.load_baked_image()


def test_unfaithful_load_fails_closed_no_matching_image():
    """If the loaded image's ID != the derived config digest, binding fails closed."""
    with tempfile.TemporaryDirectory() as tmp:
        mb = _manifest_bytes()
        manifest_path, archive_path = _write_baked(tmp, mb)
        client = _mock_client(images_get_ok=False)  # images.get raises ImageNotFound
        ex = _executor(manifest_path, archive_path, _expected_digest(mb), client)
        with pytest.raises(RuntimeError, match="did not yield the derived image ID"):
            ex.load_baked_image()
        assert ex.derived_image_id is None


def test_containers_create_binds_to_derived_image_id():
    """A bound executor passes the derived image ID to containers.create()."""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        client = create_mock_docker_client()
        executor = ScriptExecutor(
            docker_client=client,
            container_image="ubuntu:24.04",
            container_image_digest="sha256:" + "f" * 64,  # manifest digest (distinct)
            bound_image_id=CONFIG_DIGEST,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir,
        )

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="e" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )
        script_path = os.path.join(temp_dir, "test.sh")
        with open(script_path, "w") as f:
            f.write("#!/bin/bash\necho hi\n")
        os.chmod(script_path, 0o755)

        executor.execute_async(record.execution_id, temp_dir, "test.sh")

        deadline = time.time() + 5
        while time.time() < deadline:
            rec = manager.get_execution(record.execution_id)
            if rec and rec.status in (
                ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT
            ):
                break
            time.sleep(0.05)

        calls = client.containers._creation_calls
        assert calls, "containers.create() was never called"
        assert calls[0]["image"] == CONFIG_DIGEST
