# image-build Specification

## Purpose

Build the attestable KIWI image that the Remote Executor runs on, reproducibly and auditably, and publish it with the PCR measurements and provenance attestation that downstream consumers verify. This capability covers the reproducible containerized KIWI build, separated Python dependency configurations, artifact publishing to GHCR with PCR annotations, GitHub build-provenance attestation, the rootless-Docker runtime provisioned inside the image (including dependencies compiled from source on AL2023), git provisioning, runtime package minimization, Docker daemon and systemd hardening, host-login hardening, and the optional SSH debug build with its production gate.

Conversion of the KIWI image into an AWS AMI is specified in `ami-build`.

## Requirements

### Requirement: Reproducible KIWI image build

The Build_Workflow SHALL build the KIWI image inside a Docker container (KIWI_Builder) using a Dockerfile with pinned dependency versions, producing a raw disk image and a `pcr_measurements.json` containing PCR4 and PCR7. The build SHALL fail with a descriptive error if the KIWI build fails.

#### Scenario: Containerized build produces image and PCRs

- **WHEN** the Build_Workflow runs
- **THEN** it checks out the repository with submodules, configures host loop devices, runs the KIWI NG build to produce a `.raw` image in a dedicated build-output directory, and generates `pcr_measurements.json` with PCR4 and PCR7

#### Scenario: Reproducibility pinning enforced

- **WHEN** the build inputs are defined
- **THEN** the runner is pinned to a specific Ubuntu version (not `ubuntu-latest`), third-party GitHub Actions are pinned to full 40-character commit SHAs (each commented with its version/date), the KIWI builder `FROM` is pinned to an immutable `@sha256:` digest, the AL2023 package repository URL in `appliance.kiwi` is pinned to a specific release, and the Build_Instance AMI data source uses a specific AMI rather than `most_recent = true`

#### Scenario: Pinning lint check

- **WHEN** the test suite runs
- **THEN** a lint check verifies no `uses:` directive references a mutable tag (each must contain `@` followed by 40 hex characters) and that the Dockerfile `FROM` contains `@sha256:`

### Requirement: Separated Python dependency configurations

The Build_Workflow SHALL maintain script dependencies in `scripts/pyproject.toml` (including boto3 and paramiko) and remote-executor service dependencies in `pyproject.toml` (including fastapi, uvicorn, requests, docker, wolfcrypt-py, and the test tools), managed independently with uv. The KIWI image SHALL contain only the remote-executor dependencies, installed via a lockfile-enforced, integrity-checked path.

#### Scenario: Only service dependencies in the image

- **WHEN** the KIWI_Builder installs Python dependencies
- **THEN** it installs only the remote-executor dependencies from `pyproject.toml` (not the script dependencies), copying `pyproject.toml` and `uv.lock` into the build context

#### Scenario: Lockfile-enforced, hash-checked install

- **WHEN** Python dependencies are installed into the image
- **THEN** the install uses a lockfile-enforced path (`uv sync --frozen`, or a hash-checked export from `uv.lock` installed with `--require-hashes`/`--no-index --find-links`) rather than version ranges from `pyproject.toml`, before the image is finalized

#### Scenario: Source-only dependency built and hash-pinned

- **WHEN** a dependency such as wolfcrypt publishes only a source distribution
- **THEN** the source tarball is downloaded with hash verification against `uv.lock`, the wheel is built inside the KIWI builder container, and its computed SHA-256 hash is included in the final `--require-hashes` requirements file

### Requirement: Artifact publishing with PCR annotations

When the build completes, the Artifact_Publisher SHALL push the raw disk image and PCR measurements file to GHCR using ORAS, authenticated with the GitHub token, annotating the artifact with the `pcr4` and `pcr7` values and outputting the artifact digest.

#### Scenario: Artifact pushed with annotations

- **WHEN** the KIWI build completes
- **THEN** PCR4 and PCR7 are extracted from `pcr_measurements.json`, the artifact is tagged from branch name and timestamp, pushed via ORAS with `pcr4`/`pcr7` annotations, and the artifact digest is calculated and output

#### Scenario: Publishing failure modes

