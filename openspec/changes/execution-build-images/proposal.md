## Why

Today this repository builds a single attestable AMI that runs the Remote Executor against one externally-supplied execution-container image (pulled by digest at startup). There is no first-class way for a developer to declare several distinct build *environments* — each with its own toolchain, OS packages, and sandbox posture — and have CI produce a hardened, attestable image (and AMI) for each. The sibling demo repo's `hardened-build-environment` proves the single-flavor pattern; this change generalizes it to many flavors so each can be built, published by digest, and baked into its own attestable AMI.

## What Changes

- Introduce a developer-facing definition of multiple **build-environment flavors** (e.g. `projects.yml` + a shared hardened base + a thin per-flavor Dockerfile). Each flavor declares its environment (base image, toolchain, OS packages, checksummed out-of-band downloads) and its sandbox config overrides.
- Add a GitHub Actions pipeline that builds a **hardened execution-container image per flavor**, satisfying the executor's hardened contract (rootless `65534`, world-exec tools on PATH, pinned, no run-time install), and publishes each to GHCR **by immutable digest**.
- Bake **each flavor's image into its own AMI** (one AMI per flavor) as a digest-preserving **OCI image layout** inside the dm-verity-sealed erofs root, with that flavor's effective sandbox config, keeping the GHCR image (same manifest digest) as the canonical/provenance reference. The baking *mechanism* itself (OCI-layout format, offline verification, image-ID runtime binding, GHCR-digest anchor) is delivered single-flavor by the **prerequisite change `bake-image-into-ami` (Change 1)** — see **Prerequisite & scope split (D5)** below; this change multiplies it to N flavors and adds the per-flavor baked sandbox config.
- Scope boundary (**not** a guest-build tool): this repo defines build **environments only**. Guest project source and guest build scripts stay in guest repos and are cloned by the executor at run time. A flavor definition carries no build script, no compile hook, and no artifact contract; the image ships `oras`/`curl`/CA-certs as tools but no build orchestration.
- Sandbox config is **secure-by-default, per-flavor-overridable**: every security-relevant `container-security` setting defaults to its hardened value and may be overridden explicitly per flavor; the effective config is baked into that flavor's AMI, shown in the build-time configuration summary, and bound into the runtime attestation.

This proposal deliberately leaves several **open questions unresolved**, to be settled before design/implementation (some are now resolved — see **Resolved Decisions** below):

