## Context

Today the Remote Executor obtains its single execution-container image at **server
startup** by pulling it from GHCR. `script_executor.py:559` (`pull_container_image()`)
calls `images.pull()`, then verifies the pulled image's `RepoDigests` against the
configured `CONTAINER_IMAGE_DIGEST` (`script_executor.py:622`), and execution binds to the
resulting `repository@sha256:<manifest>` reference string in `containers.create()`
(`script_executor.py:284`). Startup fails closed if the digest is absent or mismatched
(`config.py:485`, `main.py:215`).

This makes the image a **runtime-asserted** input: it depends on registry reachability at
boot, its bytes are never measured into the NitroTPM boot attestation, and the digest binding
rests on what the daemon reports after a network pull. The KIWI root is already a
dm-verity-sealed erofs (`verity_blocks="all"`, `overlayroot_write_partition="false"`,
`overlayroot_readonly_filesystem="erofs"`, measured into PCR4), and Docker's `data-root`
(`/var/lib/gha-executor/docker`) already lives on the ephemeral `fuse-overlayfs` RAM overlay
above that read-only root — so the image already lands on exactly the filesystem a baked
layout would use. The **expected digest is itself already baked and verity-sealed**: it ships
in `kiwi-descriptions/root/etc/github-actions-remote-executor/env`
(`CONTAINER_IMAGE=ubuntu:24.04`, `CONTAINER_IMAGE_DIGEST=sha256:…`), so PCR4 already measures
*which* image the operator named.

This change moves the image from runtime-pulled to **attested-at-rest**: bake an OCI image
layout into the verity-sealed root (so PCR4 measures the image bytes), verify it **offline**
at startup, and bind execution to the **image ID** rather than `RepoDigests`. It is the
prerequisite mechanism for the multi-flavor `execution-build-images` change (Change 2);
landing it single-flavor first de-risks the offline-import claim before the multi-flavor
scaffolding is built on top.

**The guarantee this change delivers** (clarified during design): a third party who pulls
`ghcr.io/<owner>/imageA@sha256:<abcd>` and the Remote Executor that runs the baked-in-AMI
image are running the **exact same image**, where `sha256:<abcd>` is the shared, content-
addressed identity. The executor proves this to itself **offline** by re-hashing the baked
layout's manifest against the expected digest; a third party can read which image an AMI
carries from the build's publish-time outputs (log, summary, ORAS annotation, AMI tag).

The proposal (`proposal.md`) holds the motivation and the fully-resolved decisions D1, D2,
and D-rec; this document covers HOW those decisions are realized across the build pipeline and
the runtime executor, plus the risks, migration, and remaining unknowns.

## Goals / Non-Goals

**Goals:**

- Bake the externally-supplied, digest-pinned image into the AMI as an **OCI image layout**,
  copied **by digest** into the KIWI root tree so it is covered by `verity_blocks="all"` and
  measured into PCR4.
- Make `sha256:<abcd>` (the `linux/amd64` manifest digest) the **single shared identity** —
  byte-identical between the GHCR artifact and the baked layout — so "GHCR-pulled == baked-in-
  AMI" holds by construction.
- Replace the startup `docker pull` + `RepoDigests` check with an **offline** verify → derive
  → load+bind sequence that **re-verifies the canonical manifest digest** and never trusts a
  daemon-reported digest.
- Bind `containers.create()` to the **image ID** (config digest) derived from the verity-
  measured, digest-verified manifest, so the loss of `RepoDigests` across import is irrelevant.
- Surface `container_image_digest` for external verifiers through **build publish-time
  outputs** (GHA job log + step summary, an ORAS annotation on the published artifact, and a
  tag on the registered AMI) — **not** through the runtime attestation `user_data`.
- Emit a **single-entry verifier record** (manifest digest → PCR4 → AMI id → producing commit)
  via those same surfaces — the seed Change 2 generalizes into `flavors.lock`.

**Non-Goals:**

- Building the execution-container image — it stays externally supplied and digest-pinned
  (owned by Change 2).
