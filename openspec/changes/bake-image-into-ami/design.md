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

This change moves the image from runtime-pulled to **attested-at-rest**: bake a docker-archive
+ OCI-manifest sidecar into the verity-sealed root (so PCR4 measures the image bytes), verify it
**offline** at startup, and bind execution to the **image ID** rather than `RepoDigests`. It is the
prerequisite mechanism for the multi-flavor `execution-build-images` change (Change 2);
landing it single-flavor first de-risks the offline-import claim before the multi-flavor
scaffolding is built on top.

**The guarantee this change delivers** (clarified during design): a third party who pulls
`ghcr.io/<owner>/imageA@sha256:<abcd>` and the Remote Executor that runs the baked-in-AMI
image are running the **exact same image**, where `sha256:<abcd>` is the shared, content-
addressed identity. The executor proves this to itself **offline** by re-hashing the baked
OCI-manifest sidecar against the expected digest; a third party can read which image an AMI
carries from the build's publish-time outputs (log, summary, ORAS annotation, AMI tag).

The proposal (`proposal.md`) holds the motivation and the fully-resolved decisions D1, D2,
and D-rec; this document covers HOW those decisions are realized across the build pipeline and
the runtime executor, plus the risks, migration, and remaining unknowns.

## Goals / Non-Goals

**Goals:**

- Bake the externally-supplied, digest-pinned image into the AMI as a **docker-archive + OCI-
  manifest sidecar** (derived **by digest** from an OCI-layout intermediate) into the KIWI root
  tree so both are covered by `verity_blocks="all"` and measured into PCR4.
- Make `sha256:<abcd>` (the `linux/amd64` manifest digest) the **single shared identity** —
  byte-identical between the GHCR artifact and the **baked sidecar** — so "GHCR-pulled == baked-
  in-AMI" holds by construction.
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
- **PCR4 rebuild reproducibility** — making PCR4 bit-for-bit reproducible across rebuilds is a
  non-goal. The trust anchor is attestation tracing PCR4 → the producing GHA run → the commit
  (verifiers read the published record and trust the attested build), not independent rebuild of
  a known-good PCR4. The only PCR4 property relied on is intrinsic: the bytes measured at build
  time are the bytes booted at runtime. This applies to every baked artifact (the OCI layout,
  the docker-archive, and the manifest sidecar), so no loader, copy tool, or converter needs to
  produce deterministic output.

## Decisions

The three load-bearing decisions are fully argued in the proposal; they are summarized here
only enough to anchor the implementation approach. **D1** — bake a docker-archive + OCI-manifest
sidecar into the verity-sealed root and bind runtime to the image ID (Option A), not `docker save`/`load`
(drops `RepoDigests`) and not the containerd image store (Option B, re-opens hardening). **D2**
— the `linux/amd64` GHCR manifest digest is the single canonical anchor; the baked sidecar is
byte-identical to the GHCR manifest. **D-rec** — emit a single-entry verifier record
now so Change 2 need not retrofit a format.

### The integrity story: three layers, distinct jobs

The clarifying realization is that integrity comes from **three** mechanisms, and the design
should not conflate them:

