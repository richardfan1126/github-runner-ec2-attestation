## ADDED Requirements

### Requirement: Per-flavor execution-container image build and publish

The build pipeline SHALL build one hardened execution-container image per flavor selected by the dynamic matrix, from `flavors/<flavor>/Dockerfile` and its supplements, and publish each to GHCR **by immutable digest** at `ghcr.io/<owner>/<repo>/<flavor>`. Each per-flavor image SHALL satisfy the executor's hardened contract (rootless `65534`, world-exec tools on PATH, pinned, no run-time install) exactly as the single-flavor image did. The published artifact for a flavor SHALL be referenced everywhere by its amd64 per-platform manifest digest, never a mutable tag and never a multi-arch index digest.

#### Scenario: Each selected flavor produces a digest-pinned image

- **WHEN** the matrix selects flavors at image level
- **THEN** the pipeline builds each flavor's `Dockerfile` and publishes its image to GHCR, capturing the amd64 manifest digest for downstream bake and `flavors.lock`

#### Scenario: Hardened contract preserved per flavor

- **WHEN** a per-flavor image is built
- **THEN** it ships rootless `65534`, world-exec tools on PATH, pinned inputs, and no run-time install, identically to the single-flavor contract

### Requirement: Per-flavor baked OCI layout in the KIWI root tree

The KIWI image build SHALL be parameterized from one flavor to N, baking each selected flavor's published image into its own AMI as a digest-preserving OCI image layout inside the dm-verity-sealed erofs root, reusing the offline-import, offline-verify, and image-ID runtime-binding mechanism delivered single-flavor by the `bake-image-into-ami` change. The OCI layout baked for a flavor SHALL correspond to that flavor's amd64 manifest digest as recorded in `flavors.lock`, and the GHCR image (same manifest digest) SHALL remain the canonical provenance reference.

#### Scenario: Flavor image baked by its recorded digest

- **WHEN** the KIWI build runs for a flavor
- **THEN** it bakes the OCI layout for that flavor's amd64 manifest digest into the verity root, preserving byte-identity with the GHCR image

#### Scenario: AMI-only rebuild reuses the published image

- **WHEN** only a flavor's `env` changed (AMI-only level)
- **THEN** the KIWI build re-bakes the AMI using the flavor's existing image digest from `flavors.lock`, without rebuilding the image

#### Scenario: Mechanism reused unchanged

- **WHEN** a flavor's image is baked
- **THEN** the offline import/verify and image-ID runtime binding behave exactly as in the single-flavor `bake-image-into-ami` mechanism, multiplied per flavor
