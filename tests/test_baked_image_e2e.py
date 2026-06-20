"""Offline end-to-end test of the baked-image runtime path (task 6.3, runtime slice).

This exercises the *real* runtime contract against a *real* Docker daemon and a
*real* image, with no mocks and no registry reachability during the executor's
work:

  1. Build real baked artifacts the way the AMI carries them — a docker-archive
     (`image.tar`) and the raw OCI-manifest sidecar (`manifest.json`, byte-
     identical to the registry manifest).
  2. Remove the image from the daemon, then run the executor's actual
     `load_baked_image()` — verify the sidecar offline, derive the image ID
     (config digest), `docker load` the archive, bind to the derived ID — with
     **no network pull**.
  3. Execute a real script in a container created from the derived image ID and
     assert its output, proving a script runs against the baked image offline.
  4. Assert the verifier-record join holds: sha256(sidecar) == the configured
     manifest digest, and the image actually run == the manifest's config digest.

The full task 6.3 (AMI build + PCR4 binding + offline EC2 boot) needs the cloud
build/boot environment; this is its runtime heart, runnable in CI.

The archive here is synthesized with `docker pull @digest` + `docker save`
(skopeo-free), which yields the same runtime invariant the production skopeo
`oci→docker-archive` path does (loaded image ID == config digest, validated
independently by the C1-a loader-faithfulness spike). The test skips cleanly
when the Docker daemon or registry is unreachable.
"""
import hashlib
import json
import os
import time
import urllib.request
import urllib.error

import pytest

from src.script_executor import ScriptExecutor, CONTAINER_NAME_PREFIX
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus

docker_lib = pytest.importorskip("docker")

ENV_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "kiwi-descriptions", "root", "etc", "github-actions-remote-executor", "env",
)


def _read_env():
    image = digest = None
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("CONTAINER_IMAGE="):
                image = line.split("=", 1)[1].strip()
            elif line.startswith("CONTAINER_IMAGE_DIGEST="):
                digest = line.split("=", 1)[1].strip()
    return image, digest


def _docker_library_path(image: str):
    """Map a Docker Hub image ref to its registry API repo path (e.g. library/ubuntu)."""
    ref = image.split("@", 1)[0]  # drop any digest
    # strip tag from the last path component
    head, _, last = ref.rpartition("/")
    last = last.split(":", 1)[0]
    name = f"{head}/{last}" if head else last
    # Only Docker Hub (implicit docker.io) is handled by this test's raw fetch.
    if name.startswith("docker.io/"):
        name = name[len("docker.io/"):]
    if "." in name.split("/", 1)[0] or ":" in name.split("/", 1)[0]:
        return None  # a non-Docker-Hub registry host — skip
    return name if "/" in name else f"library/{name}"


def _fetch_raw_manifest(repo_path: str, digest: str) -> bytes:
    token_url = (
        "https://auth.docker.io/token?service=registry.docker.io"
        f"&scope=repository:{repo_path}:pull"
    )
    token = json.loads(urllib.request.urlopen(token_url, timeout=20).read())["token"]
    req = urllib.request.Request(
        f"https://registry-1.docker.io/v2/{repo_path}/manifests/{digest}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": ", ".join([
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.v2+json",
            ]),
        },
    )
    return urllib.request.urlopen(req, timeout=20).read()


@pytest.fixture(scope="module")
def docker_client():
    try:
        client = docker_lib.from_env()
        client.ping()
    except Exception as e:
        pytest.skip(f"Docker daemon not reachable: {e}")
    return client


def test_offline_baked_image_load_and_execute(docker_client, tmp_path):
    image, digest = _read_env()
    if not image or not digest or not digest.startswith("sha256:"):
        pytest.skip("CONTAINER_IMAGE/CONTAINER_IMAGE_DIGEST not configured")
    repo_path = _docker_library_path(image)
    if repo_path is None:
        pytest.skip(f"Image '{image}' is not a Docker Hub image this test can fetch raw")

    repo = repo_path[len("library/"):] if repo_path.startswith("library/") else repo_path
    pinned_ref = f"{repo}@{digest}"

    # --- Build the real baked artifacts (sidecar + docker-archive) -------------
    try:
        sidecar = _fetch_raw_manifest(repo_path, digest)
    except (urllib.error.URLError, TimeoutError) as e:
        pytest.skip(f"Registry not reachable to fetch manifest: {e}")

    # Byte-identity: the raw sidecar hashes to the configured manifest digest.
    assert "sha256:" + hashlib.sha256(sidecar).hexdigest() == digest
    manifest = json.loads(sidecar)
    if "config" not in manifest or "manifests" in manifest:
        pytest.skip("Configured digest is not a single-platform image manifest")
    config_digest = manifest["config"]["digest"]

    try:
        image_obj = docker_client.images.pull(pinned_ref)
    except docker_lib.errors.APIError as e:
        pytest.skip(f"Could not pull {pinned_ref}: {e}")

    baked_dir = tmp_path / "baked-image"
    baked_dir.mkdir()
    archive_path = baked_dir / "image.tar"
    manifest_path = baked_dir / "manifest.json"
    manifest_path.write_bytes(sidecar)
    with open(archive_path, "wb") as f:
        for chunk in image_obj.save(named=False):
            f.write(chunk)

    # Remove the image so the executor's load — not a network pull — is what
    # brings it into the daemon (the true offline-load assertion).
    try:
        docker_client.images.remove(image_obj.id, force=True)
    except docker_lib.errors.APIError:
        pass

    manager = ExecutionManager(output_retention_hours=1)
    collector = OutputCollector()
    executor = ScriptExecutor(
        docker_client=docker_client,
        container_image=image,
        container_image_digest=digest,
        baked_image_archive_path=str(archive_path),
        baked_image_manifest_path=str(manifest_path),
        execution_manager=manager,
        output_collector=collector,
        temp_storage_path=str(tmp_path),
        timeout_seconds=60,
    )

    loaded_image_id = None
    record = None
    try:
        # --- Offline verify -> derive -> load + bind --------------------------
        executor.load_baked_image()
        loaded_image_id = executor.derived_image_id
        assert loaded_image_id == config_digest
        assert executor.execution_image_ref == config_digest
        # The verifier-record join: manifest digest (anchor) and the image run.
        assert digest != config_digest  # manifest digest vs config digest are distinct

        # --- Execute a real script against the baked image, offline -----------
        token = "E2E_OK_a1b2c3"
        script_dir = tmp_path / "repo"
        script_dir.mkdir()
        (script_dir / "run.sh").write_text(f"#!/bin/bash\necho {token}\n")
        os.chmod(script_dir / "run.sh", 0o755)

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="run.sh",
            timeout_seconds=60,
        )
        executor.execute_async(record.execution_id, str(script_dir), "run.sh")

        deadline = time.time() + 60
        while time.time() < deadline:
            rec = manager.get_execution(record.execution_id)
            if rec and rec.status in (
                ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT
            ):
                break
            time.sleep(0.2)

        final = manager.get_execution(record.execution_id)
        assert final.status == ExecutionStatus.COMPLETED, f"status={final.status}"
        output = collector.get_output(record.execution_id)
        assert token in output.stdout
        assert output.exit_code == 0
    finally:
        # Clean up any lingering execution container and the loaded image.
        try:
            for c in docker_client.containers.list(all=True, filters={"name": CONTAINER_NAME_PREFIX}):
                c.remove(force=True)
        except Exception:
            pass
        if loaded_image_id:
            try:
                docker_client.images.remove(loaded_image_id, force=True)
            except Exception:
                pass