```
        ┌──────────────── PCR4 / dm-verity (AT-REST integrity) ───────────────┐
        │                                                                      │
        │  baked archive + sidecar        baked env: CONTAINER_IMAGE_DIGEST    │
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

1. **dm-verity / PCR4** is the at-rest integrity boundary: it proves the baked bytes (archive +
   sidecar) *and* the expected-digest env file are exactly what the build sealed.
2. **The offline manifest-digest re-verification is required, not belt-and-suspenders.** It is
   the only step that *compares* the two independently-sealed artifacts (sidecar vs. expected
   digest) — verity seals each but never relates them, so without this check a build that bakes
   image A while writing env digest B would pass verity. It is also the canonical-anchor join
   (D2) and the step that lets the executor derive a *trusted* image ID from a *verified*
   manifest rather than an assumed-correct one. This keeps the existing secure-by-default
   fail-fast-at-startup contract.
3. **Image-ID binding** is what actually runs. Because the config (image ID) commits to the
   layers via `rootfs.diff_ids`, binding to the image ID transitively pins layer content, so
   the `RepoDigests` loss across import is moot.

### Build-side: getting the image into the verity-measured root

- **Copy by digest into an OCI layout (build-time *intermediate*).** The build first copies
  `imageA` from GHCR into an **OCI layout** with a digest-preserving tool (`oras cp` /
  `skopeo copy oci:`), in the builder/CI environment where such tooling is freely available.
  `imageA` is supplied **single-architecture (`linux/amd64`)**, so its published digest *is*
  the manifest digest; the copy resolves and pins that manifest explicitly so a multi-platform
  index can never leak in (D2 constraint 1). This layout is a **build-time intermediate** — it
  is *not* what gets baked.
- **Bake a docker-archive + OCI-manifest sidecar (the two artifacts under verity).** From that
  intermediate the build emits, into the KIWI root tree at a fixed path, **two** files:
  (a) a **docker-archive** (`docker save`-format tar, via the `oci→docker-archive` conversion)
  that `docker load` consumes at boot, and (b) the **OCI manifest blob** copied out byte-for-
  byte as a **sidecar**. The sidecar carries the canonical anchor — its bytes are identical to
  the GHCR `linux/amd64` manifest, so `sha256(sidecar)` equals the published manifest digest.
  The archive's only obligation is to preserve the **config blob** (and thus the image ID); it
  need not — and does not — preserve the manifest digest, which is exactly why the sidecar
  exists. Any rebuild or conversion that rewrites the **config JSON** is forbidden (D2
  constraint 2); a conversion that merely re-serializes the manifest envelope to docker schema2
  is fine, because the manifest digest is no longer load-bearing on the archive side.
- **No bake-time provenance check.** Consistent with how the image is supplied today, the bake
  does not verify `imageA`'s Sigstore provenance — it trusts the digest-pinned reference and
  lets consumers verify on GHCR.
- **Placement under verity.** Both baked files (archive + sidecar) are written at a fixed path
  inside the erofs root tree so `verity_blocks="all"` measures their bytes into PCR4. No
  `appliance.kiwi` verity-scope change is needed — whole-root sealing already covers any new
  file in the tree; the only requirement is that they land in the root tree before the image is
  finalized and the PCRs are computed (see Open Questions re: the injection point).

### Runtime-side: offline verify → derive → load + bind

Replace `pull_container_image()` and the `RepoDigests` verification with a three-step sequence
that treats no daemon-reported value as trusted:

1. **Verify** — recompute the manifest digest over the **baked OCI-manifest sidecar** and
   compare to the expected `container_image_digest`. Pure offline hashing (plain SHA-256 of the
   sidecar bytes); no daemon, no network. Fail closed on mismatch, exactly as startup fails
   closed today. Because the build already resolved and pinned the `linux/amd64` child manifest
   and emitted *exactly that* manifest blob as the sidecar, the runtime does **not** walk an
   `index.json` or select a child — there is a single manifest blob to hash. The one subtlety:
   hash the **stored sidecar bytes exactly**, never a re-canonicalized/re-serialized JSON form —
   the OCI digest is over the on-disk bytes, so any reformat would diverge from the expected
   digest. Note this also makes the binding self-enforcing: if the archive's config blob was
   rewritten, the loaded image's ID will not equal the derived image ID, so `containers.create()`
   fails closed with no matching image — the faithfulness spike de-risks "does it work," but
   production is protected regardless.
2. **Derive** — read the config descriptor out of that verified manifest; its digest is the
   trusted **image ID**, derived entirely from verity-measured, digest-verified bytes.
3. **Load + bind** — `docker load` the baked **docker-archive** into the existing rootless
   daemon (the legacy graphdriver store; `daemon.json` sets no containerd snapshotter), then
   change `containers.create()` (`script_executor.py:284`) to pass the **derived image ID**
   instead of the `repository@sha256:<manifest>` reference string. The loaded archive may be
   **tagless** (`<none>:<none>`); binding by image ID is unaffected, but any executor code that
   logs or derives the image *name* (rather than its ID) must be audited — it no longer has a
   repo tag.

The import tool's only obligation is to **not mutate the config blob**. Losing `RepoDigests`,
recompressing layers (config references uncompressed `diff_ids`), and OCI→docker-schema2
media-type conversion (media type lives in the manifest, not the config) all leave the image ID
intact; only rewriting the config JSON breaks it.

### Loader plumbing: the transient-registry path is disqualified by the rootless netns; the choice is build-vs-runtime conversion (leaning build-time)

Re-verifying the manifest digest is pure Python and needs no tool; a tool is needed only for
the **load** step. Because the offline re-hash of the **OCI manifest** is required (per the
integrity story above), any path that discards the OCI manifest bytes is rejected — notably the
bare `docker save`/`docker load` round-trip. Three loaders were considered; the candidates and
their fate:

| loader | new runtime binary | network / netns | faithfulness | status |
|---|---|---|---|---|
| transient `127.0.0.1` registry + `docker pull @digest` | a registry server | **blocked** (see below) | faithful by construction | **rejected** |
| skopeo → `docker-daemon:` (convert at **runtime**) | **skopeo** (large; compile-from-source) | socket only (works) | spike: skopeo preserves config blob | viable (fallback) |
| docker-archive @ **build-time** + OCI-manifest sidecar + `docker load` | **none** (`docker load` / docker-py `images.load()` already present) | socket only (works) | spike: `oci→archive` preserves config blob + tar determinism | **leading** |

**The transient-registry path is rejected — it fights the rootless network namespace.** The
daemon runs under `dockerd-rootless.sh` → rootlesskit + slirp4netns (`config.sh`), i.e. in a
**separate network namespace** with its **own loopback**, and stock `dockerd-rootless.sh` passes
`--disable-host-loopback`. So a registry the executor starts on the host's `127.0.0.1` is
unreachable from the daemon two ways over: different netns `lo`, and host-loopback walled off.
Every workable variant (run the registry inside the daemon's netns via `nsenter` — blocked
because the netns is owned by a child user namespace the uid-1000 executor can't enter; enable
host-loopback; or bind the registry to a routable IP + add `insecure-registries`) either erodes
the hardening posture or requires the `daemon.json`/forked-script changes the path was supposed
to avoid, and all need a spike. Its claimed "no spike, no `daemon.json` change, keeps
`RepoDigests`" advantages do not survive contact with this image. (`RepoDigests` is moot anyway:
execution binds to the image ID, not the manifest digest — the manifest digest is verified
off-daemon.)

**The two surviving loaders both reach the daemon over the UNIX socket**
(`/run/user/1000/docker.sock`), which is filesystem-based and netns-agnostic — the same channel
the executor already uses — so neither touches the network. The real axis between them is **when
the `oci→docker-schema2` conversion runs**, because skopeo (or an equivalent) can be the
converter for either:

- **Runtime conversion (skopeo in the image).** Bake the OCI layout only; at boot run
  `skopeo copy oci:<layout> docker-daemon:` → load → bind image ID. One content-addressed baked
  artifact, but a large new binary in the attestable runtime image, and the conversion is trusted
  live on every boot.
- **Build-time conversion (leading).** Run the conversion in CI (where skopeo/CPU/RAM are free),
  bake the resulting **docker-archive** plus the **OCI manifest blob as a sidecar**, and at boot
  do only built-in `docker load` (`images.load()`) + the offline sidecar re-hash (the Verify step
  the change already requires) → bind image ID. **No new runtime binary**, and faithfulness is
  proven once in CI rather than trusted per-boot.

**Decision: build-time conversion (the docker-archive path), contingent on an archive-determinism
check.** It best serves the project's two deepest commitments: keep the *attestable runtime
image* minimal (the package allow-list ethos — add nothing large you must attest), and prove
conversion faithfulness once in CI rather than carry a permanent runtime dependency. Both socket
loaders are **fail-closed at bind time** (an unfaithful conversion yields an image whose ID ≠ the
derived image ID, so `containers.create()` finds no match and startup fails — an availability
failure caught in CI, never a security hole), so the faithfulness spike is about *does it work*,
not *is it safe*.

Build-time conversion adds a **second verity-measured artifact** (the docker-archive baked
alongside the OCI-manifest sidecar). Its byte-determinism across rebuilds is **explicitly not a
concern**: this project's trust anchor is the attestation tracing PCR4 → the producing GHA run →
the commit (the verifier reads the published record and trusts the attested build), **not**
bit-for-bit rebuild reproducibility. The only PCR4 property that matters is automatic — the bytes
measured at build time *are* the bytes booted at runtime, because it is the same baked artifact —
so a non-deterministic converter is harmless. The lone real cost is the extra baked artifact's
space, negligible single-flavor: `imageA` today is `ubuntu:24.04` (~80 MB unpacked) against a 2 GB
erofs (`overlayroot_readonly_partsize=2048`); Change 2 multiplies it per flavor (its Q6).

**Fallback:** the only thing that could unseat build-time conversion is the C1-a faithfulness
spike failing — i.e. `oci→docker-archive` + `docker load` cannot be made to yield image ID ==
the derived config digest. In that case fall back to **runtime skopeo conversion**: bake the
intermediate **OCI layout** itself (whose manifest blob serves as the sidecar) instead of the
docker-archive, and at boot run `skopeo copy oci:<layout> docker-daemon:`. This makes the loader
a **build-side** decision, not a per-boot runtime toggle — the leading path bakes a docker-archive
+ sidecar, the fallback bakes an OCI layout + sidecar, both derived from the *same* digest-pinned
intermediate, and the choice is fixed once by the spike outcome. The sidecar, the offline Verify
step, and the canonical anchor are identical either way; only the layer-carrying artifact and the
runtime load tool differ. (The fallback must convert from the faithful OCI layout, not from the
suspect archive — converting the already-broken archive would defeat the fallback's purpose.)

### Verifier record (D-rec) and external self-description via build-time surfaces

The build emits a **single-entry verifier record** mapping the baked image's manifest digest →
PCR4 → AMI id → producing commit. The surfaces are **not uniform across fields**, because of a
pipeline-timing constraint: the KIWI artifact is pushed (with its `pcr4`/`pcr7` ORAS annotations)
and Sigstore-attested in `build-and-publish`, *before* `build-ami` runs and the AMI id exists.
An ORAS annotation lives in the artifact manifest, so amending one after the fact changes the
artifact digest and breaks the attestation `build-ami` verifies against — and the AMI id is not
known at publish time anyway. So the record is split:

- the **container image manifest digest** is carried as an ORAS annotation on the published
  artifact (alongside the existing `pcr4`/`pcr7` annotations), fixed at publish time;
- the **full record including the AMI id** is emitted only through surfaces available *after* the
  AMI exists — the GHA job log, the step summary (alongside the existing PCR/attestation
  reporting), and a **tag on the registered AMI**.

This is *not* a committed in-repo file in this change — that durable, committed form is Change 2's
`flavors.lock`, which inherits the same split (the AMI-id binding is AMI-side, not annotated onto
the immutable attested artifact). The join for a remote verifier is **PCR4**: read the published
`abcd → PCR4 → AMI` record, obtain a live attestation showing that PCR4, and conclude "this
instance runs `abcd`". No runtime attestation / `user_data` change is involved.

## Risks / Trade-offs

- **[The chosen loader rewrites the config blob → image ID diverges from the verified
  manifest's config digest, breaking the binding]** → The C1-a faithfulness spike validates the
  skopeo and archive paths before committing; the transient-`127.0.0.1`-registry path is
  faithful by construction (pull-by-digest) and needs no spike. The two surviving paths bake
  *different* layer-carrying artifacts (docker-archive for the leading `docker load` path, OCI
  layout for the skopeo fallback) from the same digest-pinned intermediate, so switching is a
  **build-side** change fixed once by the spike outcome, not a per-boot runtime toggle; the
  sidecar and the offline Verify step are identical in both.
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
- **[Baked archive or sidecar absent or corrupt in the root tree]** → Startup verification fails closed
  exactly as today's missing/mismatched-digest path does (`config.py:485`, `main.py:215`); a
  corrupt layout also breaks dm-verity, so PCR4 would already diverge and attestation fail.

## Migration Plan

1. Land the build-side digest-preserving copy of the `linux/amd64` image into the KIWI root
   tree as the baked **docker-archive + OCI-manifest sidecar**, and confirm PCR4 reflects the
   baked files (i.e. PCR4 covers the new files; rebuild reproducibility is not required) — no
   functional executor change yet.
2. Run the C1-a loader-faithfulness spike for the chosen build-time `oci→docker-archive` path
   (transient registry already rejected); fall back to runtime skopeo only if the archive
   conversion cannot be made faithful (image ID == derived config digest).
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

- **Loader choice** — *largely resolved* (see "Loader plumbing" above): the transient-registry
  path is **rejected** (rootless netns + `--disable-host-loopback` make a loopback registry
  unreachable), and the decision is **build-time docker-archive conversion** (no new runtime
  binary, faithfulness proven once in CI), with **runtime skopeo** as the fallback. The two
  remaining tasks-level confirmation is the **C1-a faithfulness spike** (does
  `oci→docker-archive` + `docker load` preserve the config blob so image ID == derived) —
  protected fail-closed at bind regardless; take the skopeo fallback only if it fails.
  Archive byte-determinism is **not** a gate (the project does not rely on rebuild
  reproducibility — PCR4 is anchored to the attested GHA run → commit). The manifest
  re-verification requirement is fixed in all cases.
- **Injection point** — `appliance.kiwi:89` notes there is *no* KIWI hook that runs after UKI
  assembly but before the root tree is written to disk (`pre_disk_sync.sh` runs before dracut),
  so the concrete hook/path where the baked artifacts (OCI manifest sidecar + docker-archive)
  are dropped in — so they land in the root tree and are measured into PCR4 — must be chosen in
  tasks. The leading candidate is to place them in the builder/CI step before KIWI's create
  phase (where `oras`/`skopeo` are freely available), since they are root-tree *files*, not boot
  artifacts, and so do not need the missing post-UKI hook. PCR4 **reproducibility across
  rebuilds is explicitly a non-goal** (the trust anchor is attestation tracing PCR4 → producing
  GHA run → commit, not bit-for-bit rebuilds); the only requirement is that the artifacts are in
  the tree before the PCRs are computed, which is automatic once placed pre-create.
- **Exact publish surfaces** — whether the AMI-side record lands as an AWS tag on the
  registered AMI, an ORAS annotation on the published artifact, or both, and the exact
  serialization — chosen in tasks so Change 2's `flavors.lock` is a clean superset.
