## ADDED Requirements

### Requirement: Single-entry verifier record emission

After the AMI is registered, the `build-ami` job SHALL emit a **single-entry verifier record** mapping the baked image's manifest digest → PCR4 → AMI id → producing commit through the surfaces available **after** the AMI exists: the GitHub job log, the step summary, and a tag on the registered AMI. The container image manifest digest is **additionally** carried as an ORAS annotation on the published KIWI artifact, but that annotation is fixed at publish time (alongside the existing `pcr4`/`pcr7` annotations) and SHALL NOT be amended with the AMI id afterward — the published artifact is immutable and digest-bound by its Sigstore attestation, and the AMI id does not exist until publishing has completed. The record SHALL be a clean single-entry seed whose field set is a subset of the multi-flavor `flavors.lock` introduced by the `execution-build-images` change, and SHALL NOT change the runtime NitroTPM attestation or its `user_data`.

#### Scenario: Record emitted across post-AMI surfaces

- **WHEN** AMI registration succeeds
- **THEN** the job emits a record containing the container image manifest digest, PCR4, the registered AMI id, and the producing commit to the job log and the step summary, and tags the registered AMI with the container image manifest digest

#### Scenario: Published-artifact annotation carries only the image digest

- **WHEN** the KIWI artifact was published earlier by the build-and-publish job
- **THEN** the container image manifest digest is the only verifier-record field carried as an ORAS annotation on that artifact (alongside `pcr4`/`pcr7`), and the AMI id is never added to the published artifact's annotations — because amending them would change the artifact digest and break its Sigstore attestation, and because the AMI id does not yet exist at publish time

#### Scenario: Verifier join is PCR4

- **WHEN** a remote verifier reads the published record
- **THEN** it can obtain a live NitroTPM attestation showing that PCR4 and conclude the instance runs the recorded image digest, with no change to the runtime attestation or `user_data` required

#### Scenario: Single-entry seed for flavors.lock

- **WHEN** the record is serialized
- **THEN** it is a single entry (one image manifest digest → PCR4 → AMI id → producing commit) whose field set is a subset of the per-flavor `flavors.lock` that the `execution-build-images` change introduces, so that change need not retrofit a record format onto an already-shipped AMI
