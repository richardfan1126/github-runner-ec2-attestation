## 1. Pre-flight

- [ ] 1.1 Capture a pre-change baseline: trigger a `workflow_dispatch` (or note the latest
  `main` run) and save the artifact annotations, PCR4/PCR7, and `flavors.lock` for one
  image-level and one AMI-only flavor, to diff against after the refactor.
- [ ] 1.2 (Resolved) Branch protection on `main` requires neither `build-kiwi-ami-only` nor
  `build-and-publish` — confirmed, so removing the ami-only job needs no protection change.

## 2. detect_changes.py — single producer matrix

- [ ] 2.1 Emit a single `build_matrix` of `{flavor, mode}` entries, built from the
  already-disjoint `image_flavors` / `ami_only_flavors` lists (preserve the image-wins
  promotion at `detect_changes.py:141`), each tagged `mode: image | ami-only`. Keep the
  existing `include`-list shape (`{"include": [{"flavor", "mode"}, …]}`) so `mode` is a
  per-entry key, NOT a Cartesian matrix axis. Keep `ami_matrix`.
- [ ] 2.2 Replace `has_image_builds` / `has_ami_only_builds` outputs with a single
  `has_builds` flag (keep `has_ami_builds`).
- [ ] 2.3 Update the change-detection unit tests to assert the new matrix shape and flags;
  keep the classification-rule tests unchanged.

## 3. Workflow — unify the producer job

- [ ] 3.1 In `detect-changes`, wire the new `build_matrix` / `has_builds` outputs.
- [ ] 3.2 Rename `build-and-publish` to `build-flavor-image` and add a `mode`-branching
  "resolve container image digest" step: `image` → build container + derive amd64 manifest
  digest (today's path); `ami-only` → read `container_image_digest` from `flavors.lock`
  (fail if absent).
- [ ] 3.3 Point `build-flavor-image`'s matrix at `build_matrix` and its `if` at `has_builds`;
  confirm all shared steps (KIWI build, PCR, config attestation, ORAS push, provenance,
  build-context upload, summaries) run identically for both modes.
- [ ] 3.4 Delete the `build-kiwi-ami-only` job.

## 4. Workflow — simplify downstream conditions

- [ ] 4.1 Change `build-ami` to `needs: [detect-changes, build-flavor-image]`; remove
  `always()` and the upstream-result boolean, leaving only the `has_ami_builds` + ref/event
  gate.
- [ ] 4.2 Confirm `update-flavors-lock` (`needs: [detect-changes, build-ami]`) keeps its
  event gate and no longer needs `always()`; it should run on a successful `build-ami` and
  skip otherwise.

## 5. CI config reconciliation

- [ ] 5.1 No branch-protection change needed (neither producer job is a required status
  check — confirmed in task 1.2). Verify no other workflow or doc references
  `build-kiwi-ami-only` by name.

## 6. Validation

- [ ] 6.1 Dispatch a run covering one image-level and one AMI-only flavor; confirm both are
  produced by the single `build-flavor-image` job.
- [ ] 6.2 Diff the new run's annotations, PCR4/PCR7, and `flavors.lock` entries against the
  task 1.1 baseline — they must match.
- [ ] 6.3 Confirm `update-flavors-lock` runs and commits after a successful AMI build that
  rebuilt only AMI-only flavors (the case that was previously wrongly skipped).
- [ ] 6.4 Confirm a no-op/empty-matrix run cascade-skips the producer, `build-ami`, and
  `update-flavors-lock` cleanly, leaving `flavors.lock` untouched.
- [ ] 6.5 Run `openspec validate unify-image-build-producers --strict` and resolve any
  issues.
