## Context

The `build-ami` stage of `.github/workflows/build-attestable-image.yml` runs as a
per-flavor matrix (`default`, `gpu-presence`, `rust-build`; `max-parallel: 2`).
Each matrix leg invokes `scripts/build-ami.py`, which **provisions its own EC2
build instance** (plus VPC, IAM role/policy/instance-profile, security group) via
the `terraform/build-ami/` stack, builds one AMI on it, and tears it down in a
`finally` block.

Two structural problems follow from that shape:

1. **Account-global name collisions.** The Terraform resources use fixed names
   (`build-ami-instance-role`, `-policy`, `-profile`, `-sg`). Two concurrent legs
   collide with `EntityAlreadyExists` / duplicate-name errors.
2. **Repeated flavor-independent work.** Every leg re-provisions a full
   `c5.9xlarge` and **re-compiles `coldsnap` from source** (plus Rust, ORAS, gh
   CLI) — none of which depends on the flavor.

The chosen direction ("Option B") is to make the **GitHub Actions workflow own
the Terraform apply/destroy lifecycle** for a **single shared build instance**,
and to refactor `scripts/build-ami.py` so its unit of work is "build one AMI on
an already-provisioned instance." A thin driver installs the toolchain once and
loops over the selected flavors on that one instance.

Key facts about the current implementation that constrain the design:

- The build-ami Terraform **state is gitignored and local** — there is no remote
  backend. Destroy can only remove what apply wrote to *this runner's*
  filesystem.
- `execute_remote_command` (paramiko) loops on
  `while not stdout.channel.exit_status_ready(): … time.sleep(0.1)` with **no
  timeout and no channel-liveness check**. paramiko does not raise when the host
  dies mid-command, so a dead host spins the loop forever.
- The register/upload path enforces **exactly one `.raw`** in
  `~/artifacts/build-output`; a leftover from a prior flavor would violate it.
- `wait_for_snapshot` and `register_ami` run against the boto3 `ec2_client` on
  the **runner**, not over SSH — they survive the build instance's death once a
  snapshot ID has been captured.
- The build instance today has **no `user_data`**, no
  `instance_initiated_shutdown_behavior`, and only a fixed
  `Name = "build-ami-instance"` tag.

This design captures decisions reached during exploration; see `proposal.md` for
the full motivation and the out-of-scope GPU image-size bug note.

## Goals / Non-Goals

**Goals:**

- Build all flavor AMIs on **one** shared EC2 instance per run, compiling the
  toolchain (notably `coldsnap`) **once**.
- Move Terraform apply/destroy ownership from the script into the workflow job.
- Preserve today's **per-flavor failure isolation**: one flavor's failure is
  recorded and skipped; other flavors still produce their
  `ami_build_result-<flavor>.json`, which `update-flavors-lock` already tolerates.
- Make failure isolation *actually hold on a shared host* by hardening the SSH
  command path and reacting to the **kind** of failure (host-suspect vs
  flavor-local).
- Guarantee the shared instance is torn down — including on runner hard-death and
  the ~6 h ceiling — without relying solely on the workflow `always()` step.
- Keep the change confined to the **build pipeline**: no change to runtime
  attestation, `user_data` semantics, PCRs, published annotations, or
  `flavors.lock` format.

**Non-Goals:**

- **Not** fixing the GPU image-size `sed` no-op — that is a separate `image-build`
  defect, recorded in the proposal's Notes and explicitly out of scope here.
- **Not** introducing a remote/persisted Terraform backend. (This design instead
  *records* the same-job constraint that the local-state model imposes.)
- **Not** parallelizing `coldsnap upload` across flavors — uploads stay
  sequential to preserve wipe-and-reuse and avoid EBS `PutSnapshotBlock`
  contention.
- **Not** changing what an AMI *is* or how it is attested — no PCR/build-repro
  impact; the trust anchor is unchanged.
- **Not** bumping the 30 GB root volume — one flavor's footprint at a time fits.

