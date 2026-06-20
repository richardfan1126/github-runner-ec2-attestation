## 1. Build-side: digest-preserving copy into the OCI-layout intermediate

- [ ] 1.1 In the image-build pipeline (`.github/workflows/build-attestable-image.yml`), add a step that copies the externally-supplied, digest-pinned Container_Image **by its `linux/amd64` manifest digest** from GHCR into a build-time **OCI layout** intermediate, using a digest-preserving tool (`oras cp` / `skopeo copy oci:`); `oras` is already installed in the workflow
- [ ] 1.2 Resolve and pin the `linux/amd64` per-platform manifest when a multi-arch index is supplied, and fail the build if the supplied reference resolves to a multi-platform index digest rather than a single-platform manifest (D2 constraint 1)
- [ ] 1.3 Assert the intermediate's manifest blob is byte-identical to the GHCR `linux/amd64` manifest (i.e. `sha256(manifest blob) == CONTAINER_IMAGE_DIGEST`) and fail the build on mismatch — no bake-time Sigstore provenance check is performed
- [ ] 1.4 Confirm the copy is digest-preserving and performs no rebuild (the OCI layout is a build-time intermediate, not yet a baked artifact)

## 2. Loader-faithfulness spike (C1-a) — fixes the baked layer-carrying artifact

- [ ] 2.1 Spike the leading path: convert the OCI-layout intermediate to a **docker-archive** (`oci→docker-archive`), `docker load` it into the rootless daemon, and confirm the loaded image's ID equals the config digest derived from the manifest (i.e. the conversion preserves the config blob and does not rewrite the config JSON)
- [ ] 2.2 Record the spike outcome: if faithful, bake the **docker-archive + OCI-manifest sidecar** and use built-in `docker load` at runtime; if not faithful, take the fallback — bake the **OCI layout + sidecar** and use **runtime skopeo** (`skopeo copy oci:<layout> docker-daemon:`), converting from the faithful OCI layout, never from the suspect archive
- [ ] 2.3 Fix the baked artifact form for the rest of the change from the spike outcome (build-side decision, not a per-boot toggle); the sidecar and the offline Verify step are identical either way

## 3. Build-side: bake the artifacts into the KIWI root tree (under verity)

- [ ] 3.1 Choose the injection point: place the baked files into the KIWI root tree in a builder/CI step **before** KIWI's create phase (they are root-tree files, not boot artifacts, so they need no post-UKI hook), at a fixed path documented in the change
- [ ] 3.2 Emit the **docker-archive** (or the OCI layout, per the spike) at the fixed path inside the erofs root tree so `verity_blocks="all"` measures its bytes into PCR4
- [ ] 3.3 Emit the **OCI manifest blob** copied out byte-for-byte as a **sidecar** at the fixed path, so the runtime can re-hash the on-disk bytes exactly (never a re-serialized form)
- [ ] 3.4 Confirm the baked files land in the root tree before the image is finalized and PCRs are computed, and that PCR4 covers them (rebuild reproducibility is explicitly not required)

## 4. Runtime: offline verify → derive → load + bind

- [ ] 4.1 Replace `pull_container_image()` (`src/.../script_executor.py:559`) with a **Verify** step: recompute a byte-exact SHA-256 over the stored OCI-manifest sidecar bytes and compare to the expected `CONTAINER_IMAGE_DIGEST` — pure offline hashing, no daemon call, no network, no `index.json` walk (single manifest blob)
- [ ] 4.2 Add the **Derive** step: read the config descriptor out of the verified manifest and treat its digest as the trusted **image ID**, derived entirely from verity-measured, digest-verified bytes (never a daemon-reported value)
- [ ] 4.3 Add the **Load** step: `docker load` (docker-py `images.load()`) the baked docker-archive into the existing rootless daemon (legacy graphdriver store; `daemon.json` sets no containerd snapshotter) — or run `skopeo copy oci:<layout> docker-daemon:` if the fallback path was selected
- [ ] 4.4 Change `containers.create()` (`script_executor.py:284`) to bind to the **derived image ID** instead of the `repository@sha256:<manifest>` reference string, so loss of `RepoDigests` and the absence of a repo tag on the loaded archive are irrelevant
- [ ] 4.5 Audit and fix any executor code that logs or derives the image **name/tag** rather than its ID (the loaded archive may be tagless `<none>:<none>`)
- [ ] 4.6 Preserve fail-closed semantics (`config.py:485`, `main.py:215`): fail to start with a descriptive error and no network fallback if the expected digest is absent/empty, the baked archive or sidecar is missing/corrupt, or the recomputed manifest digest mismatches
- [ ] 4.7 Keep `CONTAINER_IMAGE_DIGEST` (a **manifest** digest) and the image ID (a **config** digest) as distinct values — never compare, substitute, or conflate them; update config/startup wiring and remove the now-dead registry-pull path

## 5. Verifier record (D-rec) emission via publish-time surfaces

- [ ] 5.1 In the `build-and-publish` job, add `container_image_digest` as an ORAS annotation on the published KIWI artifact (alongside the existing `pcr4`/`pcr7` annotations at the `oras push` step), fixed at publish time and never amended with the AMI id afterward
- [ ] 5.2 Write `container_image_digest` to the job log and the step summary at publish time (alongside the existing PCR/attestation reporting)
- [ ] 5.3 In the `build-ami` job, after AMI registration emit the **single-entry verifier record** (image manifest digest → PCR4 → AMI id → producing commit) to the job log and the step summary
- [ ] 5.4 Tag the registered AMI with the container image manifest digest (via `scripts/build-ami.py` / the build-ami terraform stack)
- [ ] 5.5 Serialize the record as a single entry whose field set is a clean **subset** of the multi-flavor `flavors.lock` introduced by the `execution-build-images` change, so that change need not retrofit a format
- [ ] 5.6 Confirm the runtime NitroTPM attestation and its `user_data` are unchanged (PCR4 already binds the baked image bytes; the join for verifiers is PCR4)

## 6. Validation

- [ ] 6.1 Add a startup/unit test asserting offline verification fails closed on a missing sidecar, a corrupt sidecar, a missing/empty expected digest, and a manifest-digest mismatch
- [ ] 6.2 Add a test asserting `containers.create()` binds to the derived image ID and that an unfaithful load (image ID ≠ derived config digest) fails closed with no matching image
- [ ] 6.3 End-to-end: build an AMI with the baked archive + sidecar, boot it offline (no registry reachability), and confirm a script executes against the baked image and the emitted verifier record (log, summary, ORAS annotation, AMI tag) matches the baked manifest digest
