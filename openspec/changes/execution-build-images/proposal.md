## Why

Today this repository builds a single attestable AMI that runs the Remote Executor against one externally-supplied execution-container image (pulled by digest at startup). There is no first-class way for a developer to declare several distinct build *environments* — each with its own toolchain, OS packages, and sandbox posture — and have CI produce a hardened, attestable image (and AMI) for each. The sibling demo repo's `hardened-build-environment` proves the single-flavor pattern; this change generalizes it to many flavors so each can be built, published by digest, and baked into its own attestable AMI.

## What Changes

- Introduce a developer-facing definition of multiple **build-environment flavors** (e.g. `projects.yml` + a shared hardened base + a thin per-flavor Dockerfile). Each flavor declares its environment (base image, toolchain, OS packages, checksummed out-of-band downloads) and its sandbox config overrides.
- Add a GitHub Actions pipeline that builds a **hardened execution-container image per flavor**, satisfying the executor's hardened contract (rootless `65534`, world-exec tools on PATH, pinned, no run-time install), and publishes each to GHCR **by immutable digest**.
- Bake **each flavor's image into its own AMI** (one AMI per flavor), with that flavor's effective sandbox config, keeping the GHCR image as the canonical/provenance reference.
- Scope boundary (**not** a guest-build tool): this repo defines build **environments only**. Guest project source and guest build scripts stay in guest repos and are cloned by the executor at run time. A flavor definition carries no build script, no compile hook, and no artifact contract; the image ships `oras`/`curl`/CA-certs as tools but no build orchestration.
- Sandbox config is **secure-by-default, per-flavor-overridable**: every security-relevant `container-security` setting defaults to its hardened value and may be overridden explicitly per flavor; the effective config is baked into that flavor's AMI, shown in the build-time configuration summary, and bound into the runtime attestation.

This proposal deliberately leaves several **open questions unresolved**, to be settled before design/implementation:

- **Q1 — Flavor manifest schema**: concrete shape of the flavor definition (base image + toolchain + OS packages + checksummed downloads + sandbox-config overrides; no build/artifact fields).
- **Q2 — Rebuild strategy / cost**: N full KIWI→AMI builds; path-filtered "build only changed flavors"; a shared-base change invalidating all N; matrix concurrency.
- **Q4 — Verifier model**: where the consumer verifies (runtime NitroTPM vs. build-time provenance) and how expected PCR4 is published/mapped per flavor.
- **Q5 — Baking feasibility**: whether `docker load` into the verity-sealed erofs read-only root, with docker data-root on the ephemeral overlay, works at run time.
- **Q6 — Per-flavor scratch sizing**: RAM-backed tmpfs sizing and instance-memory implications (demo assumed ≥4 GiB vs. this repo's `256m` default).
- **Q8 — Spec scoping**: which existing capabilities need delta specs vs. being subsumed here, and whether this ships as one change or several.
- **Q9 — Flavor → executor routing**: with one executor per flavor, how a caller targets the right one.
- **Q-new — Optional guest-side build-wrapper**: whether this repo should also publish a reusable guest-side composite action (separable).
- **Terminology**: whether "project" / "flavor" / "build environment" / "runner flavor" is the right user-facing term.

## Capabilities

### New Capabilities
- `execution-build-images`: Define multiple hardened build-environment flavors; build and publish a hardened execution-container image per flavor to GHCR by digest; bake each into its own attestable AMI carrying that flavor's secure-by-default, per-flavor sandbox configuration.

### Modified Capabilities
<!-- Deferred pending open question Q8 (spec scoping). Baking per-flavor images and
     config plausibly changes requirements in `image-build`, `ami-build`, and
     `container-security`, but whether those capabilities need delta specs (and whether
     this lands as one change or several) is the explicit open question Q8 above,
     so no delta specs are authored at the proposal stage. -->

## Impact

- **New artifacts**: a flavor manifest (e.g. `projects.yml`), a shared hardened base image definition, per-flavor Dockerfiles, and a per-flavor build/publish/build-ami workflow (matrix).
- **Affected existing capabilities/code (pending Q8)**: `image-build` (KIWI build parameterized and baking a per-flavor image), `ami-build` (one AMI per flavor), `container-security` (per-flavor effective config baked and summarized). PCR4 already binds the baked image/config via whole-root dm-verity (the verity root hash is embedded in the UKI that PCR4 measures), so no executor `user_data` change is required for integrity.
- **CI cost**: N full KIWI→AMI builds; needs a rebuild strategy (open question Q2).
- **No change to**: the guest-side build flow (`remote-executor` execution, cloning, `request-encryption`) — guests still ship their own source and build scripts.