- **Q1 — Flavor manifest schema**: concrete shape of the flavor definition (base image + toolchain + OS packages + checksummed downloads + sandbox-config overrides; no build/artifact fields).
- **Q2 — Rebuild strategy / cost** *(resolved by D4)*.
- **Q4 — Verifier model** *(largely resolved by Change 1's D2 and this change's D4)*: the canonical anchor (the per-flavor GHCR manifest digest) and the dual GHCR/Sigstore-vs-NitroTPM/PCR4 verification surfaces are settled by Change 1's D2; the per-flavor publication/mapping mechanism is the durable `flavors.lock` record in D4 (generalizing Change 1's single-entry verifier record). What remains open is only **where that record lives** (git-committed source of truth vs. SSM mirror — see D4) and **where/how a consumer is expected to run the runtime NitroTPM attestation check**.
- **Q5 — Baking feasibility** *(resolved by Change 1's D1)*.
- **Q6 — Per-flavor scratch + image RAM sizing**: RAM-backed tmpfs sizing and instance-memory implications — now including the decompressed baked image resident in the RAM overlay (per Change 1's D1) on top of the `256m` `/tmp` default (demo assumed ≥4 GiB vs. this repo's `256m` default).
- **Q8 — Spec scoping** *(resolved by D5)*: no existing capability is subsumed; `image-build`/`ami-build`/`container-security` receive delta specs and this change adds one new flavor-fleet capability, sequenced after the `bake-image-into-ami` prerequisite — see D5.
- **Q9 — Flavor → executor routing**: given one AMI/executor per flavor (D3), how a caller targets the right one.
- **Q-new — Optional guest-side build-wrapper**: whether this repo should also publish a reusable guest-side composite action (separable).
- **Terminology**: whether "project" / "flavor" / "build environment" / "runner flavor" is the right user-facing term.

## Resolved Decisions

### D5 — Prerequisite & scope split: bake first (single flavor), then multiply (resolves Q8)

**Decision**: deliver the work as **two sequenced changes** rather than one. The bake-instead-of-pull *mechanism* ships single-flavor in the prerequisite change **`bake-image-into-ami` (Change 1)**; this change (`execution-build-images`, Change 2) depends on it and adds the multi-flavor fleet on top.

**Rationale**:

- **Two orthogonal axes were bundled.** Axis 1 = *how* the image reaches the executor (runtime pull → baked, offline-verified). Axis 2 = *how many* environments (one → N flavors). Axis 2 depends on Axis 1 (per D3 the per-flavor sandbox config is bound by PCR4, which is only possible once it is baked), but Axis 1 stands alone and is valuable single-flavor (removes the boot-time GHCR dependency; folds the image into measured boot).
- **Risk placement.** D1 (offline OCI-layout import on the rootless `fuse-overlayfs` RAM overlay, image-ID binding to survive the `RepoDigests` loss) is the most novel, least-proven claim. Change 1 proves it single-flavor *before* the manifest schema, dynamic matrix, and N× AMI cost are built on top — and Change 1's runtime code is exactly what this change reuses, so rework risk is low.

**Spec-scoping consequence (the other half of Q8)**: **nothing is subsumed.** `image-build`, `ami-build`, and `container-security` receive **delta specs** (split across the two changes); this change adds **one new flavor-fleet capability** (the flavor manifest + selective rebuild + `flavors.lock`). No mega-capability that re-owns image building.

**Decision migration**: **D1** and the single-flavor **core of D2** (GHCR-digest anchor, image-ID binding, the two byte-identity constraints) move to Change 1. They remain referenced here because this change *multiplies* them per flavor; D3 and D4 (and the per-flavor multiplication of D2) stay in this change.

### D3 — One AMI per flavor (confirmed)

**Decision**: each flavor produces its own attestable AMI, carrying that flavor's baked OCI layout (D1) and its effective sandbox config. A single AMI is **not** shared across flavors.

**Rationale and consequence**:

- The flavor's sandbox config is baked into the verity root and bound by **PCR4**, consistent with D1's bake posture (attested-at-rest over runtime-asserted). A shared multi-flavor AMI would force per-flavor config to be runtime-selected and bound only via `user_data`, and would give every flavor an identical PCR4 (so PCR4 could no longer distinguish flavors).
- **Consequence for rebuilds:** because the config lives in the measured image, a **config-only change is a full vertical rebuild** of that flavor — there is no cheap "re-tag" path. (Feeds D4.)
- This is the N-multiplier behind Q6 (per-flavor RAM), Q9 (one executor per flavor → routing), and D4 (selective rebuild).

### D4 — Selective rebuild via a two-level invalidation graph and a durable `flavors.lock` record (resolves Q2)

**Decision**: the per-flavor build/publish/AMI pipeline rebuilds **only the flavors whose inputs changed**, computed by a `detect-changes` job that maps changed paths to affected flavors and emits a **dynamic build matrix**. Per-flavor results are recorded in a **durable `flavors.lock`** mapping (flavor → image manifest digest, PCR4, AMI id, producing commit) so that flavors not rebuilt in a given run are carried forward rather than orphaned.

**Two-level invalidation graph**:

- **Global invalidators → rebuild ALL flavors**: the shared hardened base image, the build machinery (`.github/scripts/**`, the KIWI builder Dockerfile), the shared executor OS (`kiwi-descriptions/**`), reproducibility pins (`uv.lock`, `pyproject.toml`, `appliance.kiwi`), the workflow itself, and the path-map logic.
- **Per-flavor invalidators → rebuild only that flavor**: `flavors/<f>/Dockerfile` (image inputs), the flavor's sandbox config (AMI inputs only — still a full rebuild per D3), and the flavor's stanza in the flavor manifest.
- **Two levels, so image builds are skipped when only AMI inputs changed**: AMI-only changes (e.g. `kiwi-descriptions/**` or a sandbox-config edit) re-bake the AMI while **reusing the existing image by digest from `flavors.lock`**, avoiding an unnecessary image rebuild.

**Durable record (`flavors.lock`)**:

- Selective rebuild and the durable record are a **package deal**: "only changed flavors rebuild" requires "unchanged flavors are remembered durably." `flavors.lock` is that memory, and it **doubles as Q4's per-flavor verifier mapping** (flavor → expected PCR4 + manifest digest + AMI). It **generalizes Change 1's minimal single-entry verifier record** (D-rec) from one image to N flavors, reusing the same fields so the verifier story is continuous across the split.
- **Open sub-decision (deferred to design):** where `flavors.lock` lives — a **git-committed source of truth** (best auditability; verifier reads flavor → PCR4 → digest → AMI from history, tied to the producing commit) optionally mirrored to **SSM Parameter Store** for the `deployment` side to consume.

**Fail-safe and edge-case requirements** (to be specified at design time):

- **No diff baseline** (initial commit, force-push, fork PR, shallow clone) → **fail safe to "build ALL"**; never silently build nothing.
- **Empty changed set** (e.g. docs-only) → empty matrix; downstream jobs skip cleanly and `flavors.lock` is untouched.
- **`workflow_dispatch` override** → force a specific flavor or `all`; `enable_ssh` Debug_Builds target a single flavor and must not overwrite that flavor's production `flavors.lock` entry (compose with the existing `debug=true` converter gate).
- **Concurrency** → bound matrix `max-parallel` (each AMI build is an EC2 instance → quota/cost) and serialize `flavors.lock` updates with a concurrency group.
- **`develop` vs `main`** → on `develop`, build/publish changed-flavor images for testing but skip AMIs; full vertical only on `main` (composing with the existing `build-ami` develop-skip rule).
- **Auditability** → `detect-changes` records its rebuild decision (commit → flavors) in the run summary; the changed-set logic is itself a verifier trust input.

## Capabilities

### New Capabilities
- `execution-build-images`: Define multiple hardened build-environment flavors; build and publish a hardened execution-container image per flavor to GHCR by digest; bake each into its own attestable AMI carrying that flavor's secure-by-default, per-flavor sandbox configuration.

### Modified Capabilities

Per D5 (Q8 resolved), this change authors delta specs for the following — none is subsumed:

- `image-build`: KIWI build **parameterized 1 → N per flavor**, each baking that flavor's OCI layout. (The pull → offline-verified-bake *mechanism* itself is delivered by Change 1; this change multiplies it.)
- `ami-build`: **one AMI per flavor** driven by a dynamic matrix.
- `container-security`: per-flavor **effective sandbox config baked into the verity root and bound by PCR4** (the shift from runtime operator-set config; `container-security` is untouched by Change 1).

## Impact

- **Prerequisite**: depends on **`bake-image-into-ami` (Change 1)**, which delivers the single-flavor bake mechanism (D1 + D2-core) and the minimal single-entry verifier record this change generalizes.
- **New artifacts**: a flavor manifest (e.g. `projects.yml`), a shared hardened base image definition, per-flavor Dockerfiles, a per-flavor build/publish/build-ami workflow (matrix), and a per-flavor OCI image layout copied **by digest** into the KIWI root tree (reusing Change 1's bake path).
- **Affected existing capabilities/code (per D5)**: `image-build` (KIWI build parameterized 1 → N and baking a per-flavor OCI layout — building on Change 1's pull → offline-bake mechanism), `ami-build` (one AMI per flavor), `container-security` (per-flavor effective config baked and summarized, bound by PCR4). PCR4 already binds the baked image/config via whole-root dm-verity (the verity root hash is embedded in the UKI that PCR4 measures), so no executor `user_data` change is required for integrity (Change 1's D2 surfaces `container_image_digest` in `user_data` to make the runtime attestation self-describing).
- **CI cost**: up to N full KIWI→AMI builds in the worst case (a global invalidator), but per-commit cost is bounded to the changed flavors by the selective-rebuild strategy in D4 (with image builds further skipped on AMI-only changes).
- **New persistent state**: a durable `flavors.lock` record (D4) mapping each flavor to its image manifest digest, PCR4, AMI id, and producing commit — carried forward across selective-rebuild runs and doubling as Q4's verifier mapping.
- **No change to**: the guest-side build flow (`remote-executor` execution, cloning, `request-encryption`) — guests still ship their own source and build scripts.
