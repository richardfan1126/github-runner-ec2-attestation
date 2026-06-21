## 1. Flavor layout migration (D6, D8)

- [x] 1.1 Create `flavors/default/env` from the current `kiwi-descriptions/root/etc/github-actions-remote-executor/env`, **stripping** the bucket-③ keys (`CONTAINER_IMAGE`, `CONTAINER_IMAGE_DIGEST`) and the per-flavor authorization keys (`ALLOWED_REPOSITORIES`, `EXPECTED_AUDIENCE`); keep only shared bucket-② declared values (ports, timeouts, rate limits, retention, paths, resource defaults)
- [x] 1.2 Create the first real flavor `flavors/<flavor>/` (e.g. the rust-build demo) with its `Dockerfile` (+ supplements) satisfying the hardened contract (rootless `65534`, world-exec tools on PATH, pinned, no run-time install)
- [x] 1.3 Add a minimal `flavors/<flavor>/env` declaring this flavor's `ALLOWED_REPOSITORIES` and `EXPECTED_AUDIENCE` (moved out of step 1.1) plus any resource/limit overrides
- [x] 1.4 Remove the old single-flavor `kiwi-descriptions/root/etc/github-actions-remote-executor/env` from the source tree (it is now produced by the merge), updating any references to it

## 2. Effective-config merge and validation (D7, D8, D10, D11)

- [x] 2.1 Implement the fixed-precedence merge `code defaults ◀ flavors/default/env ◀ flavors/<flavor>/env ◀ pipeline-injected bucket ③`, producing a deterministic effective env, routed through `src/config.py::load_config()` so no second schema exists
- [x] 2.2 Implement the pre-bake validator (D11, D16) that fails the build fast over every committed `env` (default or flavor) on two checks: (a) any hand-set bucket-③ key (`CONTAINER_IMAGE`/`CONTAINER_IMAGE_DIGEST`), and (b) any **unknown key** — anything not in the recognized-key set derived from `ServerConfig`'s field→env-var enumeration (the same enumeration `print_config.py` walks, so no second schema); place it alongside `print_config.py`/`load_config()`
- [x] 2.3 Implement derived-digest injection: after image push, inject `CONTAINER_IMAGE=ghcr.io/<owner>/<repo>/<flavor>` and `CONTAINER_IMAGE_DIGEST=sha256:…` (amd64 per-platform manifest digest, never a multi-arch index) into the effective env before bake
- [x] 2.4 Enforce deny-all authorization: confirm the merged env fails the build-time config-resolution gate when a flavor supplies no `ALLOWED_REPOSITORIES`/`EXPECTED_AUDIENCE` (required keys in `load_config()`), so a deny-all flavor never ships an AMI
- [x] 2.5 Extend `print_config.py` to print the **effective merged** config per flavor (grouped by category, "Other" last, no redaction), labeled with the flavor name; fail the flavor's build if the config is unresolvable

## 3. Flavor enumeration and selective rebuild (D12)

- [x] 3.1 Implement flavor enumeration as `ls flavors/` minus the explicitly-excluded `default` entry (directory tree is the manifest; no central index)
- [x] 3.2 Implement the `detect-changes` job: map changed paths to affected flavors on three levels — global invalidators → all flavors; `flavors/<f>/**` except `env` → image level (image + AMI); `flavors/<f>/env` alone → AMI-only level
- [x] 3.3 Emit a dynamic build matrix from `detect-changes`, distinguishing image-level vs AMI-only entries, with bounded `max-parallel`
- [x] 3.4 Implement fail-safe/edge rules: no diff baseline → build ALL; empty changed set → empty matrix (skip cleanly, `flavors.lock` untouched); `flavors.lock`-only diff → empty matrix (write-back loop guard); `workflow_dispatch` override forces a flavor or `all`
- [x] 3.5 Record the `detect-changes` rebuild decision (commit → flavors) in the GitHub Actions run summary

## 4. Per-flavor image build and publish (image-build delta)

- [x] 4.1 Build one execution-container image per image-level matrix flavor from `flavors/<flavor>/Dockerfile` and publish to GHCR by immutable digest at `ghcr.io/<owner>/<repo>/<flavor>`, capturing the amd64 per-platform manifest digest
- [x] 4.2 Parameterize `.github/scripts/build-kiwi-image.sh` from one flavor to N, baking the selected flavor's published image as a digest-preserving OCI layout into the dm-verity-sealed erofs root (reusing the `bake-image-into-ami` offline-import/verify/image-ID-binding mechanism)
- [x] 4.3 For AMI-only matrix entries, bake reusing the flavor's existing image digest from `flavors.lock` without rebuilding the image

## 5. Per-flavor AMI build matrix (ami-build delta)

- [x] 5.1 Run the `build-ami` stage once per matrix flavor (one attestable AMI per flavor carrying its baked OCI layout and effective sandbox config; never a shared AMI), with bounded `max-parallel`
- [x] 5.2 Apply the existing `develop`-skip rule: on `develop` build/publish changed-flavor images but register no AMIs; full vertical (AMI registration) only on `main`
- [x] 5.3 Verify two flavors with differing effective sandbox config produce AMIs with distinct PCR4 values

## 6. flavors.lock durable record (D13, container-security delta)

- [x] 6.1 Define the `flavors.lock` schema: per-flavor `{image manifest digest, PCR4, AMI id, producing commit}`, generalizing Change 1's single-entry verifier record with the same field set
- [x] 6.2 After each AMI is registered, write that flavor's entry to `flavors.lock`, recording `producing commit` as the source commit `C_src` (not the pipeline's write-back commit) — note `scripts/build-ami.py` today records `producing_commit` as the run's own SHA, so it must learn the `C_src` vs. write-back-commit distinction when generalizing its single-entry `verifier_record`
- [x] 6.3 Carry forward unchanged entries for flavors not rebuilt; commit `flavors.lock` back to git; serialize updates via a concurrency group
- [x] 6.4 Ensure a `workflow_dispatch` debug/SSH build targeting a single flavor does NOT overwrite that flavor's production `flavors.lock` entry (compose with the existing `debug=true` gate)

## 7. Attestation binding and summary (container-security delta)

- [x] 7.1 Confirm each flavor's effective sandbox config is baked into the verity root and bound by PCR4 (attested-at-rest, not `user_data`-asserted)
- [x] 7.2 Surface every relaxation of a bucket-① hardened default non-silently: baked, reflected in PCR4, shown in the per-flavor config summary, and recorded in `flavors.lock`

## 8. Tests and documentation

- [x] 8.1 Test the precedence merge: flavor delta overrides shared default; unset bucket-① key falls through to hardened default; effective env is reconstructible from committed inputs + recorded digest
- [x] 8.2 Test the pre-bake validator rejects (a) hand-set `CONTAINER_IMAGE`/`CONTAINER_IMAGE_DIGEST` (including migration leftovers in `default/env`) and (b) an unknown/misspelled key (e.g. `NO_NEW_PRIVILEGE=true`), failing the build rather than silently dropping it; confirm a valid key in `ServerConfig`'s enumeration passes
- [x] 8.3 Test flavor enumeration excludes `default` explicitly; test `detect-changes` mapping for each level and the fail-safe/loop-guard edge cases
- [x] 8.4 Test deny-all: a flavor with no allowlist fails the build-time gate; an executor booted with empty allowlist denies all callers
- [x] 8.5 Update project docs (README/specs references) for the flavor model, the `flavors/` layout, the merge precedence, and how to add a flavor