## Decisions

### D1 — Workflow owns Terraform lifecycle; apply and destroy in the same job

The workflow gains a `terraform apply` step (provision once) and an `always()`
`terraform destroy` step (tear down once). `scripts/build-ami.py` stops running
Terraform in its `finally` block and instead receives the instance connection
details (host + SSH key) as inputs.

**Constraint (hard):** apply and destroy **must stay in the same job on the same
runner.** Because the state is gitignored and local, only the runner that ran
apply holds the state needed to destroy. Splitting provision and teardown into
separate jobs would silently break destroy unless a remote backend is introduced
first (a non-goal). Recorded so a future refactor does not regress teardown.

*Alternative considered:* remote backend (S3 + DynamoDB lock) to decouple jobs —
rejected as scope creep; the single-job layout already satisfies the requirement.

### D2 — Self-terminating build instance as the runner-death backstop

`always()` covers step failure and normal cancellation but **cannot** fire on
runner hard-death, the ~6 h ceiling force-kill, an exceeded cancellation grace
period, or the destroy step itself erroring. Add an unconditional, runner-
independent self-destruct to `terraform/build-ami/`:

- `user_data` running `shutdown -h +<TTL>` (TTL comfortably longer than a normal
  run),
- `instance_initiated_shutdown_behavior = "terminate"`,
- a **run-id tag** so any orphan is identifiable and sweepable out-of-band. Its
  value is `${github.run_id}-${github.run_attempt}` (see D9), sourced from the same
  Terraform `run_id` var; `run_id` is externally queryable via the GitHub API, so a
  sweeper can decide whether the owning run is still active — the foundation of the
  optional orphan sweep.

This is the transient **builder** instance, not the runtime AMI, so adding
`user_data` here has **no attestation/PCR impact**. The remaining orphan window
shrinks to "runner dies *and* TTL not yet elapsed," which self-heals at the TTL.

**The TTL is coupled to an explicit job `timeout-minutes` — they must be set as a
pair.** The TTL must exceed the worst-case *legitimate* run or it self-destructs a
good build. The build-ami job currently has **no `timeout-minutes`**, so it
inherits GitHub's 360-min (6 h) default — which would force a useless >6 h TTL. So
this change **adds** an explicit job timeout and sizes the TTL just above it,
preserving the invariant:

```
  worst-case run  <<  timeout-minutes  <  TTL

  normal run   ─────────────► finish → always() destroy          (TTL never fires)
  hung, alive  ─[timeout-minutes]─► killed → always() destroy     (TTL never fires)
  runner DIES  ─[········TTL········]─► self-destruct + terminate  (TTL fires — ONLY case)
```

**Concrete values.** Estimated worst-case run (c5.4xlarge, 3 flavors incl. the
8 GB gpu upload): provision ~3 + connect ~1 + install-once ~12 + Pass 1 `3×~18` +
Pass 2 ~10 ≈ **80 min**. So: job **`timeout-minutes: 120`** (~1.5× headroom,
well under 6 h) and TTL **`shutdown -h +150`** (2.5 h) = timeout + ~30 min for the
`always()` destroy to run. Orphan billing is then bounded to ~2.5 h and *only* on
true runner hard-death.

*Caveat — clock origin.* `shutdown -h +150` counts from **instance boot**
(user_data), which precedes the job's real work by the provision offset — that is
*why* the TTL sits 30 min above the timeout rather than hugging it: the gap absorbs
both the provision offset and the destroy margin. Confirm the ~80-min worst case on
one measured single-instance run (as in D11) and adjust the pair while holding
`TTL ≈ timeout-minutes + 30`.

*Alternative considered:* rely on `always()` alone — rejected; it structurally
misses the hard-death and ceiling cases, and this change *removes* the script's
`finally` destroy, so teardown attempts drop from two to one (see D3).

### D3 — Single `always()` destroy must retry and fail loud

