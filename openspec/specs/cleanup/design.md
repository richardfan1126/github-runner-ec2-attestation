# cleanup — Design Rationale

> Imported from `.kiro/specs/github-actions-remote-executor/design.md` (PART 4: Cleanup Design). Captures the *why* behind `spec.md`; not normative.

## Overview

`scripts/cleanup.py` removes all AWS resources created across build and deployment, in three phases: (1) Terraform infrastructure destruction, (2) AMI deregistration + snapshot deletion, (3) verification of complete removal. It is deliberately a **separate** script from build/deploy — it runs when the operator is finished and wants to tear everything down, and it requires interactive confirmation first.

## Key decisions & trade-offs

- **Idempotent, skip-don't-fail on absence.** Each phase tolerates already-gone resources: missing Terraform dir or `terraform.tfstate` → log a warning and skip; AMI `InvalidAMIID.NotFound` → skip. This lets cleanup be re-run safely after a partial teardown without spurious failures.
- **`--keep-ami` escape hatch.** Operators often want to destroy the running deployment (VPC, instance, security groups) while keeping the AMI/snapshot for re-deployment. `--keep-ami` skips phase 2 entirely and excludes the AMI/snapshot from the phase-3 verification, logging that they were intentionally preserved — so "preserved" is never mistaken for "leaked".
- **Verify propagation, don't assume.** After `DeregisterImage(... DeleteAssociatedSnapshots=True)` the script waits ~2s and re-queries `describe_images`/`describe_snapshots` (expecting `NotFound`) because AWS deregistration/deletion is eventually consistent. Terraform destruction is likewise verified by parsing `terraform.tfstate` and checking the `resources` array is empty.
- **Confirmation before destruction.** A `yes`/`y` prompt guards an irreversible operation; declining exits 0 (cancel), so an accidental invocation is harmless.
- **Honest exit codes + reporting.** Phase 3 enumerates leftover EC2 instances (tagged Purpose `AMI Build` / `Attestation Demo` in non-terminated states) and, unless `--keep-ami`, the specific AMI/snapshot; anything remaining is reported with type/ID/status and the operator is advised to delete it manually. The script returns 0 only when everything succeeded, 1 (logging that resources may remain) on any failure — so automation can trust the exit code. Dual-logged to stdout and `cleanup.log`.

## Data models (shapes)

Input: `ami_build_result.json` (`ami_id`, `snapshot_id`, `region`). Functions: `parse_arguments`, `destroy_infrastructure`, `deregister_ami(keep_ami=…)`, `verify_cleanup(keep_ami=…)`, `main` (returns the process exit code).