- **WHEN** PCR measurements are missing/invalid, or the ORAS push fails, or the downloaded ORAS binary's SHA-256 checksum does not match the expected value
- **THEN** the Artifact_Publisher fails with the corresponding error (and the ORAS version used matches the AMI_Converter's version)

### Requirement: Build provenance attestation

When artifacts are pushed to GHCR, the GitHub Attestation_Service SHALL generate a Sigstore-signed build-provenance attestation including the artifact digest and repository identity, push it to the registry, and the workflow SHALL output the attestation ID/URL with verification instructions.

#### Scenario: Provenance attested and reported

- **WHEN** the artifact is pushed
- **THEN** a build-provenance attestation is generated, signed via Sigstore, includes the artifact digest and repository identity, is pushed to the registry, and the workflow summary reports the attestation ID, URL, and verification instructions

### Requirement: Docker daemon provisioned in rootless mode

The KIWI image SHALL run Docker in rootless mode under a dedicated non-root service user (`gha-executor`, UID pinned to 1000) so the Remote Executor can manage Execution_Containers without a rootful Docker socket. The image SHALL include the `docker` and `git` packages and the runtime libraries required by the compiled rootless helpers.

#### Scenario: Rootless daemon runs under service user

- **WHEN** the KIWI image boots
- **THEN** a rootless Docker daemon runs under `gha-executor` (data-root `/var/lib/gha-executor/docker`) accessible via `/run/user/1000/docker.sock`, with `/etc/subuid` and `/etc/subgid` configured with two non-overlapping 65,536-ID ranges, `loginctl enable-linger` set, and a udev rule giving `gha-executor` 0600 ownership of the NitroTPM device nodes

#### Scenario: git available for cloning

- **WHEN** the image boots
- **THEN** the `git` binary is on the system PATH for the Repository_Client; if `git` or `docker` were absent the image could not clone or run Execution_Containers

#### Scenario: Library discovery refreshed

- **WHEN** the image is prepared
- **THEN** `/etc/ld.so.conf.d/usr-local-lib64.conf` adds `/usr/local/lib64` to the linker path, `config.sh` runs `ldconfig`, and `config.sh` verifies `dockerd-rootless.sh` and the rootless helper binaries are present and executable at `/usr/local/bin/`

### Requirement: Rootless Docker dependencies built from source

Because rootlesskit, slirp4netns, fuse-overlayfs, and libslirp are not in the AL2023 core repository, the build SHALL compile them from source inside the KIWI builder container at pinned, integrity-verified versions and install them into the image overlay, and download `dockerd-rootless.sh` from Moby at a pinned, checksum-verified version. `appliance.kiwi` SHALL NOT list these as packages.

#### Scenario: Helpers compiled at pinned versions

- **WHEN** the KIWI image is built
- **THEN** rootlesskit (v1.1.1, not v2.x), slirp4netns, fuse-overlayfs, and libslirp are compiled inside the builder container at pinned commits/tags, the binaries are placed in the image overlay `/usr/local/bin/` and `libslirp.so` in `/usr/local/lib64/`, and `dockerd-rootless.sh` (Moby v20.10.27) is installed to `/usr/local/bin/`

#### Scenario: Source integrity verified

- **WHEN** sources are fetched before compilation
- **THEN** git-cloned helpers are pinned to immutable commit SHAs and verified by GPG-signed tag or SHA-256 checksum, the libslirp release tarball and `dockerd-rootless.sh` are verified against known SHA-256 checksums, and any verification or compilation failure exits non-zero with a descriptive error

#### Scenario: Pinning regression test

- **WHEN** the test suite runs
- **THEN** it verifies all rootless Docker helper sources are pinned to immutable commits and verified by signature or checksum

### Requirement: Docker daemon security configuration

The KIWI image SHALL include a rootless Docker `daemon.json` (e.g. `~gha-executor/.config/docker/daemon.json`) with explicit hardening, and the executor systemd unit SHALL run as `gha-executor` and connect to the rootless socket. All existing container security constraints SHALL remain enforced under rootless Docker.

#### Scenario: Hardened daemon.json

- **WHEN** the image is built
- **THEN** `daemon.json` sets `no-new-privileges` true, `live-restore` false, and `data-root` to `/var/lib/gha-executor/docker`, omits `userns-remap` (rootless provides namespace isolation), and the configuration is documented in code comments

#### Scenario: Executor connects to rootless socket

- **WHEN** the executor service runs
- **THEN** the systemd unit runs as `gha-executor`, the Script_Executor connects to `/run/user/1000/docker.sock` (not `/var/run/docker.sock`), and `/var/run/docker.sock` is not in `ReadWritePaths`

### Requirement: Systemd service hardening

The executor systemd unit SHALL apply hardening directives so a container breakout has reduced host impact, while accommodating the bind-mount and TPM requirements of the service.

#### Scenario: Hardening directives present

- **WHEN** the systemd unit is defined
- **THEN** it sets `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK`, `StateDirectory=gha-executor`, `LogsDirectory=github-actions-executor`, `ReadWritePaths` including `/var/lib/gha-executor`, `/var/log/github-actions-executor`, and `/tmp`, `DeviceAllow=/dev/tpm0 rw`, and `LimitCORE=0`, and does NOT set `PrivateTmp=true` (which would break Docker bind mounts)

#### Scenario: Ordering and socket readiness

- **WHEN** the executor service starts
- **THEN** the unit declares `After=user@1000.service` and `Requires=user@1000.service` and an `ExecStartPre` that waits (with timeout) for `/run/user/1000/docker.sock`; `TEMP_STORAGE_PATH` is set to `/var/lib/gha-executor` (outside `/tmp`)

#### Scenario: UID and core-dump regression checks

- **WHEN** the test suite runs
- **THEN** it verifies the `gha-executor` UID matches the UID in the unit's Docker socket path and that `LimitCORE=0` is present in the unit

### Requirement: Host login access hardening

The `config.sh` script SHALL lock the root account and mask the serial-getty login prompt unconditionally during image creation, leaving the serial console available for read-only log output.

#### Scenario: Root locked and getty masked

- **WHEN** the image is created (regardless of `ENABLE_SSH`)
- **THEN** `passwd -l root` locks the root account and `systemctl mask serial-getty@ttyS0.service` disables the serial login prompt, while `console=ttyS0` remains active for log output; with SSH debug enabled these controls remain in effect and debug access is via `ec2-user` over SSH only

### Requirement: Runtime image package minimization

The `appliance.kiwi` package definition SHALL maintain a documented runtime allow-list with justification per package and SHALL exclude build/debug/admin tooling not required for executor operation.

#### Scenario: Disallowed packages absent

- **WHEN** `appliance.kiwi` is built
- **THEN** `awscli`, `binutils`, `python3.11-pip`, and `pciutils` are not in the `<packages type="image">` section, `git` remains with documented justification, and a comment block documents the allow-list policy

#### Scenario: Minimization regression test

- **WHEN** the test suite runs
- **THEN** it parses `appliance.kiwi` and verifies `awscli`, `binutils`, `python3.11-pip`, and `pciutils` are not present in the runtime packages

### Requirement: Container image pull at server startup

When the GHA_Server starts, it SHALL obtain the configured Container_Image from the **baked docker-archive** measured into the verity-sealed root — verifying it **offline** (no registry, no network) against the **baked OCI-manifest sidecar** and binding every `containers.create()` call to the derived **image ID** (config digest) rather than the daemon-reported `repository@sha256:<digest>` reference. The server SHALL fail to start, with the same fail-closed semantics as before, if the expected digest is absent, the baked docker-archive or OCI-manifest sidecar is missing or corrupt, or the recomputed manifest digest does not match the expected value. The expected `CONTAINER_IMAGE_DIGEST` is a **manifest** digest and the image ID is the **config** digest; these are distinct values and the executor SHALL NOT compare, substitute, or otherwise conflate one for the other.

#### Scenario: Offline manifest-digest verification

- **WHEN** the server starts
- **THEN** it recomputes the digest as a **byte-exact SHA-256 over the stored OCI-manifest sidecar bytes** (never over a re-canonicalized or re-serialized form of the JSON), and compares it to the expected `CONTAINER_IMAGE_DIGEST` using pure offline hashing — no Docker daemon call and no network — failing to start on mismatch or on a missing/empty expected digest. The build has already resolved and pinned the `linux/amd64` manifest and emitted exactly that blob as the sidecar, so the runtime does not walk an `index.json` or select a child manifest

#### Scenario: Image ID derived from the verified manifest

- **WHEN** the manifest digest is verified
- **THEN** the server reads the config descriptor out of that verified manifest and treats its digest as the trusted **image ID**, derived entirely from verity-measured, digest-verified bytes and independent of any value the daemon reports

#### Scenario: Layout loaded and execution bound to the image ID

- **WHEN** the image ID is derived
- **THEN** the server `docker load`s the baked docker-archive into the existing rootless daemon (legacy graphdriver store; `daemon.json` sets no containerd snapshotter), and constructs the Script_Executor so every `containers.create()` call references the derived image ID rather than a `repository@sha256:<manifest>` string — so the loss of `RepoDigests` across import (and the absence of any repo tag on the loaded archive) does not affect execution

#### Scenario: Unfaithful load fails closed at bind time

- **WHEN** the loader produces a local image whose ID does not equal the image ID derived from the verified manifest (for example a loader that rewrote the config blob)
- **THEN** `containers.create()` referencing the derived image ID fails closed because no loaded image matches that ID, so execution never proceeds against an image other than the one the verified manifest commits to

#### Scenario: Fail-closed without network reachability

- **WHEN** the baked docker-archive or OCI-manifest sidecar is absent or corrupt, or the recomputed manifest digest does not match the expected digest
- **THEN** the server fails to start with a descriptive error, with no fallback to a network pull and without requiring registry reachability at boot

### Requirement: Baked docker-archive and OCI-manifest sidecar in the KIWI root tree

The Build_Workflow SHALL copy the externally-supplied, digest-pinned Container_Image **by digest** into an OCI layout (a build-time intermediate, using a digest-preserving tool such as `oras cp` / `skopeo copy oci:`), then bake **two** files into the KIWI root tree at a fixed path inside the erofs root so `verity_blocks="all"` measures their bytes into PCR4: (a) a **docker-archive** (`docker save`-format) produced by an `oci→docker-archive` conversion, which `docker load` consumes at runtime, and (b) the **OCI manifest blob** copied out byte-for-byte as a **sidecar**, whose `sha256` equals the published `linux/amd64` manifest digest. The copy into the intermediate layout SHALL pin the `linux/amd64` per-platform manifest (never a multi-platform index digest). The conversion SHALL preserve the **config blob** (and thus the image ID) and SHALL NOT rebuild the image or rewrite the config JSON; it MAY re-serialize the manifest envelope to docker schema2, because the manifest digest is anchored by the sidecar, not the archive. The build SHALL NOT perform a bake-time provenance check of the image.

#### Scenario: Image copied by digest into the verity-measured root

- **WHEN** the KIWI image is built
- **THEN** the Container_Image is copied by its `linux/amd64` manifest digest into an intermediate OCI layout, and a docker-archive plus an OCI-manifest sidecar derived from it are placed at a fixed path in the KIWI root tree, landing there before the image is finalized so `verity_blocks="all"` measures their bytes into PCR4

#### Scenario: Digest-preserving copy, config-preserving conversion

- **WHEN** the image is copied and converted
- **THEN** the copy into the intermediate layout is digest-preserving so the manifest blob (later emitted as the sidecar) is byte-identical to the GHCR artifact and the `linux/amd64` child manifest is resolved and pinned if a multi-arch index is supplied; the `oci→docker-archive` conversion preserves the config blob so the image ID is unchanged; and the image is never rebuilt nor its config JSON rewritten

#### Scenario: No bake-time provenance verification

- **WHEN** the image is baked
- **THEN** the build trusts the digest-pinned reference and does NOT verify the image's Sigstore provenance, leaving provenance verifiable by consumers on GHCR

### Requirement: Container image digest surfaced for external verifiers

When the build publishes the KIWI artifact, the Artifact_Publisher SHALL surface the baked image's manifest digest (`container_image_digest`) through publish-time outputs — the GitHub job log, the step summary, and an ORAS annotation on the published artifact — so external verifiers can read which image the artifact (and any AMI built from it) carries without running the instance. This self-description SHALL NOT be carried in the runtime attestation `user_data`.

#### Scenario: Digest reported at publish time

- **WHEN** the KIWI artifact is pushed to GHCR
- **THEN** `container_image_digest` is written to the job log and the step summary and attached as an ORAS annotation on the published artifact, alongside the existing `pcr4`/`pcr7` annotations

#### Scenario: Attestation user_data unchanged

- **WHEN** the runtime attestation is produced
- **THEN** no `container_image_digest` self-description is added to `user_data`, because PCR4 already binds the baked image bytes

### Requirement: Per-flavor execution-container image build and publish

The build pipeline SHALL build one hardened execution-container image per flavor selected by the dynamic matrix, from `flavors/<flavor>/Dockerfile` and its supplements, and publish each to GHCR **by immutable digest** at `ghcr.io/<owner>/<repo>/<flavor>`. Each per-flavor image SHALL satisfy the executor's hardened contract (rootless `65534`, world-exec tools on PATH, pinned, no run-time install) exactly as the single-flavor image did. The published artifact for a flavor SHALL be referenced everywhere by its amd64 per-platform manifest digest, never a mutable tag and never a multi-arch index digest.

#### Scenario: Each selected flavor produces a digest-pinned image

- **WHEN** the matrix selects flavors at image level
- **THEN** the pipeline builds each flavor's `Dockerfile` and publishes its image to GHCR, capturing the amd64 manifest digest for downstream bake and `flavors.lock`

#### Scenario: Hardened contract preserved per flavor

- **WHEN** a per-flavor image is built
- **THEN** it ships rootless `65534`, world-exec tools on PATH, pinned inputs, and no run-time install, identically to the single-flavor contract

### Requirement: Per-flavor baked OCI layout in the KIWI root tree

The KIWI image build SHALL be parameterized from one flavor to N, baking each selected flavor's published image into its own AMI as a digest-preserving OCI image layout inside the dm-verity-sealed erofs root, reusing the offline-import, offline-verify, and image-ID runtime-binding mechanism delivered single-flavor by the `bake-image-into-ami` change. The OCI layout baked for a flavor SHALL correspond to that flavor's amd64 manifest digest as recorded in `flavors.lock`, and the GHCR image (same manifest digest) SHALL remain the canonical provenance reference.

#### Scenario: Flavor image baked by its recorded digest

- **WHEN** the KIWI build runs for a flavor
- **THEN** it bakes the OCI layout for that flavor's amd64 manifest digest into the verity root, preserving byte-identity with the GHCR image

#### Scenario: AMI-only rebuild reuses the published image

- **WHEN** only a flavor's `env` changed (AMI-only level)
- **THEN** the KIWI build re-bakes the AMI using the flavor's existing image digest from `flavors.lock`, without rebuilding the image

#### Scenario: Mechanism reused unchanged

- **WHEN** a flavor's image is baked
- **THEN** the offline import/verify and image-ID runtime binding behave exactly as in the single-flavor `bake-image-into-ami` mechanism, multiplied per flavor

### Requirement: Optional SSH debug build with production gate

The Build_Workflow SHALL default to building without SSH access and SHALL only include SSH packages and enable `sshd` when explicitly requested via a `workflow_dispatch` `enable_ssh` input, annotating the published artifact so debug images cannot be silently converted to production AMIs.

#### Scenario: Default excludes SSH

- **WHEN** the workflow is triggered by push, pull_request, schedule, or `workflow_dispatch` with `enable_ssh: false`
- **THEN** the image excludes `openssh-server`, `cloud-init`, `cloud-init-cfg-ec2`, and `ec2-instance-connect` and does not enable `sshd`

#### Scenario: Debug build enables SSH with warning

- **WHEN** `workflow_dispatch` is triggered with `enable_ssh: true`
- **THEN** the build passes `--enable-ssh` (removing the ignore directives and enabling `sshd` via `ENABLE_SSH`), relies on cloud-init/ec2-instance-connect for key provisioning (no baked-in keys), and appends a visually distinct production-use warning to `GITHUB_STEP_SUMMARY`

#### Scenario: Debug annotation and converter gate

- **WHEN** the artifact is published
- **THEN** ORAS annotates it `debug=true` (SSH build) or `debug=false`, and the AMI_Converter refuses to build an AMI from a `debug=true` artifact — or when the annotation is indeterminate (manifest fetch or JSON parse failure) — unless `--allow-debug` is explicitly provided (in which case it logs a prominent warning); the gate fails closed and is covered by regression tests
