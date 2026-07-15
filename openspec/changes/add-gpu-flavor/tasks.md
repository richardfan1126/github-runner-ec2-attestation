# Tasks — add-gpu-flavor

Two tiers. **Tier 1 (§1–§4)** is spec-satisfying code work: a developer can finish it and CI can
prove it — the merge gate. **Tier 2 (§5–§6)** validates un-attested runtime preconditions on real
G-series hardware and hands off downstream — the *reliance* gate, not the merge gate (these tasks
cannot be checked off at a desk; they map to no spec scenario by design — see design.md Risks).

The only cross-file ordering constraint: §1 (build wiring) MUST land together with §3 (the flavor
env), or a `gpu-presence/env` with `ENABLE_GPU=true` bakes the broken driver-less/runtime-on AMI the
proposal warns about. §1 and §2 are otherwise independent.

## 1. Build wiring — flavor env is the single source of truth (D3)

- [ ] 1.1 In `.github/scripts/build-kiwi-image.sh`, derive the `ENABLE_GPU` shell variable by `grep`-ing the key out of the `--env-file` it already receives, mirroring the existing `CONTAINER_IMAGE`/`CONTAINER_IMAGE_DIGEST` reads at `:498-499`. Place the derivation after the effective env is copied into the build context (`:87`) and before the consumers at `:105` (image-size bump) and `:622` (`-e ENABLE_GPU` into the KIWI builder). Absent key ⇒ `false`.
- [ ] 1.2 Delete the `--enable-gpu` CLI flag: the default at `:23-24` and the arg parse at `:31-33`. It is dead in the real pipeline (the workflow passes only `--env-file` + `$SSH_FLAG`), so removal changes no live behavior.
- [ ] 1.3 Verify a non-GPU flavor (`default`/`rust-build`, no `ENABLE_GPU` key) derives `false`, installs no driver, applies no size bump, and builds byte-for-byte as before.

## 2. Toolkit bump for read-only rootfs (D5)

- [ ] 2.1 In `kiwi-descriptions/config.sh`, bump `NVIDIA_CTK_VERSION` from `1.18.2-1` to `1.19.1-1` (`:248`) and update the rationale comment (`:246`) to cite the two reasons: native read-only-rootfs support (landed 1.19.0) and the 1.19.1 fix removing the `multi-user.target` dependency from `nvidia-cdi-refresh.service` — the exact unit enabled at `:283`.
- [ ] 2.2 Confirm `nvidia-container-toolkit-1.19.1-1` is available in the NVIDIA toolkit RPM repo for AL2023/`$basearch` (the repo configured at `config.sh:152`) — a `dnf`/repo check before relying on the pin.

## 3. The `gpu-presence` flavor files (D1 / D2 / D4 / D6)

- [ ] 3.1 Add `flavors/gpu-presence/env`: `ENABLE_GPU=true`, `GPU_DEVICES=all`, `NVIDIA_DRIVER_CAPABILITIES=utility` (not `compute,utility` — D2), `CONTAINER_READ_ONLY_ROOTFS=true` and `CONTAINER_NETWORK_MODE=none` (D6), plus per-flavor authorization `ALLOWED_REPOSITORIES` (including the `github-runner-ec2-attestation-gpu-demo` repo) and `EXPECTED_AUDIENCE` matching the demo's OIDC dispatch. Follow `flavors/rust-build/env` for shape.
- [ ] 3.2 Add `flavors/gpu-presence/Dockerfile`: `FROM debian:bookworm-slim` pinned to its amd64 manifest `@sha256:` digest, then `USER 65534:65534`. **No `RUN` layer, no GPU packages** — confirmed against the demo's `gpu-presence-workload.sh`, which invokes only CDI-injected `nvidia-smi` + bash builtins. `bash` ships in the base; driver + `nvidia-smi` arrive at runtime via CDI.
- [ ] 3.3 Confirm the flavor's env keys all pass `validate_env.py` (every GPU key is already in `RECOGNIZED_ENV_KEYS`; `NVIDIA_DRIVER_CAPABILITIES` is a deny-listed server-controlled override with no allow-list gate, so `utility` is accepted).

## 4. Tests — satisfy the spec regression scenarios

- [ ] 4.1 Regression test (image-build spec, "Toolkit-version regression check"): assert `NVIDIA_CTK_VERSION` in `config.sh` is ≥ 1.19.0.
- [ ] 4.2 Regression test (image-build spec, "Baked-driver regression check"): assert no `flavors/*/Dockerfile` installs NVIDIA driver, `nvidia-smi`, CUDA, or `nvidia-utils` packages — mirroring the existing package-minimization regression test.
- [ ] 4.3 Test (image-build spec, "Single source of truth"): assert `build-kiwi-image.sh` derives `ENABLE_GPU` from the env file and carries no `--enable-gpu` flag.

## 5. Build + downstream handoff (Tier 2 — reliance gate)

- [ ] 5.1 Confirm `detect_changes.py` auto-enrolls `flavors/gpu-presence/` into the build matrix (it enumerates `flavors/` minus `default`; no matrix code change expected).
- [ ] 5.2 On a real pipeline run, confirm the `gpu-presence` row `{image digest, PCR4, AMI id, producing commit}` is written to `flavors.lock` (automatic pipeline output, like the single-flavor PCR4 in prior changes).
- [ ] 5.3 Point verifier policy at the new GPU-flavor PCR4 recorded in `flavors.lock`. *(Largely the consumer/demo's concern — the demo verifies against `flavors.lock`; recorded here as the handoff, not necessarily code in this repo.)*

## 6. Hardware spike — validate the two runtime preconditions (Tier 2 — needs G-series)

Run on a NitroTPM G-series instance (G4dn/G5/G6/G6e) booting the built AMI, **as the sandbox user under
read-only rootfs**. Confirms documented, default-on 1.19.0 features integrate in our exact stack
(rootless + `no-cgroups` + CDI mode + `--user 65534` + AL2023). Cannot be completed at a desk.

- [ ] 6.1 Leg (a) — container path (the workload demonstration): `docker run --runtime=nvidia --read-only --user 65534:65534 -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=utility <base> nvidia-smi -L` succeeds. The `--read-only` and `--user 65534:65534` flags are load-bearing — dropping either hides a precondition.
- [ ] 6.2 Leg (b) — host path (the load-bearing consumer that actually produces the `gpu` block): `sudo -u gha-executor python3 -c 'import pynvml; pynvml.nvmlInit(); print(pynvml.nvmlDeviceGetCount())'` (or, cheaply, `sudo -u gha-executor nvidia-smi -L`) succeeds. Leg (b) is easy to omit and essential: a passing (a) with a failing (b) leaves the real attestation read broken.
- [ ] 6.3 Record the outcome and apply the scoped fallback only if a leg fails (contingent — expected not needed):
  - read-only rootfs still forces container writes ⇒ explicit, attested `CONTAINER_READ_ONLY_ROOTFS=false` on the GPU flavor only.
  - additional-GID injection misbehaves under rootless (expected fine: injected GID is `0`, always mapped) ⇒ set the `no-additional-gids-for-device-nodes` CDI feature flag where `config.sh` refreshes the CDI spec, falling back to the `0666` world-access path.
  - both DAC paths fail ⇒ deterministic device-mode + explicit `group_add` grant (last resort; note it re-introduces the rootless-subgid entanglement — see design.md Risks).
