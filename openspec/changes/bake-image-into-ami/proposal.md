## Why

Today the Remote Executor obtains its execution-container image by **pulling it from GHCR by digest at server startup** (`image-build` → "Container image pull at server startup"). That makes the image a *runtime-asserted* input: the executor depends on registry reachability at boot, the image bytes are never measured into the boot attestation, and the digest binding rests on the daemon's `RepoDigests` after a network pull.

This change replaces the runtime pull with a **baked-into-AMI image (a docker-archive + OCI-manifest sidecar) that is verified offline** and folded into the dm-verity-sealed root, so the image becomes an *attested-at-rest* input measured by PCR4. It is deliberately scoped to the **single, externally-supplied image used today** — it does **not** introduce building the image, multiple flavors, or per-flavor configuration.

It exists as a standalone change because it is the **prerequisite mechanism** for the multi-flavor `execution-build-images` change (Change 2): that change binds *per-flavor* sandbox config to PCR4, which is only possible once the image (and later the config) is baked rather than pulled. Landing the bake mechanism single-flavor first **validates the riskiest claim** — offline OCI-layout import on the rootless `fuse-overlayfs` RAM overlay, with image-ID binding to survive the `RepoDigests` loss — before the multi-flavor scaffolding (manifest schema, dynamic matrix, N× AMI cost) is built on top of it.

## What Changes

- Replace the executor's **startup `docker pull` + `RepoDigests` digest check** with an **offline verification of a baked OCI-manifest sidecar**: the executor verifies the sidecar's manifest digest against the expected value (no registry, no network), `docker load`s the baked docker-archive into the existing rootless Docker daemon, and binds `containers.create()` to the resulting **image ID** (config digest) rather than `RepoDigests`.
- Bake the externally-supplied image into the AMI as a **docker-archive plus an OCI-manifest sidecar** (derived by digest from an OCI-layout intermediate) in the KIWI root tree, covered by `verity_blocks="all"` so their bytes are measured into **PCR4**.
- Surface the **manifest digest** (`container_image_digest`) through the build pipeline's **publish-time outputs** — the GHA job log and step summary, an ORAS annotation on the published artifact, and a tag on the registered AMI — so external verifiers can read which image the AMI carries without running the instance. The runtime NitroTPM attestation and its `user_data` are **unchanged**: PCR4 already binds the baked image, so self-description is a build-output concern, not an attestation one.
- Emit a **minimal single-entry verifier record** (image manifest digest → PCR4 → AMI id → producing commit), published through the same build-time surfaces above, as the seed that Change 2 generalizes into the durable, committed multi-flavor `flavors.lock`.

Explicit **non-goals** (all owned by Change 2, `execution-build-images`):

- Building the execution-container image (it stays externally supplied and digest-pinned, exactly as today).
- Multiple flavors / a flavor manifest / per-flavor Dockerfiles.
- Baking sandbox config or binding per-flavor config to PCR4 (`container-security` is **untouched** here; config stays runtime operator-set).
- Selective rebuild / dynamic matrix.

## Resolved Decisions

### D1 — Image baking format and runtime binding *(relocated from `execution-build-images`)*

**Decision**: bake the image into the AMI as a **docker-archive plus an OCI-manifest sidecar** (derived by digest from an OCI-layout intermediate) placed inside the dm-verity-sealed erofs root (covered by `verity_blocks="all"`, so their bytes are measured into PCR4). At run time the executor verifies the baked **sidecar's manifest digest** against the expected value **offline** (no registry, no network), `docker load`s the archive into the existing rootless Docker daemon, and binds container creation to the resulting **image ID** (config digest) rather than the daemon's `RepoDigests`. (This is "Option A"; the rejected "Option B" was switching the daemon to the containerd image store.)

**Rationale**:

