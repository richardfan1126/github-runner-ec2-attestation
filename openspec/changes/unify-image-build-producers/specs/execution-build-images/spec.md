## ADDED Requirements

### Requirement: Per-flavor builds run in a single producer job parametrized by rebuild level

The two per-flavor rebuild levels (image-level and AMI-only) SHALL be driven by a **single
producer job** whose build matrix carries a per-entry rebuild-level dimension (e.g.
`mode: image | ami-only`), NOT by two separate conditionally-run sibling jobs. The shared
build steps (KIWI image build, PCR extraction, config attestation, artifact push,
provenance attestation, build-context upload) SHALL exist in exactly one place; only the
container-image-digest source SHALL branch on the rebuild level — building the container
and deriving a fresh amd64 manifest digest for `image`, or reading the existing digest from
`flavors.lock` for `ami-only`. `detect-changes` SHALL emit a single rebuild-level-tagged
producer matrix rather than two disjoint matrices.

Because there is exactly one producer job (never a conditionally-skipped sibling), the
downstream `build-ami` job SHALL NOT require `always()` or hand-written upstream-result
booleans to avoid transitive skips; ordinary `needs` resolution SHALL suffice.

#### Scenario: Image-level and AMI-only flavors share one producer job

- **WHEN** a run rebuilds one flavor at image level and another at AMI-only level
- **THEN** both are produced by the same producer job as distinct matrix entries
  distinguished by their rebuild-level dimension, with the container-digest source the only
  step that differs between them

#### Scenario: AMI-only entry reuses the recorded image digest

- **WHEN** a producer matrix entry has rebuild level `ami-only`
- **THEN** the job reads that flavor's `container_image_digest` from `flavors.lock` instead
  of building a new container image, and fails if no such entry exists

#### Scenario: No conditionally-skipped sibling forces always() downstream

- **WHEN** a run rebuilds only AMI-only flavors (or only image-level flavors)
- **THEN** the single producer job runs for exactly those flavors and `build-ami` resolves
  its dependency through ordinary `needs` (no `always()`), without any sibling producer
  being skipped to trip transitive-skip propagation

## MODIFIED Requirements

### Requirement: flavors.lock is the git-committed durable record written by the pipeline

`flavors.lock` SHALL be committed to git as the durable source of truth mapping each flavor
to its image manifest digest, PCR4, AMI id, and producing commit. It SHALL be
machine-written and committed back by the pipeline (GitHub Actions) after a flavor's AMI is
registered; flavors not rebuilt in a run SHALL be carried forward unchanged. Updates SHALL
be serialized via a concurrency group. The `producing commit` field SHALL record the source
commit that supplied the flavor's inputs (`C_src`), not the pipeline's own write-back
commit, so a verifier dereferences the correct tree for `flavors/default/env` and
`flavors/<flavor>/env`.

The write-back job SHALL run after **any** successful AMI registration regardless of which
rebuild level (image-level or AMI-only) produced it, and SHALL NOT be skipped as a side
effect of a conditionally-skipped upstream producer. Concretely, the write-back's execution
condition SHALL depend on the AMI-build job's own result rather than on an implicit
success-of-all-ancestors evaluation that an `always()`-rescued upstream job would poison.

#### Scenario: Pipeline writes and commits the record

- **WHEN** a flavor's AMI is registered
- **THEN** the pipeline writes that flavor's `{image manifest digest, PCR4, AMI id,
  producing commit}` entry into `flavors.lock` and commits it back to the repository

#### Scenario: Unchanged flavors carried forward

- **WHEN** a run rebuilds a subset of flavors
- **THEN** the entries for flavors not rebuilt are preserved unchanged in `flavors.lock`

#### Scenario: Producing commit points at the inputs

- **WHEN** the pipeline records `producing commit` for a flavor
- **THEN** it stores the source commit `C_src` that supplied `flavors/<flavor>/**` and
  `flavors/default/env`, not the bot's write-back commit, so reconstruction reads the env
  inputs from the right tree

#### Scenario: Record generalizes the single-entry seed

- **WHEN** `flavors.lock` is serialized
- **THEN** each entry's field set matches the single-entry verifier-record seed emitted by
  the `ami-build` capability, generalized from one image to N flavors

#### Scenario: Write-back runs after a successful AMI build regardless of rebuild level

- **WHEN** the AMI-build job succeeds for a run that rebuilt only AMI-only flavors (so the
  image-level path produced nothing)
- **THEN** the `flavors.lock` write-back job still runs and commits the updated record,
  rather than being transitively skipped because of a conditionally-skipped upstream
  producer

#### Scenario: Write-back skips cleanly when no AMI was built

- **WHEN** a run results in no AMI registration (the AMI-build job did not succeed — e.g. an
  empty matrix or a develop-branch skip)
- **THEN** the `flavors.lock` write-back job does not run and `flavors.lock` is left
  untouched
