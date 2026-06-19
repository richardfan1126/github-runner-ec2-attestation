# Requirements Quality Checklist: Configurable Execution Permission on the Container Scratch tmpfs

**Purpose**: Lightweight author-side sanity pass to confirm the spec's requirements are complete, clear, consistent, and measurable before running `/speckit-plan`.
**Created**: 2026-06-18
**Feature**: [spec.md](../spec.md)

**Depth**: Lightweight sanity · **Audience**: Author (pre-plan) · **Focus**: Security posture, Attestation & observability, Config parsing & validation, Edge cases & interactions

## Security Posture Requirements

- [x] CHK001 Is the secure default (`noexec` when the variable is unset) stated unambiguously and tied to "no change for existing deployments"? [Clarity, Spec §FR-002]
- [x] CHK002 Are all protections that MUST remain unchanged when exec is enabled explicitly enumerated (`mode=1777`, configured size, `nosuid`, `nodev`)? [Completeness, Spec §FR-006]
- [x] CHK003 Is it specified that enabling adds *only* the `exec` option, with no other mount relaxation? [Clarity, Spec §FR-006, §SC-005]
- [x] CHK004 Are requirements documented stating the exec relaxation does not affect other container controls (`cap_drop=ALL`, no-new-privileges, network mode, memory/CPU/pids limits)? [Coverage, Spec §Assumptions, §SC-005]

## Attestation & Observability Requirements

- [x] CHK005 Are all surfaces where the effective value must appear specified (attested `user_data` *and* build-time configuration summary)? [Completeness, Spec §FR-009, §FR-010]
- [x] CHK006 Is the requirement that the build-summary value be derived programmatically (so it cannot drift from the baked image) clearly worded? [Clarity, Spec §FR-010]
- [x] CHK007 Is the no-drift requirement between reported posture and the mount actually applied stated as objectively checkable? [Measurability, Spec §FR-008]
- [x] CHK008 Is the attested field's name/representation specified, or left to existing convention without explicit statement? [Gap, Spec §FR-009]

## Configuration Parsing & Validation Requirements

- [x] CHK009 Is the strict-boolean grammar defined concretely (which exact strings are accepted as truthy/falsy) rather than only by analogy to other toggles? [Clarity, Spec §FR-003]
- [x] CHK010 Is fail-fast-at-startup behavior, naming the offending variable, explicitly required for unrecognized values? [Completeness, Spec §FR-003, §SC-004]
- [x] CHK011 Is the variable name `CONTAINER_TMPFS_EXEC` used consistently across every requirement and scenario that references it? [Consistency]
- [x] CHK012 Is the "enabling value" / "truthy" phrasing pinned to a concrete value rather than left vague in the user stories? [Ambiguity, Spec §US2]

## Edge Cases & Interactions

- [x] CHK013 Is behavior defined when `CONTAINER_TMPFS_SIZE` is empty (setting still resolved & attested, but has no mount effect)? [Coverage, Spec §FR-007, §Edge Cases]
- [x] CHK014 Is the setting's independence from `CONTAINER_READ_ONLY_ROOTFS` specified? [Coverage, Spec §Edge Cases]
- [x] CHK015 Is it specified whether enabling exec with no tmpfs mounted should emit a signal or silently no-op (current text implies silent)? [Ambiguity, Spec §Edge Cases]

## Acceptance Criteria & Coverage

- [x] CHK016 Are success criteria objectively verifiable, including SC-002 referencing the exact prior failure (`Permission denied (os error 13)`) it must resolve? [Measurability, Spec §SC-002]
- [x] CHK017 Do acceptance scenarios cover both the disabled (`noexec`) and enabled (`exec`) paths end to end? [Coverage, Spec §US1, §US2]

## Assumptions & Documentation

- [x] CHK018 Is the trust assumption (operator-trusted workloads only) documented *and* required to surface as a security implication in `.env.example` / operator docs? [Assumption, Spec §FR-011, §Assumptions]

## Notes

- Check items off as completed: `[x]`
- These items test the **requirements**, not the implementation — each asks whether the spec says enough, clearly and consistently.
- Open candidates flagged above (CHK008, CHK009, CHK012, CHK015) are low-impact and can be deferred to `/speckit-plan` rather than blocking.
