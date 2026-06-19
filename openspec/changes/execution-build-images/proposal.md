## Why

Today this repository builds a single attestable AMI that runs the Remote Executor against one externally-supplied execution-container image (pulled by digest at startup). There is no first-class way for a developer to declare several distinct build *environments* — each with its own toolchain, OS packages, and sandbox posture — and have CI produce a hardened, attestable image (and AMI) for each. The sibling demo repo's `hardened-build-environment` proves the single-flavor pattern; this change generalizes it to many flavors so each can be built, published by digest, and baked into its own attestable AMI.

## What Changes

- Introduce a developer-facing definition of multiple **build-environment flavors** (e.g. `projects.yml` + a shared hardened base + a thin per-flavor Dockerfile). Each flavor declares its environment (base image, toolchain, OS packages, checksummed out-of-band downloads) and its sandbox config overrides.
- Add a GitHub Actions pipeline that builds a **hardened execution-container image per flavor**, satisfying the executor's hardened contract (rootless `65534`, world-exec tools on PATH, pinned, no run-time install), and publishes each to GHCR **by immutable digest**.
- Bake **each flavor's image into its own AMI** (one AMI per flavor) as a digest-preserving **OCI image layout** inside the dm-verity-sealed erofs root, with that flavor's effective sandbox config, keeping the GHCR image (same manifest digest) as the canonical/provenance reference. See **Resolved Decisions** D1–D2 below for the baking format, runtime binding, and verifier anchor.
- Scope boundary (**not** a guest-build tool): this repo defines build **environments only**. Guest project source and guest build scripts stay in guest repos and are cloned by the executor at run time. A flavor definition carries no build script, no compile hook, and no artifact contract; the image ships `oras`/`curl`/CA-certs as tools but no build orchestration.
- Sandbox config is **secure-by-default, per-flavor-overridable**: every security-relevant `container-security` setting defaults to its hardened value and may be overridden explicitly per flavor; the effective config is baked into that flavor's AMI, shown in the build-time configuration summary, and bound into the runtime attestation.

This proposal deliberately leaves several **open questions unresolved**, to be settled before design/implementation (some are now resolved — see **Resolved Decisions** below):