- Multiple flavors, a flavor manifest, per-flavor Dockerfiles, selective rebuild, or a dynamic
  matrix (Change 2).
- Baking sandbox config or binding per-flavor config to PCR4 — `container-security` is
  untouched; sandbox config stays runtime operator-set (Change 2).
- Any change to the runtime NitroTPM attestation or its `user_data` (PCR4 already binds the
  image; self-description is a build-output concern).
- **Bake-time provenance verification of `imageA`** — the image is trusted as the digest-
  pinned input it is today; consumers verify its Sigstore provenance on GHCR themselves.
- Changing the number of images or AMIs (still one each) or the guest-side build/clone/
  request-encryption flow.

## Decisions

The three load-bearing decisions are fully argued in the proposal; they are summarized here
only enough to anchor the implementation approach. **D1** — bake an OCI image layout into the
verity-sealed root and bind runtime to the image ID (Option A), not `docker save`/`load`
(drops `RepoDigests`) and not the containerd image store (Option B, re-opens hardening). **D2**
— the `linux/amd64` GHCR manifest digest is the single canonical anchor; the baked layout is a
digest-preserving copy of the GHCR artifact. **D-rec** — emit a single-entry verifier record
now so Change 2 need not retrofit a format.

### The integrity story: three layers, distinct jobs

The clarifying realization is that integrity comes from **three** mechanisms, and the design
should not conflate them:

```
        ┌──────────────── PCR4 / dm-verity (AT-REST integrity) ───────────────┐
        │                                                                      │
        │   baked OCI layout bytes        baked env: CONTAINER_IMAGE_DIGEST    │
        │          │                                  │                        │
        └──────────┼──────────────────────────────────┼───────────────────────┘
                   │   offline verify (REQUIRED): sha256(manifest blob) == expected
                   ▼   — ties the two independently-sealed artifacts together,
            OCI manifest    fails closed on a build mismatch, and yields a trusted anchor
                   │
                   ├── commits to ──▶ config digest (= image ID)  ── BIND execution here
                   │                          │
                   └── config.rootfs.diff_ids ┘ (transitively pins layer content)
```

1. **dm-verity / PCR4** is the at-rest integrity boundary: it proves the layout bytes *and*
   the expected-digest env file are exactly what the build sealed.
2. **The offline manifest-digest re-verification is required, not belt-and-suspenders.** It is
   the only step that *compares* the two independently-sealed artifacts (layout vs. expected
   digest) — verity seals each but never relates them, so without this check a build that bakes
   image A while writing env digest B would pass verity. It is also the canonical-anchor join
   (D2) and the step that lets the executor derive a *trusted* image ID from a *verified*
   manifest rather than an assumed-correct one. This keeps the existing secure-by-default
   fail-fast-at-startup contract.
3. **Image-ID binding** is what actually runs. Because the config (image ID) commits to the
   layers via `rootfs.diff_ids`, binding to the image ID transitively pins layer content, so
   the `RepoDigests` loss across import is moot.

### Build-side: getting the layout into the verity-measured root

- **Copy by digest, never rebuild.** The build copies `imageA` from GHCR into the KIWI root
  tree as an OCI layout using a digest-preserving tool (`oras cp` / `skopeo copy oci:`), in the
  builder/CI environment where such tooling is freely available. `imageA` is supplied
  **single-architecture (`linux/amd64`)**, so its published digest *is* the manifest digest;
  the copy resolves and pins that manifest explicitly so a multi-platform index can never leak
  in (D2 constraint 1). Any rebuild or media-type conversion that rewrites the config JSON
  would change the image ID and is forbidden (D2 constraint 2).
- **No bake-time provenance check.** Consistent with how the image is supplied today, the bake
  does not verify `imageA`'s Sigstore provenance — it trusts the digest-pinned reference and
  lets consumers verify on GHCR.
- **Placement under verity.** The layout is written into the KIWI overlay at a fixed path
  inside the erofs root tree so `verity_blocks="all"` measures its bytes into PCR4. No
  `appliance.kiwi` verity-scope change is needed — whole-root sealing already covers any new
  file in the tree; the only requirement is that the layout lands in the root tree before the
  image is finalized and the PCRs are computed (see Open Questions re: the injection hook).