Removing the script's `finally` destroy collapses teardown from **two** attempts
(script `finally` + workflow `always()`) to **one**. `terraform destroy` can exit
non-zero transiently (API throttling, dependency/in-use races, eventual
consistency). The one remaining attempt therefore:

- **retries with backoff** — safe because destroy is idempotent/convergent; a
  re-run only removes what still exists, and
- **fails loud** if it still cannot converge — surface a visible failure rather
  than green-washing a possible orphan.

D2's self-destruct is the safety net beneath even a fully-failed destroy.

### D4 — Wipe-and-reuse the shared artifacts directory per flavor

Reset the artifacts working directory (`~/artifacts`) at the **start of each
flavor iteration**, so every flavor builds from a known-empty tree. On a shared
instance this is required for:

- **Correctness** — the register/upload path enforces exactly one `.raw` in
  `build-output`; a prior flavor's leftover would trip "Expected exactly one
  .raw file."
- **Capacity** — only one flavor's OCI blob + unpacked `.raw` occupies the 30 GB
  root at a time, instead of all flavors accumulating.

Pass the artifacts base path as a **single named parameter** (default
`~/artifacts`) rather than re-hardcoding the constant, so a future move to
parallel per-flavor subdirectories is a small change, not a re-thread of every
call site.

*Alternative considered:* per-flavor subdirectories from the start — rejected as
premature; it forfeits the capacity benefit and adds cleanup complexity now,
while the parameterized base path keeps that door open cheaply.

### D5 — Two-pass (hybrid) execution: sequential uploads, batched waits

Rather than fully serializing each flavor end-to-end (which pays each ~10-minute
snapshot-completion wait back-to-back), split execution:

- **Pass 1 (sequential, host-dependent):** for each flavor — wipe → pull →
  verify → validate → `coldsnap upload`, capture the returned snapshot ID, then
  discard the `.raw` (the snapshot lives server-side once upload returns, freeing
  the disk for the next flavor).
- **Pass 2 (batched, host-independent):** wait for all captured snapshots to
  reach `completed`, then `register_ami` each.

This keeps uploads **sequential** — preserving wipe-and-reuse and avoiding EBS
`PutSnapshotBlock` contention — while **overlapping the passive completion
waits** that naive serialization would pay one after another.

Sequential uploads also **halve the peak EBS Direct API pressure.** coldsnap
v0.9.0 drives a **hardcoded 64** concurrent `PutSnapshotBlock` workers per upload
(`const SNAPSHOT_BLOCK_WORKERS = 64`; not CPU-derived, no CLI override). Peak
worker count is therefore `(uploads in flight) × 64`: the old matrix
(`max-parallel: 2`) could stack **2 × 64 = 128** against the account/region
throttle, whereas the sequential model holds **1 × 64 = 64**. The AWS SDK already
self-regulates throttling via retry-with-backoff (throttling manifests as slower
throughput, not failure, until retries exhaust), and 64 has been working under the
old c5.9xlarge, so this change introduces **no new throttling risk and is strictly
gentler**.

```
Naive full-serialize (per flavor, back to back):
  [build+upload]──[≈10m wait]──[register] │ [build+upload]──[≈10m wait]──[register] │ …

Hybrid two-pass:
  Pass 1:  [f1 build+upload]──[f2 build+upload]──[f3 build+upload]
  Pass 2:                                        [wait all]──[register f1,f2,f3]
                                                  ▲ waits overlap instead of stacking
```

Per-flavor isolation applies within each pass: a Pass-1 failure excludes that
flavor from Pass 2; a Pass-2 wait/register failure is recorded without aborting
the others.

*Alternative considered:* fully parallel uploads — rejected for EBS contention
and loss of wipe-and-reuse. Naive full serialization — rejected for stacking the
passive waits.

### D6 — Bound SSH wall-clock and detect transport death

