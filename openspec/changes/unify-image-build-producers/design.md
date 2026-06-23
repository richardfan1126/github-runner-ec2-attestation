## Context

`build-attestable-image.yml` has five jobs: `detect-changes` → {`build-and-publish`,
`build-kiwi-ami-only`} → `build-ami` → `update-flavors-lock`. The two producer jobs are a
disjoint partition of the flavor set by rebuild level: `build-and-publish` rebuilds the
container and derives a fresh amd64 manifest digest; `build-kiwi-ami-only` reuses the digest
already recorded in `flavors.lock`. Apart from that one digest-source step, the two jobs are
~90% identical (KIWI build, PCR extraction, config attestation, ORAS install + push,
provenance attestation, build-context upload, summaries).

Because either producer is conditionally skipped on a normal push (a Dockerfile change skips
the ami-only job; an `env`-only change skips the image job), `build-ami` lists both in
`needs` and uses `always()` plus a long boolean to tolerate a skipped sibling while still
gating on ref/event and "is there anything to build". GitHub Actions then propagates the
skipped sibling's status transitively: `always()` rescues `build-ami` itself but not its
descendants, so `update-flavors-lock` (which relies on the implicit `success()` wrapper) is
skipped even when `build-ami` succeeds — leaving `flavors.lock` un-updated after a real AMI
registration.

The `ami-build` spec already specifies `needs: build-and-publish` (a *single* producer);
the dual-producer split is implementation drift, so this refactor re-aligns the workflow
with the existing spec intent — renaming the unified producer to `build-flavor-image`
(Decision 2) — rather than inventing new behavior.

## Goals / Non-Goals

**Goals:**
- One producer job parametrized by a per-entry `mode` (`image` | `ami-only`) field on the
  build matrix's `include` list (not a Cartesian matrix axis); shared steps written once.
- Remove `always()` and the hand-written result boolean from `build-ami`.
- `update-flavors-lock` runs reliably after any successful AMI build and skips when none was
  built.
- Preserve every existing behavior: two-level invalidation semantics, PCR measurement,
  attestation, build-context contract, and the `flavors.lock` record schema.

**Non-Goals:**
- Changing what triggers an image-level vs AMI-only rebuild (the `detect_changes.py`
  classification rules are unchanged; only the *shape* of the emitted matrix changes).
- Changing attestation, PCR derivation, or the verifier record.
- Touching the runtime executor, deployment, or any non-CI code.

## Decisions

### Decision 1: One producer job with a per-entry `mode` field (over a no-op join job)

Collapse both producers into a single job. `detect-changes` emits one matrix in the same
`include`-list shape it already uses — `{"include": [{"flavor": …, "mode": …}, …]}`,
consumed as `matrix: ${{ fromJSON(...) }}` — so `mode` is just an extra key per entry, never
a Cartesian axis. A single step does:

```yaml
- name: Resolve container image digest
  id: digest
  run: |
    if [ "${{ matrix.mode }}" = "image" ]; then
      # build container, push, derive amd64 manifest digest  (today's build-and-publish path)
    else
      # read container_image_digest from flavors.lock          (today's build-kiwi-ami-only path)
    fi
```

Everything downstream of that step is identical to today and exists once.

*Alternative considered — keep both producers, add a trivial `join` job* (`needs: [both],
if: always() && <normalize results>`) so `build-ami` needs only the join. Rejected: it
relocates the `always()` ugliness instead of removing it, and leaves the ~370 lines of
duplicated YAML (and the attestation-parity risk that comes with two copies) in place.

*Alternative considered — leave topology, only patch conditions* (the stopgap). Rejected
outright, not retained as a fallback: the minimal patch (`update-flavors-lock: if: always()
&& needs.build-ami.result == 'success' && …`) adds a *second* `always()` whose only job is to
compensate for the first. The goal is zero `always()` in the workflow, because `always()` is
the source of the confusing transitive skips; a fix that spreads more of it fails the goal
even though it makes the red X disappear.

### Decision 2: Rename the producer job to `build-flavor-image`