### Runtime-side: offline verify → derive → load + bind

Replace `pull_container_image()` and the `RepoDigests` verification with a three-step sequence
that treats no daemon-reported value as trusted:

1. **Verify** — recompute the manifest digest over the baked layout's on-disk manifest blob
   and compare to the expected `container_image_digest`. Pure offline hashing (plain SHA-256 of
   the manifest bytes); no daemon, no network. Fail closed on mismatch, exactly as startup
   fails closed today.
2. **Derive** — read the config descriptor out of that verified manifest; its digest is the
   trusted **image ID**, derived entirely from verity-measured, digest-verified bytes.
3. **Load + bind** — load the layout into the existing rootless daemon (the legacy graphdriver
   store; `daemon.json` sets no containerd snapshotter), then change `containers.create()`
   (`script_executor.py:284`) to pass the **derived image ID** instead of the
   `repository@sha256:<manifest>` reference string.

The import tool's only obligation is to **not mutate the config blob**. Losing `RepoDigests`,
recompressing layers (config references uncompressed `diff_ids`), and OCI→docker-schema2
media-type conversion (media type lives in the manifest, not the config) all leave the image ID
intact; only rewriting the config JSON breaks it.

### Loader plumbing is a deferred choice (verify is separable from load)

Re-verifying the manifest digest is pure Python and needs no tool; a tool is needed only for
the **load** step. Because the offline re-hash of the **OCI manifest** is required (per the
integrity story above), any path that discards the OCI manifest bytes is rejected — notably the
bare `docker save`/`docker load` round-trip. That leaves three viable loaders, deferred to
tasks:

| loader | re-verify manifest | new runtime binary | needs C1-a faithfulness spike | `RepoDigests` |
|---|---|---|---|---|
| skopeo → `docker-daemon:` | Python re-hash | **skopeo** (compile-from-source, like the rootless helpers) | yes (does it preserve the config blob?) | lost |
| transient `127.0.0.1` registry + `docker pull @digest` | **docker-native** (pull-by-digest re-verifies) | a registry server | **no** (faithful pull by construction) | kept |
| docker-archive + OCI-manifest sidecar + `docker load` | Python re-hash (of the sidecar) | **none** (`docker load` is built-in) | yes (does `oci→archive` preserve the config blob?) | lost |

The transient-registry path is the most attractive — pull-by-digest *is* the required
re-verification, it dodges the faithfulness spike, and it preserves `RepoDigests` as a bonus —
at the cost of shipping a registry binary into the hardened image. The skopeo and archive paths
both ride the same "tool mustn't rewrite the config blob" spike. All three consume the **same**
deterministic baked OCI layout; they differ only in the runtime load step, so the choice does
not affect the build-side artifact or its PCR4 contribution.

### Verifier record (D-rec) and external self-description via build-time surfaces

The build emits a **single-entry verifier record** mapping the baked image's manifest digest →
PCR4 → AMI id → producing commit, **published through the build's publish-time surfaces**: the
GHA job log, the step summary (alongside the existing PCR/attestation reporting), an ORAS
annotation on the published artifact (alongside the existing `pcr4`/`pcr7` annotations), and a
tag on the registered AMI. This is *not* a committed in-repo file in this change — that durable,
committed form is Change 2's `flavors.lock`. The join for a remote verifier is **PCR4**: read
the published `abcd → PCR4 → AMI` record, obtain a live attestation showing that PCR4, and
conclude "this instance runs `abcd`". No runtime attestation / `user_data` change is involved.

## Risks / Trade-offs

