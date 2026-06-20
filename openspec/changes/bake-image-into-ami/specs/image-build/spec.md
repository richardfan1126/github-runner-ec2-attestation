## MODIFIED Requirements

### Requirement: Container image pull at server startup

When the GHA_Server starts, it SHALL obtain the configured Container_Image from the **baked docker-archive** measured into the verity-sealed root — verifying it **offline** (no registry, no network) against the **baked OCI-manifest sidecar** and binding every `containers.create()` call to the derived **image ID** (config digest) rather than the daemon-reported `repository@sha256:<digest>` reference. The server SHALL fail to start, with the same fail-closed semantics as before, if the expected digest is absent, the baked docker-archive or OCI-manifest sidecar is missing or corrupt, or the recomputed manifest digest does not match the expected value. The expected `CONTAINER_IMAGE_DIGEST` is a **manifest** digest and the image ID is the **config** digest; these are distinct values and the executor SHALL NOT compare, substitute, or otherwise conflate one for the other.

#### Scenario: Offline manifest-digest verification

- **WHEN** the server starts
- **THEN** it recomputes the digest as a **byte-exact SHA-256 over the stored OCI-manifest sidecar bytes** (never over a re-canonicalized or re-serialized form of the JSON), and compares it to the expected `CONTAINER_IMAGE_DIGEST` using pure offline hashing — no Docker daemon call and no network — failing to start on mismatch or on a missing/empty expected digest. The build has already resolved and pinned the `linux/amd64` manifest and emitted exactly that blob as the sidecar, so the runtime does not walk an `index.json` or select a child manifest

#### Scenario: Image ID derived from the verified manifest

- **WHEN** the manifest digest is verified
- **THEN** the server reads the config descriptor out of that verified manifest and treats its digest as the trusted **image ID**, derived entirely from verity-measured, digest-verified bytes and independent of any value the daemon reports

#### Scenario: Layout loaded and execution bound to the image ID

- **WHEN** the image ID is derived
- **THEN** the server `docker load`s the baked docker-archive into the existing rootless daemon (legacy graphdriver store; `daemon.json` sets no containerd snapshotter), and constructs the Script_Executor so every `containers.create()` call references the derived image ID rather than a `repository@sha256:<manifest>` string — so the loss of `RepoDigests` across import (and the absence of any repo tag on the loaded archive) does not affect execution

#### Scenario: Unfaithful load fails closed at bind time

- **WHEN** the loader produces a local image whose ID does not equal the image ID derived from the verified manifest (for example a loader that rewrote the config blob)
- **THEN** `containers.create()` referencing the derived image ID fails closed because no loaded image matches that ID, so execution never proceeds against an image other than the one the verified manifest commits to

#### Scenario: Fail-closed without network reachability

- **WHEN** the baked docker-archive or OCI-manifest sidecar is absent or corrupt, or the recomputed manifest digest does not match the expected digest
- **THEN** the server fails to start with a descriptive error, with no fallback to a network pull and without requiring registry reachability at boot

## ADDED Requirements

### Requirement: Baked docker-archive and OCI-manifest sidecar in the KIWI root tree

The Build_Workflow SHALL copy the externally-supplied, digest-pinned Container_Image **by digest** into an OCI layout (a build-time intermediate, using a digest-preserving tool such as `oras cp` / `skopeo copy oci:`), then bake **two** files into the KIWI root tree at a fixed path inside the erofs root so `verity_blocks="all"` measures their bytes into PCR4: (a) a **docker-archive** (`docker save`-format) produced by an `oci→docker-archive` conversion, which `docker load` consumes at runtime, and (b) the **OCI manifest blob** copied out byte-for-byte as a **sidecar**, whose `sha256` equals the published `linux/amd64` manifest digest. The copy into the intermediate layout SHALL pin the `linux/amd64` per-platform manifest (never a multi-platform index digest). The conversion SHALL preserve the **config blob** (and thus the image ID) and SHALL NOT rebuild the image or rewrite the config JSON; it MAY re-serialize the manifest envelope to docker schema2, because the manifest digest is anchored by the sidecar, not the archive. The build SHALL NOT perform a bake-time provenance check of the image.

#### Scenario: Image copied by digest into the verity-measured root

- **WHEN** the KIWI image is built
- **THEN** the Container_Image is copied by its `linux/amd64` manifest digest into an intermediate OCI layout, and a docker-archive plus an OCI-manifest sidecar derived from it are placed at a fixed path in the KIWI root tree, landing there before the image is finalized so `verity_blocks="all"` measures their bytes into PCR4

#### Scenario: Digest-preserving copy, config-preserving conversion

- **WHEN** the image is copied and converted
- **THEN** the copy into the intermediate layout is digest-preserving so the manifest blob (later emitted as the sidecar) is byte-identical to the GHCR artifact and the `linux/amd64` child manifest is resolved and pinned if a multi-arch index is supplied; the `oci→docker-archive` conversion preserves the config blob so the image ID is unchanged; and the image is never rebuilt nor its config JSON rewritten

#### Scenario: No bake-time provenance verification

- **WHEN** the image is baked
- **THEN** the build trusts the digest-pinned reference and does NOT verify the image's Sigstore provenance, leaving provenance verifiable by consumers on GHCR

### Requirement: Container image digest surfaced for external verifiers

When the build publishes the KIWI artifact, the Artifact_Publisher SHALL surface the baked image's manifest digest (`container_image_digest`) through publish-time outputs — the GitHub job log, the step summary, and an ORAS annotation on the published artifact — so external verifiers can read which image the artifact (and any AMI built from it) carries without running the instance. This self-description SHALL NOT be carried in the runtime attestation `user_data`.

#### Scenario: Digest reported at publish time

- **WHEN** the KIWI artifact is pushed to GHCR
- **THEN** `container_image_digest` is written to the job log and the step summary and attached as an ORAS annotation on the published artifact, alongside the existing `pcr4`/`pcr7` annotations

#### Scenario: Attestation user_data unchanged

- **WHEN** the runtime attestation is produced
- **THEN** no `container_image_digest` self-description is added to `user_data`, because PCR4 already binds the baked image bytes
