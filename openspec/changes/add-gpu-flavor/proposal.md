## Why

The platform already carries a measured-driver `gpu` claims block (`gpu-attestation` spec):
the NVML collector (`src/attestation.py`), the server-controlled GPU env overlay and deny-list
(`src/script_executor.py`, `src/config.py`), CDI passthrough (`runtime=nvidia`), and the
build-time NVIDIA driver + Container Toolkit install (`kiwi-descriptions/config.sh`) are all
built. But **no flavor enables any of it**: `flavors/` has only `default` and `rust-build`, so
the platform cannot actually emit a populated `gpu` block on a real executor. The sibling
`github-runner-ec2-attestation-gpu-demo` consumer demo (`add-attested-gpu-presence-pipeline`)
depends on exactly this — a GPU-flavor executor to dispatch against — and names it as its one
upstream precondition. This change adds that flavor.

**Flavor name and scope: `gpu-presence`, not `gpu`.** The flavor is named for its *purpose* (what
it attests — GPU *presence*), following the `rust-build` convention (flavors are named for the job,
not the tech). It is deliberately not named `gpu`: the `utility`-only capability grant (below) makes
it structurally incapable of GPU compute, so `gpu` would over-claim. It is also a namespace
decision — the demo's own roadmap already foresees sibling GPU flavors with divergent attested
postures (a hardware-attestation `gpu-hw-attest` on P5/P6, a hypothetical compute flavor), which
cannot collapse into one `gpu` flavor; each must be purpose-named, just as there is no bare `build`
flavor. The flavor name is invisible to the demo — dispatch is keyed on `audience` +
`repository_url` + `commit`, never the flavor name — so this naming is a free upstream choice, and
`detect_changes.py` auto-enrolls any flavor directory. We build only `gpu-presence` now (YAGNI); a
general GPU flavor would be its own change.

Crucially, the demo's proposal states the upstream delta is "only the flavor is missing." That
is an **understatement**: adding `flavors/gpu-presence/env` alone bakes a *broken* AMI. `ENABLE_GPU` has
**two decoupled consumers** — the runtime config (`src/config.py`, fed by the flavor's baked env
file) and the build-time driver install (`kiwi-descriptions/config.sh`, gated on `${ENABLE_GPU}`).
Today the build-time value is driven **only** by the `--enable-gpu` CLI flag to
`build-kiwi-image.sh`, which the build workflow (`build-attestable-image.yml`) passes nowhere. So
a `flavors/gpu-presence/env` with `ENABLE_GPU=true` would produce an image whose runtime tries NVML
collection but whose driver was never installed — and per the `gpu-attestation` "NVML collection
fails closed" requirement, that is an attestation error, not a `gpu` block. The build wiring must
be fixed too.

## What Changes

- Add **`flavors/gpu-presence/env`**: `ENABLE_GPU=true`, `GPU_DEVICES=all`,
  `NVIDIA_DRIVER_CAPABILITIES=utility`, plus per-flavor authorization (`ALLOWED_REPOSITORIES`
  including the gpu-demo repo, `EXPECTED_AUDIENCE`). All GPU keys are already in
  `RECOGNIZED_ENV_KEYS`, so `validate_env.py` accepts them; `NVIDIA_DRIVER_CAPABILITIES` is a
  free-form, server-controlled override (deny-listed against caller injection) with no allow-list
  gate, so `utility` simply replaces the code default `compute,utility`.
  **Why `utility`, not `compute,utility`:** `utility` injects `nvidia-smi`/NVML — everything the
  presence workload's device enumeration needs. `compute` would inject the CUDA runtime
  (`libcuda.so`, ...) the presence flavor is designed never to use. Dropping it is honest-by-
  construction: with no CUDA runtime in the container, GPU compute is impractical, so the demo's
  **T3 non-goal ("a kernel executed") is enforced by the sandbox, not merely asserted**, and the
  injection surface stays minimal. This does not weaken the attestation: the `gpu` block's
  `compute_capability` and `cuda_version` fields are host-side NVML reads (a `utility`-class
  concern), independent of the container's driver-capability grant. (A general-purpose compute GPU
  flavor, if ever needed, would be a separate flavor with its own attested posture.)
- Add **`flavors/gpu-presence/Dockerfile`**: the hardened execution-container image for the flavor. Every
  buildable flavor needs its own `Dockerfile` (the image job builds `flavors/<flavor>`; `default`
  has none precisely because it is not a buildable flavor). Must satisfy the existing hardened
  contract (rootless `65534`, world-exec tools, pinned, no run-time install). Driver libraries and
  `nvidia-smi` are injected at runtime by the Container Toolkit via CDI, so the image itself need
  not bundle the driver.
- **Make the flavor's `env` the single source of truth for GPU at build time.** The KIWI image
  build SHALL install the NVIDIA driver + Container Toolkit (and apply the GPU image-size bump)
  when the flavor being built has `ENABLE_GPU=true` in its effective env — derived from that env,
  not from a separate, manually-passed `--enable-gpu` CLI flag. This closes the gap that would
  otherwise let runtime `ENABLE_GPU=true` diverge from a driver-less image.
