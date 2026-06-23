## MODIFIED Requirements

### Requirement: build-ami job dependency and trigger

The `build-ami` job SHALL declare `needs: [detect-changes, build-flavor-image]`, where
`build-flavor-image` is the **single** per-flavor producer job (parametrized by rebuild
level), so it runs only after that producer completes. Because there is no second,
conditionally-skipped sibling producer, the job SHALL express its trigger and skip behavior
**without** `always()` and without a hand-written upstream-result boolean: ordinary `needs`
resolution combined with the job's own ref/event gate SHALL suffice. The job SHALL run on
pushes to `main`, on `workflow_dispatch` with `enable_ssh: false`, and on
`workflow_dispatch` with `enable_ssh: true` (Debug_Build), but SHALL be skipped on pushes
to `develop` and SHALL be skipped when the producer produced nothing to build.

#### Scenario: Runs after the producer on applicable triggers

- **WHEN** the workflow is triggered by a push to `main`, or by `workflow_dispatch` with
  either value of `enable_ssh`, and the producer job built at least one flavor
- **THEN** the `build-ami` job executes after the single `build-flavor-image` producer
  completes successfully, via ordinary `needs` resolution (no `always()`)

#### Scenario: Skipped on develop

- **WHEN** the workflow is triggered by a push to `develop`
- **THEN** the `build-ami` job is skipped

#### Scenario: Cascade-skips cleanly when nothing was built

- **WHEN** a run produces an empty rebuild matrix (no flavors to build)
- **THEN** the producer job and `build-ami` skip cleanly through ordinary `needs`
  propagation, with no `always()` gate needed to suppress a spurious run

### Requirement: AMI_Build_Script invocation and outputs

The `build-ami` job SHALL invoke AMI_Build_Script with the digest-pinned artifact reference and supporting arguments, upload the result JSON, and summarize it, passing `--allow-debug` only for Debug_Builds.

#### Scenario: Script arguments

- **WHEN** the `build-ami` job invokes AMI_Build_Script
- **THEN** it passes `--artifact-ref ${{ needs.build-flavor-image.outputs.artifact_ref }}`, `--region <configured region>`, `--output-file ami_build_result.json`, and `--expected-workflow .github/workflows/build-attestable-image.yml`

### Requirement: Single-entry verifier record emission

After the AMI is registered, the `build-ami` job SHALL emit a **single-entry verifier record** mapping the baked image's manifest digest → PCR4 → AMI id → producing commit through the surfaces available **after** the AMI exists: the GitHub job log, the step summary, and a tag on the registered AMI. The container image manifest digest is **additionally** carried as an ORAS annotation on the published KIWI artifact, but that annotation is fixed at publish time (alongside the existing `pcr4`/`pcr7` annotations) and SHALL NOT be amended with the AMI id afterward — the published artifact is immutable and digest-bound by its Sigstore attestation, and the AMI id does not exist until publishing has completed. The record SHALL be a clean single-entry seed whose field set is a subset of the multi-flavor `flavors.lock` introduced by the `execution-build-images` change, and SHALL NOT change the runtime NitroTPM attestation or its `user_data`.

#### Scenario: Published-artifact annotation carries only the image digest

- **WHEN** the KIWI artifact was published earlier by the build-flavor-image job
- **THEN** the container image manifest digest is the only verifier-record field carried as an ORAS annotation on that artifact (alongside `pcr4`/`pcr7`), and the AMI id is never added to the published artifact's annotations — because amending them would change the artifact digest and break its Sigstore attestation, and because the AMI id does not yet exist at publish time
