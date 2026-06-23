## Why

The `build-attestable-image.yml` pipeline splits the per-flavor image build into two
sibling jobs — `build-and-publish` (image-level rebuild) and `build-kiwi-ami-only`
(AMI-only rebuild) — that are ~90% identical and are each conditionally skipped. Because
either sibling is normally skipped, `build-ami` must use `always()` plus a hand-written
boolean to re-implement the skip logic GitHub would otherwise provide. `always()` is
contagious: it does not "launder" a skipped sibling's status for jobs further downstream,
so `update-flavors-lock` is currently being **wrongly skipped even after `build-ami`
succeeds**, leaving `flavors.lock` un-updated after a real AMI registration. This violates
the existing requirement that the pipeline write `flavors.lock` back after a flavor's AMI
is registered.

## What Changes

- Collapse `build-and-publish` and `build-kiwi-ami-only` into a **single producer job**
  (renamed `build-flavor-image`, since in `ami-only` mode it skips only the container
  rebuild and still builds/attests/publishes per flavor) whose matrix carries a per-entry
  `mode`
  (`image` | `ami-only`) dimension. One conditional step branches on `matrix.mode` to
  either build the container and derive a fresh digest (`image`) or read the existing
  digest from `flavors.lock` (`ami-only`). All shared steps — KIWI build, PCR extraction,
  config attestation, ORAS push, provenance attestation, build-context upload — exist in
  exactly one place.
- `detect-changes` emits **one** producer matrix (entries tagged with `mode`) instead of
  separate `image_matrix` / `ami_only_matrix`, plus the `ami_matrix` it already emits.
- `build-ami` declares `needs: [detect-changes, build-flavor-image]` (one producer). With
  no conditionally-skipped sibling, it **drops `always()`** and its hand-written result
  boolean, keeping only its genuine gates (ref/event and "is there anything to build").
- `update-flavors-lock` no longer trips over a skipped grandparent: with the `always()`
  contagion removed upstream, its plain dependency on `build-ami` resolves correctly and
  it runs whenever an AMI was actually built.
- Remove the now-unused `build-kiwi-ami-only` job. Verify it is not a required status
  check in branch protection before removal (and update branch protection if it is).

The end state contains **no `always()`** anywhere in the workflow. A condition-only stopgap
was explicitly rejected: making `update-flavors-lock` explicit would require a *second*
`always()` to compensate for the first, spreading the very construct this change exists to
remove.

No change to the two-level invalidation *semantics* (global / image / ami-only), to PCR
measurement, to attestation, or to what `flavors.lock` records.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `execution-build-images`: the two per-flavor rebuild levels (image, ami-only) are driven
  by a **single producer job parametrized by rebuild level**, not by two conditionally-run
  sibling jobs; and `flavors.lock` write-back SHALL run after any successful AMI
  registration regardless of which rebuild level produced it (closing the `always()`-skip
  gap).
- `ami-build`: clarify that `build-ami` depends on the single producer job and that its
  trigger/skip behavior is expressed without `always()`-based result juggling, so a
  no-build run cascade-skips cleanly and a successful build flows to `flavors.lock`
  write-back.

## Impact

- **Workflow:** `.github/workflows/build-attestable-image.yml` — merge two jobs into one;
  simplify `build-ami` and `update-flavors-lock` conditions; remove `build-kiwi-ami-only`.
- **Script:** `.github/scripts/detect_changes.py` — emit a single `mode`-tagged producer
  matrix and corresponding `has_*` flag(s); adjust tests in `.github/scripts/` accordingly.
- **CI config:** branch-protection required status checks may reference the removed job
  name `build-kiwi-ami-only` and must be reconciled.
- **No impact** on runtime executor, attestation contents, PCR values, or the
  `flavors.lock` record schema.