- **Bump the NVIDIA Container Toolkit to ≥ 1.19.0** in `kiwi-descriptions/config.sh` (currently
  pinned `1.18.2-1`). The platform runs execution containers with `CONTAINER_READ_ONLY_ROOTFS=true`
  by default, which the GPU flavor inherits. On toolkit 1.18.2, CDI injection recreates the driver
  `.so.1` soname symlinks (`create-symlinks` hook) and the linker cache (`update-ldcache` hook) by
  **writing into the container rootfs** — the soname is not mounted, only the versioned `.so.<ver>`
  file is — so a read-only rootfs makes those writes fail and GPU access breaks (`nvidia-smi`
  cannot resolve `libnvidia-ml.so.1`). Toolkit **v1.19.0 adds native read-only-rootfs support**, so
  the GPU flavor keeps the hardened read-only-rootfs posture with **no attested-posture relaxation**
  and **no base-image constraint**. This is the deliberate alternative to relaxing
  `CONTAINER_READ_ONLY_ROOTFS` on the flavor (which would weaken the PCR4-attested sandbox posture).
  The toolkit install is already gated on `ENABLE_GPU=true`, so the bump only affects GPU-flavor
  builds; 1.19.x retains the `nvidia-cdi-refresh` service that 1.18.x was chosen for.
- The new flavor auto-enrolls in the build matrix (`detect_changes.py` enumerates `flavors/` minus
  `default`); no matrix change is required.

## Capabilities

### New Capabilities
<!-- None. The GPU flavor is an instance of existing capabilities (gpu-attestation,
     remote-executor authorization, container-security), not a new behavioral contract.
     Its build-time wiring is a modification to image-build (below). -->

### Modified Capabilities
- `image-build`: Two build-time GPU changes. (1) The KIWI image build's NVIDIA driver + Container
  Toolkit install (and the GPU image-size increase) SHALL be driven by the flavor's effective
  `ENABLE_GPU` env value — a single source of truth shared with the runtime config — rather than by
  a separate `--enable-gpu` invocation flag that the build pipeline never sets, so a flavor that
  enables GPU at runtime is built with the driver present and the `gpu-attestation` fail-closed path
  is never reached for a correctly-configured flavor. (2) The NVIDIA Container Toolkit version
  installed for GPU builds SHALL be ≥ 1.19.0, so CDI driver injection works under the hardened
  `CONTAINER_READ_ONLY_ROOTFS=true` posture (read-only-rootfs support landed in toolkit 1.19.0);
  this keeps the GPU flavor's attested sandbox posture identical to the hardened default rather than
  requiring a `container-security` relaxation.

## Impact

- **New files**: `flavors/gpu-presence/env`, `flavors/gpu-presence/Dockerfile`.
- **Modified**: the KIWI build wiring so build-time GPU install derives from the flavor env —
  either `build-attestable-image.sh`/`build-kiwi-image.sh` reading `ENABLE_GPU` from the
  `--env-file` (preferred: single source of truth), or `build-attestable-image.yml` deriving and
  passing `--enable-gpu` for GPU flavors. The `--enable-gpu` flag path
  (`build-kiwi-image.sh:23-32,:105,:622`) is either retired or reduced to a derived value.
- **Modified**: `kiwi-descriptions/config.sh` — bump `NVIDIA_CTK_VERSION` from `1.18.2-1` to a
  ≥ 1.19.0 release (read-only-rootfs support). Gated on `ENABLE_GPU=true`, so no effect on non-GPU
  flavors. Changes the GPU AMI's PCR4 (expected — the toolkit is part of the measured root).
- **Runtime precondition (out of scope here, stated as an assumption)**: the executor must run on
  a NitroTPM-supported G-series instance (G4dn/G5/G6/G6e per the `gpu-attestation` supported-
  instance bound). The AMI *build* itself does not need a GPU (the driver DKMS compile runs in the
  KIWI builder container against kernel headers); running the executor does.
- **Deliberately NOT affected**: the `container-security` posture. The GPU flavor keeps the
  hardened defaults (`CONTAINER_READ_ONLY_ROOTFS=true`, nonroot `65534`, `no-new-privileges`, cap
  drops), and can keep the most-secure `CONTAINER_NETWORK_MODE=none` since the presence workload
  needs no network. The toolkit ≥ 1.19.0 bump is what makes CDI injection work under read-only
  rootfs, so no relaxation of the attested sandbox posture is required.
- **Not affected**: the NVML collector, GPU env overlay/deny-list, CDI passthrough, and driver-
  install script — all already present. The `gpu-attestation` spec's requirements are unchanged;
  this change makes the platform able to *satisfy* them with a real flavor.
- **Assumption to validate before/at apply (spike)**: toolkit 1.19.0's read-only-rootfs support is
  documented but its mechanism is not; a `docker run --runtime=nvidia --read-only -e
  NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=utility <base> nvidia-smi -L` on a real
  G-series instance confirms end-to-end. If 1.19.x still writes into the container under read-only
  rootfs, the fallback is an explicit, attested `CONTAINER_READ_ONLY_ROOTFS=false` on the GPU
  flavor only.
- **Enables**: the `github-runner-ec2-attestation-gpu-demo` `add-attested-gpu-presence-pipeline`
  consumer demo to run green against a real GPU-flavor executor.
