## ADDED Requirements

### Requirement: Per-flavor AMI build matrix

The `build-ami` stage SHALL run once per flavor selected by the dynamic matrix, producing one attestable AMI per flavor that carries that flavor's baked OCI layout and effective sandbox config. A single AMI SHALL NOT be shared across flavors. The matrix SHALL bound `max-parallel` (each AMI build is an EC2 instance) and SHALL apply the existing `develop`-skip rule (build/publish images on `develop`, register AMIs only on `main`).

#### Scenario: One AMI per selected flavor

- **WHEN** the matrix selects two flavors for full rebuild
- **THEN** the stage registers two distinct AMIs, each carrying its own flavor's baked image and effective config, with distinct PCR4 values

#### Scenario: develop skips AMI registration

- **WHEN** the pipeline runs on `develop`
- **THEN** changed-flavor images are built and published but no AMIs are registered

### Requirement: Multi-flavor flavors.lock aggregation written back by the pipeline

After each per-flavor AMI is registered, the pipeline SHALL aggregate the per-flavor verifier records into the git-committed `flavors.lock`, generalizing the single-entry verifier record (one image manifest digest → PCR4 → AMI id → producing commit) from one image to N flavors using the same field set. The pipeline SHALL write back and commit `flavors.lock`, carrying forward unchanged entries for flavors not rebuilt, serializing updates via a concurrency group, and recording each flavor's `producing commit` as the source commit that supplied its inputs.

#### Scenario: Each registered AMI lands an entry

- **WHEN** a flavor's AMI is registered
- **THEN** its `{image manifest digest, PCR4, AMI id, producing commit}` entry is written into `flavors.lock` and committed back, while the per-AMI post-registration surfaces (job log, step summary, AMI tag) continue to be emitted as in the single-entry record

#### Scenario: Subset rebuild preserves other entries

- **WHEN** a run rebuilds only some flavors
- **THEN** `flavors.lock` retains the existing entries for the flavors not rebuilt

#### Scenario: Debug build does not overwrite production entry

- **WHEN** a `workflow_dispatch` debug/SSH build targets a single flavor
- **THEN** it does not overwrite that flavor's production `flavors.lock` entry