The unified job is renamed `build-flavor-image`. Neither old name (`build-and-publish` nor
`build-kiwi-ami-only`) is a required status check (see Open Questions), so nothing external
pins the name and the rename is free. `build-flavor-image` is accurate for *both* modes: the
job builds, attests, and publishes the per-flavor image either way — `ami-only` skips only
the *container* rebuild (reusing the digest recorded in `flavors.lock`), not the KIWI build
or the attestation/publish. The `ami-build` spec's `needs:` declaration is updated to the new
name as part of this change.

### Decision 3: `build-ami` and `update-flavors-lock` use ordinary `needs`, no `always()`

With a single producer, `build-ami: needs: [detect-changes, build-flavor-image]` and its
`if` reduces to the genuine gates only:

```yaml
if: needs.detect-changes.outputs.has_ami_builds == 'true' &&
    (github.ref == 'refs/heads/main' || github.event_name == 'workflow_dispatch')
```

`update-flavors-lock: needs: [detect-changes, build-ami]` then keeps its event gate and, no
longer sitting downstream of an `always()` job, resolves through the normal implicit
`success()` — it runs when `build-ami` succeeded and cascade-skips otherwise.

### Decision 4: `detect_changes.py` emits one `mode`-tagged producer matrix

Replace the separate `image_matrix` / `ami_only_matrix` outputs with a single
`build_matrix` of `{flavor, mode}` entries (the union, tagged), keeping `ami_matrix` and the
`has_ami_builds` flag. Collapse `has_image_builds` / `has_ami_only_builds` into a single
`has_builds` for the producer's `if`. Update the script's unit tests to assert the new
matrix shape; the classification logic itself is untouched.

Crucially, `build_matrix` is built from the existing `image_flavors` and `ami_only_flavors`
lists, which `compute_matrix` already guarantees disjoint (the
`ami_only_final = [f for f in ami_only if f not in image]` promotion at
`detect_changes.py:141`). Concatenating those two already-disjoint lists into one tagged
matrix therefore cannot produce a duplicate-flavor entry or build a flavor twice — the
single-matrix collapse is safe *because* that promotion exists, so it must be preserved
(build the matrix from the two lists, do not re-derive `mode` from raw paths).

## Risks / Trade-offs

- **Branch protection references `build-kiwi-ami-only`** → Confirmed not a required status
  check, so removing the job does not wedge merges and no protection update is required.
- **Attestation/PCR parity regression while merging two jobs** → The merge makes the shared
  steps single-sourced, which *reduces* drift risk, but the diff must be reviewed so the
  unified steps are byte-identical to today's `build-and-publish` (the authoritative copy).
  Validate by running both an image-level and an AMI-only flavor through a real dispatch and
  diffing the produced annotations/PCRs against a pre-change baseline.
- **Matrix `mode` plumbing bug** → an entry mislabeled `image` vs `ami-only` would build the
  wrong digest source. Mitigation: the existing guard in the ami-only path (fail if no
  `flavors.lock` entry) stays, and a fresh image build always overwrites the digest, so a
  mislabel fails loudly rather than silently shipping a stale digest.

## Migration Plan

1. Update `detect_changes.py` to emit the `mode`-tagged `build_matrix` + `has_builds`;
   update its tests.
2. Merge `build-kiwi-ami-only` and `build-and-publish` into the unified, renamed
   `build-flavor-image` job behind the `mode` branch; delete both old job names.
3. Simplify `build-ami` and `update-flavors-lock` conditions (drop `always()`).
4. Reconcile branch-protection required checks (remove `build-kiwi-ami-only`).
5. Validate with a `workflow_dispatch` covering one image-level and one AMI-only flavor;
   diff annotations/PCRs/`flavors.lock` against the pre-change baseline.

**Rollback:** revert the workflow + script commit; no persisted state changes (the
`flavors.lock` schema is unchanged), so rollback is a plain git revert.

## Open Questions

- ~~Is `build-kiwi-ami-only` (or `build-and-publish`) currently a *required* status check on
  `main`?~~ **Resolved: no.** Neither job is a required status check, so removing
  `build-kiwi-ami-only` does not wedge merges (migration step 4 / task 5.1 drops to a no-op
  verification), and — since nothing external pins either name — the unified job is freely
  renamed to `build-flavor-image` (Decision 2).
