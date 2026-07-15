## Context

The platform already carries a complete measured-driver GPU attestation stack — the NVML collector
(`src/attestation.py`), the server-controlled GPU env overlay and deny-list (`src/script_executor.py`,
`src/config.py`), CDI passthrough (`runtime=nvidia`), and the build-time NVIDIA driver + Container
Toolkit install (`kiwi-descriptions/config.sh`, gated on `ENABLE_GPU=true`). What is missing is a
**flavor** that turns it on: `flavors/` holds only `default` and `rust-build`, so no built AMI can
emit a populated `gpu` block. This change adds that flavor (`gpu-presence`) and fixes the build
wiring that currently prevents a runtime-GPU flavor from being built with the driver present.

See `proposal.md` for full motivation. This document records the *how* — the technical decisions,
their alternatives, and the runtime preconditions that are not covered by the build-time
measurement.

Key constraints inherited from existing specs:
- **Attestation is host-side.** The `gpu` block is read via NVML *in the server process*, decoupled
  from the container's runtime device grant (`gpu-attestation` spec). The execution container plays
  no role in producing it.
- **Config is attested-at-rest.** A flavor's effective env is merged deterministically, baked into
  the verity-sealed erofs, and measured into PCR4 (`execution-build-images` spec). Anything derived
  from that env at build time inherits the same provenance.
- **The sandbox is hardened by default.** Execution containers run rootless `65534:65534`,
  `cap_drop=ALL` (+ a fixed 7-cap add), `no-new-privileges`, and `read_only` rootfs
  (`src/script_executor.py`). The GPU flavor inherits this and must not relax it.

## Goals / Non-Goals

**Goals:**
- Add a buildable `gpu-presence` flavor (`env` + `Dockerfile`) that emits a populated `gpu`
  attestation block on a real G-series executor.
- Make the flavor's `env` the single source of truth for GPU at build time, so a flavor that enables
  GPU at runtime is always built with the driver present (no driver-less / runtime-on divergence).
- Preserve the hardened, PCR4-attested sandbox posture unchanged — no `container-security` relaxation.
- Keep the flavor honest-by-construction: it attests GPU *presence* and is structurally incapable of
  GPU *compute*.

**Non-Goals:**
- A general-purpose GPU **compute** flavor (CUDA runtime in the container). That would be a separate
  flavor with its own attested posture.
- Hardware (silicon/firmware) GPU attestation (NVIDIA NRAS). The `gpu-attestation` spec reserves
  `gpu.attestation.report_digest` for that future work; it is out of scope here.
- Any change to the NVML collector, GPU env overlay/deny-list, CDI passthrough, or driver-install
  script — all already present and unchanged.
- Changing the demo. Dispatch is keyed on `audience` + `repository_url` + `commit`, never the flavor
  name, so the flavor name and internals are invisible to the consumer.

## Decisions

### D1 — Flavor named `gpu-presence`, not `gpu`

Named for its *purpose* (what it attests — GPU presence), following the `rust-build` convention that
flavors are named for the job, not the tech. The `utility`-only capability grant (D2) makes it
structurally incapable of GPU compute, so a bare `gpu` name would over-claim. It is also a namespace
decision: sibling GPU flavors with divergent attested postures are already foreseen (a
hardware-attestation flavor on P5/P6, a hypothetical compute flavor) and cannot collapse into one
`gpu` flavor.

- **Alternative — `gpu`:** rejected as over-claiming and un-extensible. There is no bare `build`
  flavor either; each is purpose-named.
- **Cost:** none. `detect_changes.py` auto-enrolls any `flavors/<name>/` directory, and the demo
  never keys on the flavor name.

### D2 — Container driver capability `utility`, not `compute,utility`

`utility` injects `nvidia-smi`/NVML — everything the presence workload's device enumeration needs.
`compute` would inject the CUDA runtime (`libcuda.so`, …) the presence flavor is designed never to
use. Dropping it is honest-by-construction: with no CUDA runtime in the container, GPU compute is
impractical, so the "no kernel executed" non-goal is **enforced by the sandbox, not merely asserted**,
and the runtime injection surface stays minimal.

