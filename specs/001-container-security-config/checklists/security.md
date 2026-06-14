# Security Requirements Quality Checklist: Container Security Configuration

**Purpose**: Lightweight pre-plan sanity check on the *quality* of the requirements (completeness, clarity, consistency, measurability) across security posture, config validation, attestation/audit, and backward compatibility. Tests how the spec is written — not whether code works.
**Created**: 2026-06-13
**Feature**: [spec.md](../spec.md)

## Security Posture

- [x] CHK001 Is the default capability set that "preserves today's working behavior" enumerated explicitly in a requirement, rather than referenced abstractly? [Clarity, Spec §FR-005] — Resolved: FR-005 lists the 7 caps (CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, NET_BIND_SERVICE, KILL).
- [x] CHK002 Is the relationship between the `CONTAINER_CAP_ADD` allow-list and the default set specified (is the allow-list a defined superset, or only stated as an assumption)? [Consistency, Spec §FR-015 / Assumptions] — Decided: allow-list = Docker default-bounding 14-cap set, a defined superset of the 7-cap default (FR-015 + Assumptions).
- [x] CHK003 Are the security implications of `CONTAINER_NETWORK_MODE=host` (weakest isolation) captured as a requirement, or only noted as an edge case? [Coverage, Spec §Edge Cases] — Resolved: `host` is an allowed enum value (FR-010) whose effective value is observably surfaced (FR-026/FR-027), so the weakest-isolation choice is never silent; rationale documented per FR-024/FR-025.

## Config Validation

- [x] CHK004 Is the accepted `CONTAINER_TMPFS_SIZE` format defined concretely (permitted units and any min/max bounds) rather than as "a recognized size unit"? [Clarity, Spec §FR-016] — Resolved: FR-016 pins positive integer + `b`/`k`/`m`/`g`, rejects 0/negative/no-unit/whitespace.
- [x] CHK005 Are `CONTAINER_USER` value constraints fully specified beyond "non-negative integers" (e.g., upper bound, whether a bare uid without gid is accepted)? [Completeness, Spec §FR-014] — Decided: both `uid:gid` parts required; bare uid rejected (FR-014).
- [x] CHK006 Is the unset-vs-empty distinction for `CONTAINER_CAP_ADD` (preserve default vs grant nothing) stated as a testable requirement, not only an edge case? [Consistency, Spec §FR-005 / §Edge Cases] — Resolved: promoted into FR-005 as a testable requirement tied to the surfaced effective value.
- [x] CHK007 Is "fail fast at startup, before serving any request" defined as an observable, testable condition (what state proves the server refused to start)? [Measurability, Spec §FR-011] — Resolved: FR-011 now defines it as non-zero exit during config load, listen port never bound.

## Attestation / Audit

- [x] CHK008 Is the set of values that must appear in the attestation/audit surface enumerated, or left implicit in "security-relevant settings"? [Ambiguity, Spec §FR-026, §FR-027] — Resolved: FR-027 now enumerates all eight effective values.
- [x] CHK009 Is "surfaced for attestation/audit" specified concretely (which surface, which fields) rather than deferred to "the existing mechanism"? [Clarity, Spec §FR-027 / Assumptions] — Resolved: FR-027 names the surface (attestation `user_data` via the attestation generator, mirroring `gpu_enabled`).
- [x] CHK010 Can "a relaxed default is always distinguishable from the secure default" (SC-004) be objectively verified from the surfaced data? [Measurability, Spec §SC-004] — Resolved: with all eight effective values bound to `user_data` (FR-027), a relying party compares surfaced value to the documented default per setting.

## Backward Compatibility

- [x] CHK011 Is each breaking behavior change (non-root user, read-only rootfs, read-only workspace, network `none`) tied to a documented, explicit opt-out requirement? [Completeness, Spec §FR-025, §FR-009, §FR-010] — Resolved: FR-029 pairs each breaking default with its opt-out (and notes workspace `ro` is not a change).
- [x] CHK012 Are operator-facing migration expectations for jobs that break under the new defaults captured as a requirement, or only as a trade-off note? [Gap, Spec §FR-025 / Assumptions] — Resolved: FR-029 requires per-default symptom + remediation in the docs.

## Notes

- Check items off as the spec is updated to resolve each: `[x]`
- Items reference spec sections `[Spec §X]` or use `[Gap]` / `[Ambiguity]` markers where the requirement is missing or underspecified.
- Unresolved items are candidates for `/speckit-clarify` before `/speckit-plan`. CHK001/CHK002 (capability allow-list scope) overlap the open question already flagged on the spec.
