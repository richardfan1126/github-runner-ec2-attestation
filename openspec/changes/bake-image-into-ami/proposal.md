## Why

Today the Remote Executor obtains its execution-container image by **pulling it from GHCR by digest at server startup** (`image-build` → "Container image pull at server startup"). That makes the image a *runtime-asserted* input: the executor depends on registry reachability at boot, the image bytes are never measured into the boot attestation, and the digest binding rests on the daemon's `RepoDigests` after a network pull.

This change replaces the runtime pull with a **baked-into-AMI OCI image layout that is verified offline** and folded into the dm-verity-sealed root, so the image becomes an *attested-at-rest* input measured by PCR4. It is deliberately scoped to the **single, externally-supplied image used today** — it does **not** introduce building the image, multiple flavors, or per-flavor configuration.

It exists as a standalone change because it is the **prerequisite mechanism** for the multi-flavor `execution-build-images` change (Change 2): that change binds *per-flavor* sandbox config to PCR4, which is only possible once the image (and later the config) is baked rather than pulled. Landing the bake mechanism single-flavor first **validates the riskiest claim** — offline OCI-layout import on the rootless `fuse-overlayfs` RAM overlay, with image-ID binding to survive the `RepoDigests` loss — before the multi-flavor scaffolding (manifest schema, dynamic matrix, N× AMI cost) is built on top of it.

## What Changes

- Replace the executor's **startup `docker pull` + `RepoDigests` digest check** with an **offline verification of a baked OCI image layout**: the executor verifies the baked layout's manifest digest against the expected value (no registry, no network), imports it into the existing rootless Docker daemon, and binds `containers.create()` to the resulting **image ID** (config digest) rather than `RepoDigests`.
- Bake the externally-supplied image into the AMI as an **OCI image layout copied by digest** into the KIWI root tree, covered by `verity_blocks="all"` so its bytes are measured into **PCR4**.
- Surface the **manifest digest** (`container_image_digest`) in the attestation `user_data` so the runtime NitroTPM attestation is self-describing.
- Emit a **minimal, durable single-entry verifier record** (image manifest digest → PCR4 → AMI id → producing commit) as the seed that Change 2 generalizes into the multi-flavor `flavors.lock`.

Explicit **non-goals** (all owned by Change 2, `execution-build-images`):

- Building the execution-container image (it stays externally supplied and digest-pinned, exactly as today).
- Multiple flavors / a flavor manifest / per-flavor Dockerfiles.
- Baking sandbox config or binding per-flavor config to PCR4 (`container-security` is **untouched** here; config stays runtime operator-set).
- Selective rebuild / dynamic matrix.

## Resolved Decisions

### D1 — Image baking format and runtime binding *(relocated from `execution-build-images`)*

**Decision**: bake the image into the AMI as an **OCI image layout** placed inside the dm-verity-sealed erofs root (covered by `verity_blocks="all"`, so its bytes are measured into PCR4). At run time the executor verifies the baked layout's **manifest digest** against the expected value **offline** (no registry, no network), imports it into the existing rootless Docker daemon, and binds container creation to the resulting **image ID** (config digest) rather than the daemon's `RepoDigests`. (This is "Option A"; the rejected "Option B" was switching the daemon to the containerd image store.)

**Rationale**:

- **Filesystem feasibility is the status quo, not a new risk.** With `overlayroot_write_partition="false"`, Docker's `data-root` (`/var/lib/gha-executor/docker`) already lives on the ephemeral tmpfs/RAM overlay above the read-only erofs root, served by source-compiled `fuse-overlayfs`. The shipping product already pulls the image into exactly this location at startup, so "docker data-root on the ephemeral overlay" carries no new feasibility risk.
- **Plain `docker save`/`docker load` cannot be used.** A save/load round-trip drops `RepoDigests` (moby#22011): registries — not local daemons — assign manifest digests, so a loaded image reports digest `<none>`, breaking the executor's existing digest-pinning contract.
- **An OCI layout preserves the digest binding without a registry.** The OCI layout is content-addressed, so the manifest digest travels with the bytes; it is verified offline against the verity-measured layout, and the image ID it commits to is used for execution. This avoids switching the daemon to the containerd image store (Option B), which would entangle the rootless `fuse-overlayfs` snapshotter and re-open all existing hardening.
- **Cost:** the imported image is resident in the RAM overlay for the instance's lifetime, so instance memory must budget the decompressed image size on top of the `256m` `/tmp` scratch and the workspace. (Change 2 multiplies this per flavor — its Q6.)

**Runtime import mechanism (resolves C1-a)**: the import is structured as three separable steps so daemon-reported digests are never trusted:

1. **Verify** — recompute the manifest digest over the baked layout's on-disk manifest blob and compare to the expected `container_image_digest`. Pure offline hashing; no daemon, no network.
2. **Derive** — read the config descriptor *out of that verified manifest*; its digest is the trusted **image ID**. The image ID is therefore derived from verity-measured, digest-verified bytes, independent of anything the daemon reports.
3. **Load + bind** — load the layout into the rootless daemon via `skopeo copy oci:<layout> docker-daemon:<repo>:<tag>` (the daemon runs the legacy graphdriver store — `daemon.json` sets no containerd snapshotter), then call `containers.create(image=<derived image ID>)` instead of today's `repo@sha256:<manifest>` reference string (`script_executor.py:285`). Because execution binds to the image ID — preserved across load even though `RepoDigests` and the post-load manifest digest are not — the `RepoDigests` loss is moot.

**The import tool's only obligation** is to not mutate the **config blob** (which would change the image ID). Losing `RepoDigests`, recompressing layers (the config references *uncompressed* `diff_ids`), and OCI→docker-schema2 manifest media-type conversion (the media type lives in the manifest, not the config blob) all leave the config blob — and thus the image ID — intact; only a tool that rewrites the config JSON would break it.

**Design-time spike (the residual C1-a risk)**: confirm that `skopeo copy oci:… docker-daemon:…` on the rootless `fuse-overlayfs` daemon produces a local image whose ID equals the config digest read from the verified manifest, for **both** an OCI-media-type and a docker-media-type source image. If skopeo rewrites the config, fall back to **serving the baked layout from a transient `127.0.0.1` registry and `docker pull`-ing it** (a real pull is faithful by construction and even preserves `RepoDigests`; Docker auto-treats `127.0.0.0/8` as insecure-allowed, so no `daemon.json` change). Both paths bake the same deterministic OCI layout; they differ only in the runtime import step.

**Rejected — bake the populated daemon store** (pull at build time, freeze `/var/lib/gha-executor/docker` into erofs): preserves `RepoDigests` with no runtime import and no binding change, but producing a faithful overlay2 store requires running nested rootless `fuse-overlayfs` Docker *inside the KIWI builder* and then booting the daemon off a read-only erofs lower — a larger feasibility surface than the layout import it would replace, working against this change's purpose of de-risking the simplest viable mechanism first. (Under the build-provenance trust model the store's non-reproducible overlay2 cache IDs are not themselves disqualifying — third parties trust the attested GHA run, not a reproduction — but the build-time DinD requirement is what rejects it.)

