## MODIFIED Requirements

### Requirement: Container image pull at server startup

When the GHA_Server starts, it SHALL obtain the configured Container_Image from the **baked OCI image layout** measured into the verity-sealed root — verifying it **offline** (no registry, no network) and binding every `containers.create()` call to the derived **image ID** (config digest) rather than the daemon-reported `repository@sha256:<digest>` reference. The server SHALL fail to start, with the same fail-closed semantics as before, if the expected digest is absent, the baked layout is missing or corrupt, or the recomputed manifest digest does not match the expected value. The expected `CONTAINER_IMAGE_DIGEST` is a **manifest** digest and the image ID is the **config** digest; these are distinct values and the executor SHALL NOT compare, substitute, or otherwise conflate one for the other.

#### Scenario: Offline manifest-digest verification

- **WHEN** the server starts
- **THEN** it locates the `linux/amd64` image manifest in the baked layout via the layout's `index.json` (selecting the `linux/amd64` child manifest if `index.json` is itself an image index), recomputes the digest as a **byte-exact SHA-256 over the stored manifest blob bytes** (never over a re-canonicalized or re-serialized form of the JSON), and compares it to the expected `CONTAINER_IMAGE_DIGEST` using pure offline hashing — no Docker daemon call and no network — failing to start on mismatch or on a missing/empty expected digest

#### Scenario: Image ID derived from the verified manifest

- **WHEN** the manifest digest is verified
- **THEN** the server reads the config descriptor out of that verified manifest and treats its digest as the trusted **image ID**, derived entirely from verity-measured, digest-verified bytes and independent of any value the daemon reports

#### Scenario: Layout loaded and execution bound to the image ID

- **WHEN** the image ID is derived
- **THEN** the server loads the baked OCI layout into the existing rootless daemon (legacy graphdriver store; `daemon.json` sets no containerd snapshotter) with a config-blob-preserving loader, and constructs the Script_Executor so every `containers.create()` call references the derived image ID rather than a `repository@sha256:<manifest>` string — so the loss of `RepoDigests` across import does not affect execution

#### Scenario: Unfaithful load fails closed at bind time

- **WHEN** the loader produces a local image whose ID does not equal the image ID derived from the verified manifest (for example a loader that rewrote the config blob)
- **THEN** `containers.create()` referencing the derived image ID fails closed because no loaded image matches that ID, so execution never proceeds against an image other than the one the verified manifest commits to

#### Scenario: Fail-closed without network reachability

- **WHEN** the baked layout is absent or corrupt, or the recomputed manifest digest does not match the expected digest
- **THEN** the server fails to start with a descriptive error, with no fallback to a network pull and without requiring registry reachability at boot

## ADDED Requirements

### Requirement: Baked OCI image layout in the KIWI root tree

The Build_Workflow SHALL copy the externally-supplied, digest-pinned Container_Image into the KIWI root tree as an **OCI image layout**, copied **by digest** with a digest-preserving tool (e.g. `oras cp` / `skopeo copy oci:`), placed at a fixed path inside the erofs root so `verity_blocks="all"` measures its bytes into PCR4. The copy SHALL pin the `linux/amd64` per-platform manifest (never a multi-platform index digest), SHALL NOT rebuild the image or convert media types in a way that rewrites the config JSON, and SHALL NOT perform a bake-time provenance check of the image.

#### Scenario: Image copied by digest into the verity-measured root

- **WHEN** the KIWI image is built
- **THEN** the Container_Image is copied by its `linux/amd64` manifest digest into the KIWI root tree as an OCI image layout at a fixed path, landing in the root tree before the image is finalized so `verity_blocks="all"` measures its bytes into PCR4

#### Scenario: Digest-preserving copy only

- **WHEN** the layout is copied
- **THEN** a digest-preserving tool is used so the manifest digest and config JSON are unchanged, the `linux/amd64` child manifest is resolved and pinned if a multi-arch index is supplied, and the image is never rebuilt or media-type-converted in a way that would rewrite the config blob and change the image ID

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