Add a wall-clock **timeout** plus **transport-liveness** detection to
`execute_remote_command`, so a dead host raises an exception instead of spinning
`exit_status_ready()` forever. Under the old matrix an infinite hang only wedged
one leg (killed by the job timeout); on a shared instance a hang during flavor 1's
`coldsnap upload` means flavors 2 and 3 **never start** and the per-flavor
`try/except` never runs. This hardening is the **precondition** that makes
per-flavor isolation real on a shared host — and it doubles as protection against
the 6 h-ceiling orphan (D2).

**Liveness is the primary signal; wall-clock is a generous backstop.** The
legitimate commands here are *long*: the `coldsnap` compile runs ~5 min mostly
silent, and an 8 GB gpu `coldsnap upload` can run 10–15 min. A short flat
wall-clock would **false-abort slow-but-progressing** work, so the primary detector
must be **transport liveness / progress** (is the transport still up, is data /
are keepalive acks still flowing?), which distinguishes "slow but alive" from
"dead" in a way a flat timer cannot; the wall-clock is only a comfortably-generous
upper bound to catch a truly wedged channel. **Keepalive is a dependency, not a
substitute:** `verify_ssh_connectivity` already sets `transport.set_keepalive(30)`
(which carries the connection through the silent compile stretch and refreshes NAT
mappings), but paramiko does not *raise* out of the exec read-loop on keepalive
failure — surfacing that is exactly D6's job.

### D7 — Failure taxonomy drives the loop's reaction

The driver distinguishes two failure classes and reacts differently:

| Failure class | Signal | Reaction |
|---|---|---|
| **Application error** | non-zero exit, signature/validation failure, `RuntimeError` | host is healthy → record flavor as failed, **continue** to next |
| **Transport / timeout** | raised paramiko or timeout exception (from D6) | host is *suspect* → attempt bounded reconnect (D12); if it recovers, resume the loop; if not, **stop** further Pass 1 flavors (each would only time out), mark them skipped, **still run Pass 2** for already-captured snapshots, **still run `always()` destroy** |

The two are cleanly distinguishable at the call site: a raised paramiko/timeout
exception ⇒ host-suspect; a non-zero exit or validation `RuntimeError` ⇒
flavor-local.

**Generalization:** this transient-vs-deterministic signal is not loop-specific.
The same distinction drives **retry-vs-fail-fast** in the toolchain-install phase
(see D10) — a transient cause (download blip) is worth retrying, a deterministic
one (compile error, checksum/GPG failure) is not. D7 applies it to "continue vs
abort the loop"; D10 applies it to "retry vs fail fast the install."

### D8 — Two-pass split *is* the host-fault backstop

`wait_for_snapshot` and `register_ami` use the runner's boto3 `ec2_client`, not
SSH, so once a flavor's `coldsnap upload` returns a snapshot ID, that flavor can
be completed **even if the build instance dies**. `coldsnap upload` returning is
therefore the last host-dependent moment; the host-fault blast radius shrinks
with each completed upload (already-uploaded flavors are immune, only in-flight
and not-yet-started ones are lost). The ultimate backstop remains
`update-flavors-lock` carrying forward any missing per-flavor result — and a
skipped flavor has **no attestation/trust impact**.

### D9 — Run-scoped uniqueness on Terraform resource names

Add a run-scoped suffix to the build-instance resource names as defense-in-depth,
so any future concurrency (e.g. overlapping runs across refs) cannot collide on
account-global IAM names. This reuses the same identifier as D2's run-id tag —
one identifier serving both collision-avoidance and orphan-observability.