- **[The chosen loader rewrites the config blob → image ID diverges from the verified
  manifest's config digest, breaking the binding]** → The C1-a faithfulness spike validates the
  skopeo and archive paths before committing; the transient-`127.0.0.1`-registry path is
  faithful by construction (pull-by-digest) and needs no spike. All three consume the same baked
  layout, so switching is a pure runtime change.
- **[The runtime loader is a new dependency in the minimized, verity-sealed image]** → skopeo
  and a registry server are not in AL2023 core and would join the compile-from-source set (the
  established pattern for rootlesskit/slirp4netns/fuse-overlayfs). The archive+sidecar path
  avoids this entirely (`docker load` is built-in) at the cost of the conversion spike. This is
  an explicit input to the loader choice, not an afterthought.
- **[A multi-arch index digest used as the anchor resolves per-host and yields a different
  image ID off-platform]** → `imageA` is supplied single-arch (`linux/amd64`) and the bake
  resolves/pins that manifest (D2 constraint 1); the verifier record records the single-platform
  manifest digest.
- **[An accidental rebuild / media-type-converting copy in the pipeline rewrites the config
  JSON and changes the image ID]** → Use only digest-preserving copy (`oras cp` / `skopeo copy`
  preserving digests); the offline verify step at startup catches any drift fail-closed because
  the recomputed manifest digest would no longer match the expected `container_image_digest`.
- **[Decompressed image resident in the RAM overlay for the instance lifetime]** → Instance
  memory must budget the decompressed image size on top of the `256m` `/tmp` scratch and the
  workspace. The current startup-pull already incurs this (the pulled image also lands on the
  RAM overlay), so it is not a new cost single-flavor; documented because Change 2 multiplies it
  per flavor (its Q6).
- **[No bake-time provenance check means a wrong-but-digest-pinned image could be baked]** →
  Accepted: the digest pin is the contract today, and the offline verify + PCR4 still guarantee
  the baked bytes match the named digest. Provenance remains verifiable by consumers on GHCR.
- **[Baked layout absent or corrupt in the root tree]** → Startup verification fails closed
  exactly as today's missing/mismatched-digest path does (`config.py:485`, `main.py:215`); a
  corrupt layout also breaks dm-verity, so PCR4 would already diverge and attestation fail.

## Migration Plan

1. Land the build-side digest-preserving copy of the `linux/amd64` layout into the KIWI root
   tree and confirm PCR4 changes deterministically with the baked layout present (no functional
   executor change yet).
2. Run the C1-a loader-faithfulness spike for the candidate path(s); choose the loader
   (transient registry / skopeo / archive+sidecar) based on the result and the runtime-binary
   cost.
3. Implement the runtime verify → derive → load+bind sequence behind the existing startup
   gating so the image-ID binding replaces the `RepoDigests` check; keep fail-closed semantics
   identical.
4. Emit the single-entry verifier record via the build's publish-time surfaces (log, summary,
   ORAS annotation, AMI tag).

**Rollback:** the change is contained to the build copy step, the executor startup path, and the
verifier-record emission. Reverting restores the startup `docker pull` + `RepoDigests` check;
because the image stays externally supplied and digest-pinned throughout, no data or external
contract migration is involved. A previously-built baked AMI cannot fall back to pulling, so
rollback is a roll-forward to a re-pulling AMI build, not an in-place downgrade. Note the
executor code and the AMI baking are coupled and must ship in order: bake first (a new
executor is not yet required and an old pulling executor simply ignores the layout), then
switch the executor to the baked path.

## Open Questions

- **Loader choice** — transient registry (no spike, ships a registry binary), skopeo
  (compile-from-source + spike), or archive+sidecar (no new binary + conversion spike).
  Resolved in tasks after the C1-a spike; the manifest re-verification requirement is fixed
  regardless.
- **Injection point + PCR4 reproducibility** — `appliance.kiwi:89` notes there is *no* KIWI
  hook that runs after UKI assembly but before the root tree is written to disk
  (`pre_disk_sync.sh` runs before dracut), so the concrete hook/path where the OCI layout is
  dropped in (so it is measured) must be chosen in tasks, and a digest-preserving copy must be
  confirmed to keep PCR4 **reproducible** (otherwise verifiers cannot pin a known-good PCR4).
- **Exact publish surfaces** — whether the AMI-side record lands as an AWS tag on the
  registered AMI, an ORAS annotation on the published artifact, or both, and the exact
  serialization — chosen in tasks so Change 2's `flavors.lock` is a clean superset.