- **Does not weaken attestation (verified against the collector).** The `gpu` block's
  `compute_capability` and `cuda_version` fields are host-side NVML reads (a `utility`-class
  concern), independent of the container's driver-capability grant. Confirmed in
  `src/attestation.py::_collect_nvml_devices()`: `cuda_version` comes from
  `nvmlSystemGetCudaDriverVersion()` (the CUDA **driver** API version — reported by the driver
  alone, no CUDA runtime and no `compute` grant) and `compute_capability` from
  `nvmlDeviceGetCudaComputeCapability()` (a pure NVML read). Neither needs the CUDA runtime that
  `compute` would inject, so dropping `compute` costs the attestation nothing.
- **Mechanism.** `NVIDIA_DRIVER_CAPABILITIES` is a free-form, server-controlled override
  (deny-listed against caller injection) with no allow-list gate, so `utility` simply replaces the
  code default `compute,utility`.
- **Alternative — inherit the `compute,utility` default:** rejected; it injects an unused CUDA
  runtime and lets the "no compute" property rest on assertion rather than construction.

### D3 — Build-time GPU install derives from the flavor `env`; delete the `--enable-gpu` flag

`ENABLE_GPU` has two consumers that today read from different origins:
- **runtime config** — the effective env is copied verbatim into the image and measured into PCR4
  (`build-kiwi-image.sh:87`); `src/config.py` reads `ENABLE_GPU` from it at boot.
- **build-time driver install** — gated on a shell variable `ENABLE_GPU` set *only* by the
  `--enable-gpu` CLI flag (`build-kiwi-image.sh:31-33`), which feeds the image-size bump (`:105`)
  and the `-e ENABLE_GPU` passed into the KIWI builder (`:622`), which gates the driver/toolkit
  install in `config.sh:147`.

Nothing passes `--enable-gpu` — the workflow invokes the script with only `--env-file` (+ `$SSH_FLAG`).
So a `gpu-presence/env` with `ENABLE_GPU=true` yields **runtime-on / driver-never-installed**, and per
the `gpu-attestation` "NVML collection fails closed" requirement that is an attestation error, not a
`gpu` block.

**Decision:** `build-kiwi-image.sh` derives its `ENABLE_GPU` shell variable by `grep`-ing the key out
of the `--env-file` it already receives, and the `--enable-gpu` flag is **deleted**. The derivation
runs after the env file is copied in (`:87`) and before the consumers at `:105` and `:622`.

- **Why (single source of truth):** the flavor `env` becomes the *only* place "GPU on" is decided, so
  the build-time install and the baked runtime config cannot drift. Both consumers then trace to the
  *same PCR4-measured* effective env — honest-by-construction, matching D2's framing.
- **Not a new mechanism:** the same script already reads `CONTAINER_IMAGE`/`CONTAINER_IMAGE_DIGEST`
  out of the effective env by `grep` (`:498-499`). D3 applies that established pattern to one more key.
- **Alternative — workflow derives and passes `--enable-gpu` for GPU flavors:** rejected. It keeps
  the flag alive as a second decision point that must agree with the env by discipline, duplicates
  the "read `ENABLE_GPU`" logic in YAML separate from where `CONTAINER_IMAGE` is already read, and its
  build-time derivation is a separate path rather than a read of the measured artifact.
- **Safe to delete the flag:** it is dead in the real pipeline, so removal changes no live behavior
  and leaves no compatibility surface. Absence of `ENABLE_GPU` in an env (e.g. `default`) derives to
  `false`, the correct default with no explicit key required.

### D4 — The `gpu-presence` Dockerfile has essentially no GPU content

All three things that make the flavor "GPU" live *outside* the image:
1. the `gpu` claims block is a host-side NVML read in the server process (D-context);
2. `nvidia-smi` and the driver `.so`s are runtime CDI injections (the `utility` grant of D2);
3. the only hard image requirement is a working **`bash`**, because the executor runs
   `["bash", "/workspace/<script>"]` (`script_executor.py:328`).

So the image is the pinned hardened base + `USER 65534:65534`, with **no GPU packages** and likely
**no `RUN` layers** (`bash` ships in the base). The `rust-build` extras (`gcc`, rust toolchain,
`oras`, `curl`) are specific to *that* flavor, not baseline, and are omitted.