- **Q1 — Flavor manifest schema**: concrete shape of the flavor definition (base image + toolchain + OS packages + checksummed downloads + sandbox-config overrides; no build/artifact fields).
- **Q2 — Rebuild strategy / cost** *(resolved by D4)*.
- **Q4 — Verifier model** *(largely resolved by D2 and D4)*: the canonical anchor (the per-flavor GHCR manifest digest) and the dual GHCR/Sigstore-vs-NitroTPM/PCR4 verification surfaces are settled in D2; the per-flavor publication/mapping mechanism is the durable `flavors.lock` record in D4. What remains open is only **where that record lives** (git-committed source of truth vs. SSM mirror — see D4) and **where/how a consumer is expected to run the runtime NitroTPM attestation check**.
- **Q5 — Baking feasibility** *(resolved by D1)*.
- **Q6 — Per-flavor scratch + image RAM sizing**: RAM-backed tmpfs sizing and instance-memory implications — now including the decompressed baked image resident in the RAM overlay (per D1) on top of the `256m` `/tmp` default (demo assumed ≥4 GiB vs. this repo's `256m` default).
- **Q8 — Spec scoping**: which existing capabilities need delta specs vs. being subsumed here, and whether this ships as one change or several.
- **Q9 — Flavor → executor routing**: given one AMI/executor per flavor (D3), how a caller targets the right one.
- **Q-new — Optional guest-side build-wrapper**: whether this repo should also publish a reusable guest-side composite action (separable).
- **Terminology**: whether "project" / "flavor" / "build environment" / "runner flavor" is the right user-facing term.

## Resolved Decisions

### D1 — Image baking format and runtime binding (resolves Q5)

**Decision**: bake each flavor's execution-container image into the AMI as an **OCI image layout** placed inside the dm-verity-sealed erofs root (covered by `verity_blocks="all"`, so its bytes are measured into PCR4). At run time the executor verifies the baked layout's **manifest digest** against the expected per-flavor value **offline** (no registry, no network), imports it into the existing rootless Docker daemon, and binds container creation to the resulting **image ID** (config digest) rather than the daemon's `RepoDigests`. (This is "Option A"; the rejected "Option B" was switching the daemon to the containerd image store.)

**Rationale**:

- **Filesystem feasibility is the status quo, not a new risk.** With `overlayroot_write_partition="false"`, Docker's `data-root` (`/var/lib/gha-executor/docker`) already lives on the ephemeral tmpfs/RAM overlay above the read-only erofs root, served by source-compiled `fuse-overlayfs` (kernel `overlay2` is documented as unsupported on an overlayfs backing). The shipping product already pulls the image into exactly this location at startup, so "docker data-root on the ephemeral overlay" carries no new feasibility risk.
- **Plain `docker save`/`docker load` cannot be used.** A save/load round-trip drops `RepoDigests` (moby#22011): registries — not local daemons — assign manifest digests, so a loaded image reports digest `<none>`, breaking the executor's existing digest-pinning contract.
- **An OCI layout preserves the digest binding without a registry.** The OCI layout is content-addressed, so the manifest digest travels with the bytes; it is verified offline against the verity-measured layout, and the image ID it commits to is used for execution. This avoids switching the daemon to the containerd image store (Option B), which would entangle the rootless `fuse-overlayfs` snapshotter and re-open all existing hardening — a larger blast radius for a property Option A obtains without it.
- **Cost (feeds Q6):** the imported image is resident in the RAM overlay for the instance's lifetime, so per-flavor instance memory must budget the decompressed image size on top of the `256m` `/tmp` scratch and the workspace.

### D2 — Canonical verifier anchor: the GHCR manifest digest (resolves the anchor part of Q4)

**Decision**: the **per-flavor GHCR manifest digest is the single canonical identifier** the build publishes, the executor verifies the baked layout against, and external consumers verify against. The build pushes each flavor to GHCR by digest (canonical/provenance reference) and bakes the byte-identical OCI layout into the AMI under the same digest.

**Rationale and properties**:

- **Image authenticity is verifiable without the AMI.** A third party pulls `ghcr.io/<owner>/<image>@sha256:<manifest>` (digest-pinned, verified client-side), verifies the Sigstore build-provenance attestation, and inspects the image — none of which touches the AMI.
- **The image ID is identical across both distributions, by definition.** Per the OCI image-spec, the image ID is `SHA256(config JSON)`, content-addressable and immutable; the manifest references the config by digest, so the manifest digest deterministically commits to the config digest = image ID. Image ID is independent of registry-vs-local, pull-vs-load, and graphdriver-vs-containerd store — therefore the image ID a consumer computes from GHCR equals the image ID the AMI executes, and the `RepoDigests` loss is irrelevant because it never affects the image ID.
- **Runtime binding remains an AMI/attestation property** (intrinsic): proving *this instance runs that image* uses the NitroTPM runtime attestation, with PCR4 binding the baked layout (it is in the verity root). The two surfaces — GHCR/Sigstore for image authenticity, NitroTPM/PCR4 for runtime binding — join at the shared manifest-digest anchor. Surfacing `container_image_digest` (the manifest digest) in the attestation `user_data` makes the runtime attestation self-describing.

**Two byte-identity constraints the build pipeline must honor**:

1. **Single-platform anchor.** The per-flavor anchor must resolve to the `linux/amd64` manifest (the AMI architecture); a multi-platform index digest resolves per-host and yields a different image ID off-platform.
2. **Digest-preserving copy.** The baked layout must be copied from the GHCR artifact by digest (e.g. `oras cp` / `skopeo copy` preserving digests), never rebuilt or media-type-converted, which would rewrite the config JSON and change the image ID.

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

- Selective rebuild and the durable record are a **package deal**: "only changed flavors rebuild" requires "unchanged flavors are remembered durably." `flavors.lock` is that memory, and it **doubles as Q4's per-flavor verifier mapping** (flavor → expected PCR4 + manifest digest + AMI).
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
<!-- Deferred pending open question Q8 (spec scoping). Baking per-flavor images and
     config plausibly changes requirements in `image-build`, `ami-build`, and
     `container-security`, but whether those capabilities need delta specs (and whether
     this lands as one change or several) is the explicit open question Q8 above,
     so no delta specs are authored at the proposal stage. -->

## Impact

- **New artifacts**: a flavor manifest (e.g. `projects.yml`), a shared hardened base image definition, per-flavor Dockerfiles, a per-flavor build/publish/build-ami workflow (matrix), and a per-flavor OCI image layout copied **by digest** into the KIWI root tree (per D1–D2).
- **Affected existing capabilities/code (pending Q8)**: `image-build` (KIWI build parameterized and baking a per-flavor OCI layout; its "Container image pull at server startup" requirement shifts from a network `docker pull`+`RepoDigests` check to an **offline manifest-digest verification of the baked layout + image-ID execution** per D1 — a delta to be authored at the design phase), `ami-build` (one AMI per flavor), `container-security` (per-flavor effective config baked and summarized). PCR4 already binds the baked image/config via whole-root dm-verity (the verity root hash is embedded in the UKI that PCR4 measures), so no executor `user_data` change is required for integrity (though D2 surfaces `container_image_digest` in `user_data` to make the runtime attestation self-describing).
- **CI cost**: up to N full KIWI→AMI builds in the worst case (a global invalidator), but per-commit cost is bounded to the changed flavors by the selective-rebuild strategy in D4 (with image builds further skipped on AMI-only changes).
- **New persistent state**: a durable `flavors.lock` record (D4) mapping each flavor to its image manifest digest, PCR4, AMI id, and producing commit — carried forward across selective-rebuild runs and doubling as Q4's verifier mapping.
- **No change to**: the guest-side build flow (`remote-executor` execution, cloning, `request-encryption`) — guests still ship their own source and build scripts.