**Identifier: `${github.run_id}-${github.run_attempt}`.** `run_id` gives global
uniqueness and is externally queryable (D2's sweep); `run_attempt` is necessary
because re-runs **reuse** `run_id` — if attempt 1 orphaned its resources, a bare
`run_id` on the re-run would collide with the still-present orphan, whereas the
composite gives attempt 2 distinct names while attempt 1's orphan stays
identifiable by its own tag.

**Only the account-unique resources are renamed; the rest are only tagged.** The
true collision points are the **IAM role/policy/instance-profile names**, the
**SG `GroupName`**, and the **ssh `key_name`** — everything else (VPC, subnet,
IGW, route table, the instance) carries a fixed *Name tag*, and tags are not
unique constraints, so they need no rename (they get a run-id *tag* for D2's
observability, not a new name). IAM name constraints (`[\w+=,.@-]`, ≤128 chars)
comfortably fit `build-ami-instance-role-<run_id>-<attempt>`.

**Threading — one Terraform `run_id` string var**, passed **identically on apply
and destroy** (trivially consistent because D1 keeps both in the same job with the
same `github.*` context), interpolated into the account-unique names above and
into the run-id tag. The refactored `build-ami.py` never sees it: provisioning and
teardown moved to the workflow (D1), so `run_id` is a pure workflow→Terraform
concern and the script only receives host + SSH key.

**Replaces the existing `timestamp()` hack.** `ssh_key.tf` currently derives
`key_name` from `formatdate(..., timestamp())`, which is non-deterministic, not
externally knowable, and can produce spurious apply/destroy diffs. Sourcing the
suffix from the passed-in `run_id` var instead is deterministic, externally known,
and diff-stable — a small correctness win independent of collision avoidance.

### D10 — Toolchain install is a "gate" phase: hard-abort, retry-transient, fail-fast-deterministic

Consolidating the toolchain install (git/gcc, Rust, ORAS, gh, and the CPU-bound
`coldsnap` compile) from once-per-flavor to **once per run** moves it *before* the
flavor loop, where it becomes a **gate**: every flavor depends on it, so nothing
can partially succeed. Install failure therefore always means **hard-abort the
whole run (zero results)** — it is *not* an isolatable unit, and the D7 loop
taxonomy (continue/abort) does not apply to it. State this explicitly so nobody
later tries to "isolate install failures per-flavor," which is meaningless.

Consolidation does not change the *expected* flavors-lost-to-install (it is `N·p`
under both the old matrix and the new single install), but it **concentrates the
loss into an all-or-nothing outcome** — one transient blip now wipes the whole run
instead of costing one flavor. Split by failure kind:

- **Deterministic** (bad version pin, GPG rotation, checksum mismatch, upstream
  404, source that won't compile): already perfectly correlated across legs under
  the old matrix — all N failed identically — so new vs old is **equal**, and new
  is *better* because it fails **once, fast**. Fail-fast here is desirable; do not
  retry (a re-run only burns the expensive `coldsnap` compile again for nothing).
- **Transient** (curl timeout, git-clone rate-limit, dnf mirror hiccup): the only
  subset where the matrix's independence bought anything. **Retry with backoff** —
  the same shape as D3's single-attempt-must-be-robust reasoning, applied to the
  install side. Scope retries to transient causes and prefer **per-step**
  granularity (the install functions are already separate), so a late cheap-step
  blip does not trigger a full `coldsnap` recompile.

Partial-success is already guarded (each install verifies: `coldsnap --help`,
`oras version`, `gh version`), so a silently-broken tool is not a new hole. A
minor side benefit: all flavors now upload via **one** `coldsnap` binary instead
of N separate compiles of the same pinned tag — tidier, though not a trust matter
(no PCR/reproducibility concern).

*Alternative considered / future option:* **pre-bake the toolchain into the
builder AMI** (or a prebuilt snapshot) so there is no run-time compile at all —
this erases the entire install phase and its failure domain. Rejected for *this*
change as too large a move; recorded as the real long-term answer to "install is
a single point of failure." Retry + fail-fast-taxonomy is the proportionate fix
now.

### D11 — Right-size the build instance to c5.4xlarge

The `c5.9xlarge` (36 vCPU, 72 GB, 10 Gbps) was chosen because **every matrix leg
re-compiled `coldsnap`** and we wanted that CPU-bound step fast — paid `N` times.
This change compiles the toolchain **once** (D10), so the only remaining
CPU-bound work is a single compile that gates the flavor loop. Every other phase
is instance-insensitive:

| Phase | Bottleneck | Wants big instance? |
|---|---|---|
| compile `coldsnap` (×1) | CPU (rustc/LLVM) | yes — but now once, not `N` |
| oras pull | network / disk I/O | marginally |
| `coldsnap upload` | EBS Direct API throttle | **no** — fixed 64 workers, API-bound |
| snapshot wait | idle | no |
| register | EC2 API | no |

Crucially, `coldsnap upload` throughput is **instance-independent by
construction**: v0.9.0 hardcodes 64 `PutSnapshotBlock` workers (D5) and is bounded
by the account/region EBS Direct API throttle, not by cores or NIC. So downsizing
does **not** slow uploads.

**Decision: `c5.4xlarge` (16 vCPU, 32 GB, ~5 Gbps / 4.75 Gbps EBS baseline).**

- **Not `c5.9xlarge`** — its 36 vCPU only ever served the per-leg compile, now
  paid once; during the sequential uploads + passive waits (the bulk of the run)
  it is idle ballast at ~2× the cost with no upload benefit.
- **Not `c5.2xlarge`** — defensible on cost and the upload wouldn't care, but the
  single compile is now a **gate blocking all flavors** (D10) so cores should not
  be minimized aggressively, and its ~2.5 Gbps network baseline is the first tier
  where a 64-worker upload could brush the network floor.
- **`c5.4xlarge`** keeps the compile gate fast, sits comfortably above any single
  64-worker upload's network/EBS demand, and is ~half the `c5.9xlarge` cost — the
  lowest-regression-risk pick.

*Empirical caveat:* confirm the compile time and that the network baseline is not
the bottleneck for one 64-worker upload on a single measured run; `c5.2xlarge`
remains a fallback if the measurement shows the compile gate stays fast enough.

*EBS-pressure lever, if ever needed:* coldsnap's 64-worker concurrency is a
hardcoded constant with **no CLI flag** in v0.9.0, so tuning EBS API pressure is a
**coldsnap-version** decision, not a runtime flag — out of scope here.

### D12 — Reconnect-and-resume on transport failure

The single `SSHClient` is created once and threaded through every
`execute_remote_command`; under the matrix it lived for one flavor, but on the
shared instance it must survive install + all N flavors (~30–50 min of one
continuous TCP connection). That makes the connection its **own** failure domain,
distinct from host death: a transient TCP drop on a *healthy* host (NAT rebalance,
ISP blip, packet-loss burst) would otherwise sink every remaining flavor. D6
correctly turns such a drop into a raised exception, but D7's plain transport-abort
would then discard a still-good instance over a recoverable blip.

So on a transport error the driver first attempts a **bounded SSH reconnect**
(reusing `verify_ssh_connectivity`'s existing retry loop). The reconnect attempt is
itself the **host-alive vs host-dead discriminator**: reconnect succeeds ⇒ host was
alive ⇒ resume the loop; reconnect fails after M attempts ⇒ host genuinely dead ⇒
fall through to D7's abort (still run Pass 2 + `always()` destroy).

**Resume granularity is the flavor boundary, and it is clean by construction.** An
*in-flight* upload is not salvaged: with `get_pty=False` the remote process's fate
on a dropped channel is indeterminate and its snapshot ID was never captured, so
that flavor is recorded failed/indeterminate and the driver resumes at the **next**
flavor. This is safe *for free* because **D4 wipe-and-reuse** erases the dropped
flavor's partial `~/artifacts`, and **D5/D8 two-pass** excludes it automatically
(no snapshot ID ⇒ not in Pass 2). The decisions already made are exactly what make
mid-run reconnect tractable.

*Not required for correctness — an availability upgrade.* `update-flavors-lock`
already carries forward any lost flavor (worst case without D12: this run updates
nothing, prior AMIs persist), so D12 is a resilience improvement in the same
"don't let a shared domain sink the run" spirit as the rest of the change, not a
correctness precondition. It is cheap precisely because C2's resume-at-next falls
out of D4/D5 at no extra cost.

## Risks / Trade-offs

- **Single shared instance = single shared failure domain.** → Mitigated by D6
  (SSH timeout/liveness), D7 (failure taxonomy), and D8 (host-independent Pass 2).
  Without D6 specifically, one host fault silently takes down the whole run, so
  it is a required part of this change, not an optional hardening.

- **Teardown attempts drop from two to one.** → Mitigated by D3 (retry + fail
  loud) and D2 (self-terminating instance beneath even a failed destroy).

- **The single long-lived SSH connection is its own failure domain.** A transient
  TCP drop on a healthy host would sink every remaining flavor, and a flat
  wall-clock could false-abort a legitimately long upload/compile. → Mitigated by
  D6 (liveness as primary signal, generous wall-clock backstop, keepalive already
  present) and D12 (bounded reconnect-and-resume at the flavor boundary, clean via
  D4/D5). Residual worst case is still safe: lost flavors carry forward via
  `update-flavors-lock`.

- **Toolchain install becomes a run-wide single point of failure.** Consolidating
  N installs into one keeps expected loss at `N·p` but makes it all-or-nothing —
  a transient blip wipes the whole run. → Mitigated by D10 (retry transient
  install failures with backoff, per-step; fail fast on deterministic ones).
  Deterministic failures were already run-wide under the matrix, so no regression
  there. Long-term eliminator: pre-baked builder AMI (D10, out of scope here).

- **Same-job apply/destroy constraint is implicit in the local-state model.** →
  Recorded explicitly (D1) so a future job-splitting refactor does not silently
  break teardown; the guardrail is "introduce a remote backend first."

- **Sequential uploads are slower than fully-parallel in raw wall-clock.** →
  Accepted trade-off (D5): parallel uploads reintroduce EBS `PutSnapshotBlock`
  contention and forfeit wipe-and-reuse; the two-pass split recovers most of the
  time by overlapping the passive waits instead.

- **TTL tuning for the self-destruct.** A TTL too short kills legitimate long
  runs; too long widens the orphan window. → Set comfortably above a normal
  run's duration; the run-id tag makes any residual orphan identifiable for
  out-of-band sweeps.

- **Wipe-and-reuse discards the `.raw` before Pass 2.** Safe only because the
  snapshot is server-side once `coldsnap upload` returns. → The design ties the
  discard to a captured snapshot ID; if upload has not returned a snapshot ID,
  the flavor is a Pass-1 failure and is never entered into Pass 2.

## Migration Plan

This is a build-pipeline change with no runtime/deployed-artifact surface, so
"migration" is limited to the CI pipeline:

1. Land the Terraform changes (run-scoped names, `user_data` TTL,
   `instance_initiated_shutdown_behavior`, run-id tag) — additive to the
   `build-ami` stack.
2. Refactor `scripts/build-ami.py` to accept instance connection details and
   expose the two-pass multi-flavor driver; harden `execute_remote_command`.
3. Restructure the `build-ami` job: remove the matrix; add provision → build-loop
   → `always()` destroy steps in one job.
4. **Rollback:** revert the workflow + script + Terraform commits together. There
   is no persisted state or deployed artifact to unwind — the next run simply uses
   the previous shape. The first run after landing should be watched to confirm
   the shared instance is torn down (destroy step green *and* no tagged orphan
   remains).

## Open Questions

- **Self-destruct TTL value** — *resolved* (see D2): job `timeout-minutes: 120`
  paired with TTL `shutdown -h +150`, holding `TTL ≈ timeout-minutes + 30`.
  Confirm the ~80-min worst-case estimate on one measured single-instance run and
  adjust the pair if needed.
- **Out-of-band orphan sweep** — the run-id tag *enables* a scheduled sweep of
  stale `build-ami` instances, but whether to add one is out of scope here; noted
  as a possible follow-up.