- **Keep `nvidia-smi` out for honesty, not just size.** A baked `nvidia-smi` could enumerate (or
  fabricate) devices via a binary unrelated to the PCR4-measured host driver. CDI-only injection makes
  the container's `nvidia-smi` *be* the measured driver's, so what the workload sees and what the
  attestation reports share one measured root.
- **The base image family is forced, not chosen.** `["bash", …]` rules out distroless (no shell); CDI
  injecting glibc-built driver `.so`s rules out alpine/musl (the libs won't load). That leaves a glibc
  base *with a shell* — `debian:bookworm-slim`, matching `rust-build` for free consistency and
  driver-glibc compatibility, pinned to its amd64 manifest digest.
- **Alternative — bundle `nvidia-smi`/driver in the image:** rejected on both honesty (forks the
  container's view from the measured driver) and hardening (larger surface, run-time-fetched binaries).

### D5 — NVIDIA Container Toolkit ≥ 1.19.0 to keep read-only rootfs

The sandbox runs `CONTAINER_READ_ONLY_ROOTFS=true`, which the GPU flavor inherits. On toolkit 1.18.2
(currently pinned in `config.sh:248`), CDI injection recreates the driver `.so.1` soname symlinks
(`create-symlinks` hook) and the linker cache (`update-ldcache` hook) by **writing into the container
rootfs** — so a read-only rootfs makes those writes fail and `nvidia-smi` cannot resolve
`libnvidia-ml.so.1`. Toolkit **v1.19.0 adds native read-only-rootfs support**.

**Decision:** bump `NVIDIA_CTK_VERSION` to a ≥ 1.19.0 release. Gated on `ENABLE_GPU=true`, so it only
affects GPU-flavor builds; 1.19.x retains the `nvidia-cdi-refresh` service that 1.18.x was chosen for.

- **Why over the alternative:** the alternative is relaxing `CONTAINER_READ_ONLY_ROOTFS=false` on the
  GPU flavor, which weakens the PCR4-attested sandbox posture. The toolkit bump keeps the flavor's
  attested posture *identical* to the hardened default — no `container-security` relaxation, no
  base-image constraint. It expresses a build-time cost (a new PCR4) to avoid a runtime posture cost.
- **Mechanism (confirmed from upstream release notes / PRs).** 1.19.0's read-only support works by
  *relocating the writes out of the sealed rootfs*, not by making the rootfs writable:
  the soname `.so.1` symlinks are **pre-declared in the CDI spec** (generated host-side, injected as
  spec edits) rather than created by writing into the container; and the `update-ldcache` hook runs
  in an **isolated mount namespace** (pivot_root, with a move-mount fallback where pivot_root is
  unsupported — upstream PR #1174), so the ldcache write never lands in the read-only rootfs. The
  1.19.0 release note is verbatim: *"Add support for read-only root filesystems such as those on an
  initramfs."* Residual uncertainty therefore narrows from "does it work at all" to "does the
  isolated-namespace ldcache step behave under **rootless runc**" (PR #1174 was motivated by kata
  `--no-pivot`; rootless runc normally supports pivot_root) — confirmed by the spike (see Risks).

### D6 — Preserve the full hardened posture; keep `network=none`

The GPU flavor keeps `CONTAINER_READ_ONLY_ROOTFS=true`, nonroot `65534`, `no-new-privileges`, and cap
drops, and sets the most-secure `CONTAINER_NETWORK_MODE=none` since the presence workload needs no
network. D5 (toolkit ≥ 1.19.0) is precisely what makes this possible without relaxation.

- **Alternative — relax one or more of these for GPU convenience:** rejected. The whole value
  proposition is an attested sandbox; a relaxation would have to be attested and justified, and D5
  removes the need.

## Risks / Trade-offs

- **[Toolkit 1.19.0 read-only-rootfs support may not fully eliminate container writes]** → Validate
  with the spike below *before/at apply*. If 1.19.x still writes into the container under read-only
  rootfs, the fallback is an explicit, attested `CONTAINER_READ_ONLY_ROOTFS=false` on the GPU flavor
  only.

- **[The sandbox user's GPU access is an un-attested runtime precondition]** → `USER 65534:65534`
  satisfies the hardened *image* contract, but whether that user can open the GPU is decided
  **outside the image**, by DAC on the host device nodes. The container runs `65534:65534` with
  `cap_drop=ALL`, **no `group_add`**, and the rootless daemon uses `nvidia-container-cli.no-cgroups`
  (`config.sh:266`), so the cgroup device controller is not the gate — the gate is the filesystem
  mode of `/dev/nvidiactl` and `/dev/nvidia0` (CDI bind-mounts them with their host mode).
  As of the D5 toolkit bump there are now **two independent paths** to access, and for this flavor's
  configuration both hold:
  1. **World-access (the pre-existing path).** Nothing in `config.sh` sets a device-node mode, so the
     nodes get NVIDIA's driver default of `0666 root:root` (world-readable), under which `65534`
     opens them and `nvidia-smi -L` succeeds.
  2. **Granted access via injected GID (new in toolkit 1.19.0, default-on).** 1.19.0 generates the
     CDI spec (schema v0.7.0) with **additional GIDs for the device nodes**, so a non-root container
     user receives the node's owning group as a supplemental GID automatically — no `group_add`
     needed. Release note verbatim: *"Added support for running containers as a user that may not
     have explicit access to a device node without requiring that additional groups be explicitly
     specified."* Because we run `nvidia-container-runtime.mode=cdi` (`config.sh:272`), this spec is
     consumed by nvidia-container-runtime (itself 1.19.0), so the v0.7.0 schema support is internal
     to the toolkit — not gated on the AMI's Docker version.

  Crucially, the failure mode that usually bites this feature under rootless **does not apply here**:
  in rootless Docker a container GID must fall within the user's `/etc/subgid` map or runc fails to
  apply it, but our nodes are `root:root`, so the injected GID is `0`, which always maps to the host
  user in rootless — harmless. (A hardware group like `video=44` outside the subgid range is what
  would fail; that is not our case.) Presence-only further narrows exposure: `utility` +
  `nvidia-smi -L` touches only `/dev/nvidiactl` + `/dev/nvidia0`, never `/dev/nvidia-uvm` (compute).

  **The precondition has two consumers, and the load-bearing one is host-side and *unmitigated* by
  the GID feature.** The device nodes are opened by two distinct principals:
    - **Container** `nvidia-smi -L` (uid `65534`) — the *demonstration* the demo runs. Covered by
      *both* the `0666` world-access path and path 2 above (1.19.0 injected GID via CDI).
    - **Host** `nvmlInit()` run by the executor service (`User=gha-executor` in the systemd unit) —
      this is what actually produces the `gpu` block (`_collect_nvml_devices()`), and it is
      **load-bearing**: if it fails, collection raises and attestation fails closed
      (`attestation.py:209-212`). The host process opens the nodes *directly* with its own uid/gid,
      so the CDI injected-GID feature (path 2) **does not apply to it** — it rests solely on `0666`
      world-access (or `gha-executor` being in the nodes' owning group). So the more important
      consumer is *less* mitigated than the container one.

  This is still an **un-attested runtime precondition** — the `0666` mode and the CDI spec are both
  produced at boot on the host (nvidia-modprobe/persistenced; nvidia-cdi-refresh), not in the
  PCR4-measured erofs — so it is confirmed by the spike (run as `--user 65534:65534`, not root).
  Fallback levers are now cheaper and better-scoped: if GID injection misbehaves under rootless, the
  targeted off-switch is the `no-additional-gids-for-device-nodes` CDI feature flag (falling back to
  the world-access path), **not** a posture relaxation. Only if *both* paths fail would a
  deterministic device-mode + explicit `group_add` grant be needed.

- **[The single spike covers two independent preconditions]** → Both preconditions are now
  *documented, default-on* 1.19.0 features (read-only rootfs; non-root device access via injected
  GIDs), so the spike's role is to **confirm the documented integration in our precise stack**
  (rootless + `no-cgroups` + CDI mode + `--user 65534` + AL2023), not to validate a bet. One command
  validates both, run as the sandbox user under read-only rootfs on a real G-series instance:
  ```
  # (a) container path — the workload demonstration
  docker run --runtime=nvidia --read-only --user 65534:65534 \
    -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=utility \
    <base> nvidia-smi -L

  # (b) HOST path — the load-bearing consumer that actually produces the gpu block
  sudo -u gha-executor python3 -c \
    'import pynvml; pynvml.nvmlInit(); print(pynvml.nvmlDeviceGetCount())'
  #   (or, more cheaply:  sudo -u gha-executor nvidia-smi -L)
  ```
  The `--user 65534:65534` and `--read-only` flags on (a) are load-bearing: dropping either would
  hide one precondition. Leg **(b) is essential and easy to omit** — (a) alone validates only the
  container consumer; a passing (a) with a failing (b) leaves the real attestation read broken. (b)
  exercises `gha-executor` opening the nodes on the host, exactly as the executor service does. The AMI *build* itself does not need a GPU (the driver DKMS compile runs in the KIWI
  builder against kernel headers); running the executor does — on a NitroTPM-supported G-series
  instance (G4dn/G5/G6/G6e per the `gpu-attestation` supported-instance bound).

- **[The GPU AMI's PCR4 changes]** → Expected and correct: the toolkit and driver are part of the
  measured root. Verifier policy for the GPU flavor must pin the new PCR4 (recorded in `flavors.lock`).

- **[GPU image size]** → The driver + toolkit add ~1.5 GB; the build already bumps the GPU image from
  4 GB to 8 GB, now driven by the same derived `ENABLE_GPU` (D3).

## Migration Plan

This is additive — no existing flavor or AMI changes behavior.

1. Bump `NVIDIA_CTK_VERSION` to ≥ 1.19.0 in `config.sh` (D5). No effect on non-GPU flavors (gated on
   `ENABLE_GPU=true`).
2. Rewire `build-kiwi-image.sh`: derive `ENABLE_GPU` from `--env-file`, delete the `--enable-gpu` flag
   (D3). Verify non-GPU flavors still derive `false` and build unchanged.
3. Add `flavors/gpu-presence/env` (D1, D2, D6) and `flavors/gpu-presence/Dockerfile` (D4).
4. `detect_changes.py` auto-enrolls the new flavor; the pipeline builds it and writes its
   `{digest, PCR4, AMI id, producing commit}` into `flavors.lock`.
5. **Run the spike** on a G-series instance to confirm both preconditions before relying on the AMI.
6. Point verifier policy at the new GPU-flavor PCR4.

**Rollback:** remove `flavors/gpu-presence/` (the flavor disappears from the matrix); revert the
`config.sh` and `build-kiwi-image.sh` edits. Because the change is additive and gated, rollback does
not affect `default` or `rust-build`. The `--enable-gpu` flag deletion is the only non-additive edit;
reverting it restores the (dead) flag.

## Open Questions

- **What does the demo's presence script invoke beyond `bash` + CDI-injected `nvidia-smi`?** If it
  parses `nvidia-smi --query-gpu … --format=csv` with coreutils, the image needs nothing more; if it
  needs `jq` or similar, that is the single explicit `apt` line the Dockerfile would carry. This is a
  `github-runner-ec2-attestation-gpu-demo` fact not visible from this repo, resolved when the flavor's
  Dockerfile is written against the actual script.
- **Exact ≥ 1.19.0 patch release to pin.** Choose the latest 1.19.x that still ships
  `nvidia-cdi-refresh`, confirmed available in the NVIDIA toolkit RPM repo for AL2023/`$basearch`.
  Note 1.19.1 is a bugfix release that specifically hardens `nvidia-cdi-refresh` (removes the
  `multi-user.target` dependency; WSL2 unit-condition fixes) — worth preferring over 1.19.0 unless a
  regression surfaces.
- **Does the default-on additional-GIDs injection need disabling for our rootless setup?** Expected
  *no* (the injected GID is `0`, always mapped in rootless), but the spike settles it. If it must be
  disabled, the lever is the `no-additional-gids-for-device-nodes` CDI feature flag, applied where
  `config.sh` generates/refreshes the CDI spec — a one-line, in-scope change, not a posture
  relaxation.