- **Filesystem feasibility is the status quo, not a new risk.** With `overlayroot_write_partition="false"`, Docker's `data-root` (`/var/lib/gha-executor/docker`) already lives on the ephemeral tmpfs/RAM overlay above the read-only erofs root, served by source-compiled `fuse-overlayfs`. The shipping product already pulls the image into exactly this location at startup, so "docker data-root on the ephemeral overlay" carries no new feasibility risk.
- **Plain `docker save`/`docker load` cannot be used.** A save/load round-trip drops `RepoDigests` (moby#22011): registries — not local daemons — assign manifest digests, so a loaded image reports digest `<none>`, breaking the executor's existing digest-pinning contract.
- **An OCI-manifest sidecar preserves the digest binding without a registry.** The sidecar is the content-addressed manifest blob, so the manifest digest travels with the bytes; it is verified offline against the verity-measured sidecar, and the image ID it commits to is used for execution. This avoids switching the daemon to the containerd image store (Option B), which would entangle the rootless `fuse-overlayfs` snapshotter and re-open all existing hardening.
- **Cost:** the imported image is resident in the RAM overlay for the instance's lifetime, so instance memory must budget the decompressed image size on top of the `256m` `/tmp` scratch and the workspace. (Change 2 multiplies this per flavor — its Q6.)

**Runtime import mechanism (resolves C1-a)** — *offline manifest re-verification is a hard requirement of this change, not an optimization*: before the baked image may run, the executor MUST recompute the canonical `linux/amd64` manifest digest over the baked **OCI-manifest sidecar** and confirm it equals the expected `container_image_digest`, fully offline. This is non-negotiable for three reasons — it is the only step that *relates* the two independently verity-sealed artifacts (the sidecar bytes and the baked expected-digest env file, which dm-verity seals separately but never compares against each other), it is the canonical-anchor join that makes "GHCR-pulled == baked-in-AMI" provable (D2), and it preserves the existing secure-by-default fail-fast-at-startup contract. **Any import mechanism that cannot perform this offline re-verification is disqualified** — notably a bare `docker save`/`docker load` round-trip, which discards the OCI manifest bytes the check needs. The import is therefore structured as three separable steps so daemon-reported digests are never trusted:

1. **Verify** — recompute the manifest digest over the baked OCI-manifest sidecar and compare to the expected `container_image_digest`. Pure offline hashing; no daemon, no network.
2. **Derive** — read the config descriptor *out of that verified manifest*; its digest is the trusted **image ID**. The image ID is therefore derived from verity-measured, digest-verified bytes, independent of anything the daemon reports.
3. **Load + bind** — `docker load` the baked docker-archive into the rootless daemon (decision: **build-time `oci→docker-archive` conversion** baked alongside an OCI-manifest sidecar, then runtime `docker load`, with runtime `skopeo copy oci:→docker-daemon:` as the fallback; the transient `127.0.0.1` registry path is **rejected** — the rootless netns + `--disable-host-loopback` make a loopback registry unreachable; see `design.md`), the daemon running the legacy graphdriver store (`daemon.json` sets no containerd snapshotter); then call `containers.create(image=<derived image ID>)` instead of today's `repo@sha256:<manifest>` reference string (`script_executor.py:285`). Because execution binds to the image ID — preserved across load even though `RepoDigests` and the post-load manifest digest are not — the `RepoDigests` loss is moot.

**The import tool's only obligation** is to not mutate the **config blob** (which would change the image ID). Losing `RepoDigests`, recompressing layers (the config references *uncompressed* `diff_ids`), and OCI→docker-schema2 manifest media-type conversion (the media type lives in the manifest, not the config blob) all leave the config blob — and thus the image ID — intact; only a tool that rewrites the config JSON would break it.

**Design-time spike (the residual C1-a risk)**: for the chosen build-time `oci→docker-archive` conversion (and the skopeo fallback), confirm on the rootless `fuse-overlayfs` daemon that `docker load` produces a local image whose ID equals the config digest read from the verified manifest, for **both** an OCI-media-type and a docker-media-type source image. (Archive byte-reproducibility is **not** a concern — this project's trust anchor is attestation tracing PCR4 → the producing GHA run → commit, not bit-for-bit rebuilds; the bytes measured at build are the bytes booted, which is all PCR4 needs.) Both socket loaders are **fail-closed at bind time** (an unfaithful conversion yields an image whose ID ≠ the derived image ID, so `containers.create()` finds no match and startup fails), so the spike is about *does it work*, not *is it safe*. The rejected transient-`127.0.0.1`-registry path was previously thought spike-free, but the rootless network namespace (separate `lo`) plus stock `dockerd-rootless.sh`'s `--disable-host-loopback` make a loopback registry unreachable from the daemon — the reason it is rejected. All candidate paths derive from the same digest-pinned intermediate OCI layout and share the same offline sidecar-verify step; they differ in which artifact is baked (docker-archive for the leading `docker load` path, the OCI layout itself for the skopeo fallback) and the runtime load tool — so taking the fallback is a build-side choice fixed by the spike outcome, not a per-boot toggle. `design.md` holds the full loader comparison.

**Rejected — bake the populated daemon store** (pull at build time, freeze `/var/lib/gha-executor/docker` into erofs): preserves `RepoDigests` with no runtime import and no binding change, but producing a faithful overlay2 store requires running nested rootless `fuse-overlayfs` Docker *inside the KIWI builder* and then booting the daemon off a read-only erofs lower — a larger feasibility surface than the layout import it would replace, working against this change's purpose of de-risking the simplest viable mechanism first. (Under the build-provenance trust model the store's non-reproducible overlay2 cache IDs are not themselves disqualifying — third parties trust the attested GHA run, not a reproduction — but the build-time DinD requirement is what rejects it.)

### D2 — Canonical verifier anchor: the GHCR manifest digest (single-flavor core) *(relocated from `execution-build-images`)*

**Decision**: the **GHCR manifest digest is the single canonical identifier** the executor verifies the baked sidecar against and external consumers verify against. The baked OCI-manifest sidecar is byte-identical to the GHCR manifest under the same digest.

**Rationale and properties**:

- **Image authenticity is verifiable without the AMI.** A third party pulls `ghcr.io/<owner>/<image>@sha256:<manifest>` (digest-pinned, verified client-side), verifies the Sigstore build-provenance attestation, and inspects the image — none of which touches the AMI.
- **The image ID is identical across both distributions, by definition.** Per the OCI image-spec the image ID is `SHA256(config JSON)`; the manifest references the config by digest, so the manifest digest deterministically commits to the image ID, independent of registry-vs-local and pull-vs-load. The `RepoDigests` loss is therefore irrelevant because it never affects the image ID.
- **Runtime binding remains an AMI/attestation property**: proving *this instance runs that image* uses the NitroTPM runtime attestation with PCR4 binding the baked image (archive + sidecar). The two surfaces — GHCR/Sigstore for image authenticity, NitroTPM/PCR4 for runtime binding — join at the shared manifest-digest anchor.

**Two byte-identity constraints the build pipeline must honor**:

1. **Single-platform anchor.** `imageA` is supplied **single-architecture (`linux/amd64`)**, so its published digest *is* the `linux/amd64` manifest digest — the same value a third party pulls and the executor runs. The anchor MUST be that per-platform manifest digest, never a multi-platform index digest (which resolves per-host and yields a different image ID off-platform); if a multi-arch index is ever supplied, the bake step MUST resolve and pin the `linux/amd64` child manifest before copying.
2. **Digest-preserving copy → config-preserving conversion.** The intermediate OCI layout must be copied from the GHCR artifact by digest (e.g. `oras cp` / `skopeo copy` preserving digests) so the sidecar blob is byte-identical to the GHCR manifest; the subsequent `oci→docker-archive` conversion must preserve the **config blob** so the image ID is unchanged. Neither step may rebuild the image or rewrite the config JSON.

### D-rec — Minimal single-entry verifier record (seed of `flavors.lock`)

**Decision**: the build emits a **single-entry verifier record** mapping the baked image's manifest digest → PCR4 → AMI id → producing commit, **published through the build's publish-time surfaces** (GHA job log + step summary, an ORAS annotation on the published artifact, and a tag on the registered AMI) rather than as a committed in-repo file. In this change it is one entry; Change 2 generalizes the same mapping into the **durable, committed** per-flavor `flavors.lock` (flavor → the same fields).

**Rationale**: keeping the verifier mapping continuous from the start avoids Change 2 retrofitting a record format onto an already-shipped AMI, and gives external verifiers a stable artifact to read (commit → digest → PCR4 → AMI) even in the single-flavor world.

## Capabilities

### Modified Capabilities

- `image-build`: the "Container image pull at server startup" requirement shifts from a network `docker pull` + `RepoDigests` check to an **offline manifest-digest verification of a baked OCI-manifest sidecar + image-ID execution of a baked docker-archive** (D1/D2). The build copies the externally-supplied image by digest into an OCI-layout intermediate, bakes the docker-archive + sidecar into the KIWI root tree, and surfaces `container_image_digest` through the build's publish-time outputs (log, summary, ORAS annotation, AMI tag) rather than in attestation `user_data`.
- `ami-build`: the AMI carries the baked docker-archive + OCI-manifest sidecar inside the verity-sealed root, and the build emits the minimal single-entry verifier record (D-rec).

### New Capabilities

<!-- None. This change modifies how the existing single image reaches the executor; it
     introduces no new developer-facing capability. The verifier record (D-rec) is a delta
     to ami-build's output, not a new capability — Change 2 promotes it into the new
     flavor-fleet capability's flavors.lock. -->

## Impact

- **New artifacts**: a baked **docker-archive + OCI-manifest sidecar** (derived **by digest** from an OCI-layout intermediate) in the KIWI root tree; a single-entry verifier record (D-rec).
- **Affected existing capabilities/code**: `image-build` (pull-at-startup → offline baked-layout verification + image-ID execution), `ami-build` (root tree carries the OCI layout; emits the verifier record via publish-time surfaces). PCR4 already binds the baked image via whole-root dm-verity, so **no executor or `user_data`/attestation change is required**; `container_image_digest` self-description is surfaced through the build's log/summary/ORAS-annotation/AMI-tag outputs instead (D2).
- **Memory cost**: the decompressed image is resident in the RAM overlay for the instance lifetime — instance memory must budget it on top of `256m` `/tmp` and the workspace.
- **No change to**: `container-security` (sandbox config stays runtime operator-set), the guest-side build flow (`remote-executor`, cloning, `request-encryption`), the number of images/AMIs (still one), and the **trust model for the execution image** — it stays an externally-supplied, digest-pinned input and the bake pipeline does **not** add Sigstore/provenance verification of `imageA` (consumers verify it on GHCR themselves).
- **Prerequisite for**: `execution-build-images` (Change 2), which multiplies this mechanism to N flavors and adds per-flavor baked config bound by PCR4.