### D2 — Canonical verifier anchor: the GHCR manifest digest (single-flavor core) *(relocated from `execution-build-images`)*

**Decision**: the **GHCR manifest digest is the single canonical identifier** the executor verifies the baked layout against and external consumers verify against. The baked OCI layout is byte-identical to the GHCR artifact under the same digest.

**Rationale and properties**:

- **Image authenticity is verifiable without the AMI.** A third party pulls `ghcr.io/<owner>/<image>@sha256:<manifest>` (digest-pinned, verified client-side), verifies the Sigstore build-provenance attestation, and inspects the image — none of which touches the AMI.
- **The image ID is identical across both distributions, by definition.** Per the OCI image-spec the image ID is `SHA256(config JSON)`; the manifest references the config by digest, so the manifest digest deterministically commits to the image ID, independent of registry-vs-local and pull-vs-load. The `RepoDigests` loss is therefore irrelevant because it never affects the image ID.
- **Runtime binding remains an AMI/attestation property**: proving *this instance runs that image* uses the NitroTPM runtime attestation with PCR4 binding the baked layout. The two surfaces — GHCR/Sigstore for image authenticity, NitroTPM/PCR4 for runtime binding — join at the shared manifest-digest anchor.

**Two byte-identity constraints the build pipeline must honor**:

1. **Single-platform anchor.** The anchor must resolve to the `linux/amd64` manifest (the AMI architecture); a multi-platform index digest resolves per-host and yields a different image ID off-platform.
2. **Digest-preserving copy.** The baked layout must be copied from the GHCR artifact by digest (e.g. `oras cp` / `skopeo copy` preserving digests), never rebuilt or media-type-converted, which would rewrite the config JSON and change the image ID.

### D-rec — Minimal single-entry verifier record (seed of `flavors.lock`)

**Decision**: the build emits a **durable, committed single-entry record** mapping the baked image's manifest digest → PCR4 → AMI id → producing commit. In this change it is one entry; Change 2 generalizes the same record into the per-flavor `flavors.lock` (flavor → the same fields).

**Rationale**: keeping the verifier mapping continuous from the start avoids Change 2 retrofitting a record format onto an already-shipped AMI, and gives external verifiers a stable artifact to read (commit → digest → PCR4 → AMI) even in the single-flavor world.

## Capabilities

### Modified Capabilities

- `image-build`: the "Container image pull at server startup" requirement shifts from a network `docker pull` + `RepoDigests` check to an **offline manifest-digest verification of a baked OCI layout + image-ID execution** (D1/D2). The build copies the externally-supplied image by digest into the KIWI root tree as an OCI layout and surfaces `container_image_digest` in attestation `user_data`.
- `ami-build`: the AMI carries the baked OCI layout inside the verity-sealed root, and the build emits the minimal single-entry verifier record (D-rec).

### New Capabilities

<!-- None. This change modifies how the existing single image reaches the executor; it
     introduces no new developer-facing capability. The verifier record (D-rec) is a delta
     to ami-build's output, not a new capability — Change 2 promotes it into the new
     flavor-fleet capability's flavors.lock. -->

## Impact

- **New artifacts**: an OCI image layout copied **by digest** into the KIWI root tree; a committed single-entry verifier record (D-rec).
- **Affected existing capabilities/code**: `image-build` (pull-at-startup → offline baked-layout verification + image-ID execution), `ami-build` (root tree carries the OCI layout; emits the verifier record). PCR4 already binds the baked image via whole-root dm-verity, so no executor `user_data` change is required for integrity (D2 surfaces `container_image_digest` for self-description).
- **Memory cost**: the decompressed image is resident in the RAM overlay for the instance lifetime — instance memory must budget it on top of `256m` `/tmp` and the workspace.
- **No change to**: `container-security` (sandbox config stays runtime operator-set), the guest-side build flow (`remote-executor`, cloning, `request-encryption`), and the number of images/AMIs (still one).
- **Prerequisite for**: `execution-build-images` (Change 2), which multiplies this mechanism to N flavors and adds per-flavor baked config bound by PCR4.
