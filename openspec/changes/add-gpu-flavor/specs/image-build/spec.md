## ADDED Requirements

### Requirement: GPU driver install derived from the flavor's effective env

The KIWI image build SHALL determine whether to install the NVIDIA driver and Container Toolkit — and whether to apply the GPU image-size increase — from the `ENABLE_GPU` value in the flavor's effective, PCR4-measured env file, which is the single source of truth shared with the runtime config. It SHALL NOT determine this from a separate `--enable-gpu` invocation flag, and that flag SHALL be removed. The value SHALL be read from the `--env-file` the build script already receives, using the same mechanism by which it already reads `CONTAINER_IMAGE`/`CONTAINER_IMAGE_DIGEST` from that file, after the effective env is copied into the build context and before the size-bump and driver-install steps that consume it. Absence of an `ENABLE_GPU` key SHALL derive to `false`.

#### Scenario: GPU flavor built with the driver present

- **WHEN** a flavor whose effective env sets `ENABLE_GPU=true` is built
- **THEN** the build derives `ENABLE_GPU=true` from the copied env file, installs the NVIDIA driver and Container Toolkit into the image, and applies the GPU image-size increase, so a runtime that reads the same baked `ENABLE_GPU=true` finds the driver present and the `gpu-attestation` fail-closed path is never reached for the correctly-configured flavor

#### Scenario: Non-GPU flavor derives false and builds unchanged

- **WHEN** a flavor whose effective env omits `ENABLE_GPU` or sets it to `false` is built
- **THEN** the build derives `ENABLE_GPU=false`, installs no NVIDIA driver or Container Toolkit, and applies no image-size increase, leaving non-GPU flavor builds behaviorally unchanged

#### Scenario: Single source of truth, no separate flag

- **WHEN** the build script needs the build-time `ENABLE_GPU` decision
- **THEN** it reads the key from the `--env-file` rather than from a `--enable-gpu` CLI flag, no `--enable-gpu` flag exists on the script, and the build-time driver install and the baked runtime config both trace to the same PCR4-measured effective env so they cannot diverge

### Requirement: Read-only-rootfs-capable Container Toolkit for GPU flavors

For GPU-enabled flavor builds, the NVIDIA Container Toolkit installed into the image SHALL be a release ≥ 1.19.0, so CDI driver injection functions under the hardened `CONTAINER_READ_ONLY_ROOTFS=true` execution-container posture (native read-only-rootfs support landed in toolkit 1.19.0) without relaxing that posture. The GPU flavor's attested sandbox posture SHALL remain identical to the hardened default — no `container-security` relaxation is introduced for GPU access. The version bump is gated on `ENABLE_GPU=true` and SHALL NOT affect non-GPU flavor builds.

#### Scenario: Toolkit floor enforced for GPU builds

- **WHEN** a GPU-enabled flavor build installs the NVIDIA Container Toolkit
- **THEN** the installed `NVIDIA_CTK_VERSION` is a release ≥ 1.19.0 that provides native read-only-rootfs support, so CDI injection resolves the driver sonames without writing into the sealed container rootfs

#### Scenario: Hardened posture preserved under GPU

- **WHEN** the GPU flavor runs execution containers
- **THEN** `CONTAINER_READ_ONLY_ROOTFS` remains `true` and the rest of the hardened posture (rootless `65534`, `no-new-privileges`, cap drops) is unchanged, with no `container-security` relaxation required for GPU access

#### Scenario: Toolkit-version regression check

- **WHEN** the test suite runs
- **THEN** it verifies the `NVIDIA_CTK_VERSION` pinned in the GPU driver-install path is ≥ 1.19.0

### Requirement: GPU-flavor image obtains driver tooling only via CDI injection

A flavor execution-container image that relies on runtime CDI driver injection SHALL NOT bundle the NVIDIA driver or user-space GPU tooling — no `nvidia-smi`, no driver `.so` libraries, and no NVIDIA driver / CUDA / `nvidia-utils` packages. `nvidia-smi` and the driver libraries SHALL be supplied at runtime solely by the Container Toolkit's CDI injection from the PCR4-measured host driver, so the tooling the workload sees is byte-identical to the driver the attestation measures. This is a build-time integrity property distinct from the existing "no run-time install" contract (which does not preclude baking driver tooling at build time), and it holds independently of the flavor's other packages.

#### Scenario: GPU-flavor image bakes no driver tooling

- **WHEN** a GPU-enabled flavor's execution-container image is built
- **THEN** the image installs no NVIDIA driver, `nvidia-smi`, driver `.so` libraries, or CUDA/`nvidia-utils` packages, and remains the pinned hardened base plus `USER 65534:65534`, with driver tooling absent from the image and injected only at runtime via CDI

#### Scenario: Baked-driver regression check

- **WHEN** the test suite runs
- **THEN** it verifies no flavor `Dockerfile` installs NVIDIA driver, `nvidia-smi`, CUDA, or `nvidia-utils` packages, so GPU driver tooling can only reach the container through CDI injection
