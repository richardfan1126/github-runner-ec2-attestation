## MODIFIED Requirements

### Requirement: build-ami job dependency and trigger

The `build-ami` job SHALL declare `needs: [detect-changes, build-flavor-image]`, where
`build-flavor-image` is the **single** per-flavor producer job (parametrized by rebuild
level), so it runs only after that producer completes. Because there is no second,
conditionally-skipped sibling producer, the job SHALL express its trigger and skip behavior
**without** `always()` and without a hand-written upstream-result boolean: ordinary `needs`
resolution combined with the job's own ref/event gate SHALL suffice. The job SHALL run on
pushes to `main`, on `workflow_dispatch` with `enable_ssh: false`, and on
`workflow_dispatch` with `enable_ssh: true` (Debug_Build), but SHALL be skipped on pushes
to `develop` and SHALL be skipped when the producer produced nothing to build.

#### Scenario: Runs after the producer on applicable triggers

- **WHEN** the workflow is triggered by a push to `main`, or by `workflow_dispatch` with
  either value of `enable_ssh`, and the producer job built at least one flavor
- **THEN** the `build-ami` job executes after the single `build-flavor-image` producer
  completes successfully, via ordinary `needs` resolution (no `always()`)

#### Scenario: Skipped on develop

- **WHEN** the workflow is triggered by a push to `develop`
- **THEN** the `build-ami` job is skipped

#### Scenario: Cascade-skips cleanly when nothing was built

- **WHEN** a run produces an empty rebuild matrix (no flavors to build)
- **THEN** the producer job and `build-ami` skip cleanly through ordinary `needs`
  propagation, with no `always()` gate needed to suppress a spurious run
