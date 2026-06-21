## ADDED Requirements

### Requirement: Per-flavor effective sandbox config is baked and bound by PCR4

Each flavor's effective sandbox configuration — the result of the fixed precedence merge (code hardened defaults ◀ `flavors/default/env` ◀ `flavors/<flavor>/env` ◀ injected image identity) — SHALL be baked into that flavor's dm-verity-sealed verity root and thereby bound by **PCR4**. This is a shift from runtime operator-set configuration to attested-at-rest configuration: the security posture of a flavor SHALL be determined by what is measured into its AMI, not by `user_data` set at deploy time. Two flavors with different sandbox configs SHALL therefore have different PCR4 values.

#### Scenario: Posture is measured, not asserted at runtime

- **WHEN** a flavor's AMI boots
- **THEN** its effective sandbox configuration is the one baked into the verity root and reflected in PCR4, not a value supplied at deploy time

#### Scenario: Distinct posture yields distinct PCR4

- **WHEN** two flavors differ in any security-relevant effective setting
- **THEN** their AMIs have distinct PCR4 values, so PCR4 distinguishes the flavors' postures

### Requirement: Per-flavor relaxations are measured and recorded

When a flavor relaxes a hardened default in its baked effective config, that relaxation SHALL be non-silent end to end: it changes the flavor's PCR4, appears in that flavor's build-time configuration summary, and is recorded in `flavors.lock`. Secure-by-default-overridable SHALL hold per flavor — a flavor that relaxes nothing carries the full hardened posture.

#### Scenario: Relaxation is attested for that flavor

- **WHEN** a flavor sets `CONTAINER_ALLOW_ROOT=true` (or another relaxation)
- **THEN** the relaxed value is baked, alters the flavor's PCR4, is shown in the flavor's configuration summary, and is recorded in that flavor's `flavors.lock` entry

#### Scenario: Unrelaxed flavor stays hardened

- **WHEN** a flavor relaxes no hardened default
- **THEN** its baked effective config carries the full hardened posture and its PCR4 reflects the hardened defaults

## MODIFIED Requirements

### Requirement: Build-time configuration summary

The `Build Attestable Image` GitHub Actions workflow SHALL print, on its run summary, the full server configuration built into **each built flavor's** AMI artifact — every effective `ServerConfig` setting (a superset of the `.env.example` keys, including the nine container-security settings), enumerated from the configuration object so the set cannot drift. The printed values SHALL be the flavor's **effective merged** configuration (code hardened defaults ◀ `flavors/default/env` ◀ `flavors/<flavor>/env` ◀ injected image identity), resolved through the application's own configuration loader, printed verbatim with no redaction. Settings SHALL be grouped by category into labeled per-category subsections in a stable order with a catch-all "Other" group last. The summary SHALL identify which flavor each block describes.

#### Scenario: Summary derived from baked-in effective config per flavor

- **WHEN** the workflow prints the server configuration summary for a built flavor
- **THEN** every effective `ServerConfig` setting (including `container_tmpfs_exec` and `container_tmpfs_size`) appears for that flavor, grouped by category with "Other" last, with values matching the flavor's merged effective config that the AMI ships with, and labeled with the flavor name

#### Scenario: Unresolvable config fails the build

- **WHEN** the workflow cannot resolve the effective configuration baked into a flavor's AMI (e.g. the config loader rejects the merged env)
- **THEN** the workflow fails that flavor's build before publishing its artifact rather than publishing without the configuration summary
