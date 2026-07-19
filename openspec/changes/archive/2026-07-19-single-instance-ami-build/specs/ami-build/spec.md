## MODIFIED Requirements

### Requirement: Per-flavor AMI build matrix

The `build-ami` stage SHALL build every flavor selected by the dynamic matrix on a **single shared Build_Instance** per run, iterating the flavors in a sequential loop, and producing one distinct attestable AMI per flavor that carries that flavor's baked OCI layout and effective sandbox config. A single AMI SHALL NOT be shared across flavors, and a single Build_Instance SHALL NOT be provisioned per flavor. The stage SHALL NOT run as a per-flavor job matrix (no `max-parallel`); instead a single `build-ami` job owns one instance and drives all flavors on it. The stage SHALL preserve per-flavor **failure isolation** — one flavor's failure is recorded and skipped while other flavors still emit their per-flavor result — and SHALL apply the existing `develop`-skip rule (build/publish images on `develop`, register AMIs only on `main`).

#### Scenario: One AMI per selected flavor on one shared instance

- **WHEN** the matrix selects two flavors for full rebuild
- **THEN** the stage provisions exactly one shared Build_Instance, builds both flavors on it in sequence, and registers two distinct AMIs, each carrying its own flavor's baked image and effective config, with distinct PCR4 values

#### Scenario: Per-flavor failure isolation preserved

- **WHEN** one selected flavor's build fails while the host remains healthy
- **THEN** that flavor is recorded as failed and skipped, the loop continues, and every other selected flavor still produces its `ami_build_result-<flavor>.json`, matching the isolation the removed matrix provided

#### Scenario: develop skips AMI registration

- **WHEN** the pipeline runs on `develop`
- **THEN** changed-flavor images are built and published but no AMIs are registered

### Requirement: Build instance provisioning

The `build-ami` **workflow job** — not AMI_Build_Script — SHALL provision a single shared temporary Build_Instance via `terraform apply` in the specified region before invoking the script, isolated in its own VPC with SSH restricted to the operator's IP, using Amazon Linux 2023 with IMDSv2 required and an IAM instance profile scoped to EC2/EBS snapshot operations. AMI_Build_Script SHALL receive the instance connection details (host and SSH private key path) and the run identifier (`${github.run_id}-${github.run_attempt}`, used solely as an EBS-snapshot tag value) as inputs, and SHALL NOT run Terraform or perform any resource naming itself.

#### Scenario: Isolated build instance provisioned by the workflow

- **WHEN** the `build-ami` job provisions the Build_Instance
- **THEN** a `terraform apply` step (owned by the workflow, on the same runner that will later destroy it) creates a VPC (10.2.0.0/16) with a public subnet (10.2.1.0/24), Internet Gateway and route table, a security group allowing SSH only from the operator IP `/32`, a 4096-bit RSA key (saved to a 600-permission temp file), and an AL2023 instance with IMDSv2 required, then waits for running + status checks
- **AND** AMI_Build_Script is invoked against that already-provisioned instance with its host and SSH key path, and does not itself call `terraform apply` or `terraform destroy`

#### Scenario: Artifact reference validated and digest-pinned

- **WHEN** AMI_Build_Script receives a flavor's `artifact_ref` argument
- **THEN** it validates the reference against a strict allowlist (rejecting shell metacharacters), requires an `@sha256:` digest component (terminating otherwise), and uses only the digest — ignoring any tag — for both verification and pull

### Requirement: Build tool installation

The AMI_Converter SHALL install the tools needed for verification and AMI creation on the shared Build_Instance (git, gcc, a signature-verified Rust toolchain, ORAS, GitHub CLI, and coldsnap built from a pinned source) **once per run, before the flavor loop**, verifying each installation and streaming output to logs. Because every flavor depends on this install, it is a **gate**: any install failure SHALL hard-abort the whole run (no per-flavor isolation applies to install). The install SHALL react to the failure kind — **transient** causes (download timeout, clone rate-limit, mirror hiccup) SHALL be retried with backoff at per-step granularity so a late cheap-step blip does not trigger a full coldsnap recompile, while **deterministic** causes (bad version pin, GPG/checksum mismatch, upstream 404, source that will not compile) SHALL fail fast without retry.

#### Scenario: Tools installed once and verified

- **WHEN** the AMI_Converter installs build tools at the start of the run
- **THEN** it installs git/gcc via dnf, installs Rust from the official standalone tarball after GPG-verifying its detached signature (key 85AB96E6FA1BE5FE), installs ORAS (checksum-verified) to `/usr/local/bin`, installs GitHub CLI, builds coldsnap via `cargo install --locked` from a pinned tag, and verifies `oras version`, `gh version`, and `coldsnap --help` — a single time, shared by all subsequent flavors

#### Scenario: Transient install failure retried, deterministic fails fast

- **WHEN** a tool install step fails
- **THEN** a transient failure (e.g. curl/network timeout, clone rate-limit) is retried with backoff at per-step granularity, and a deterministic failure (integrity mismatch, compile error, upstream 404) fails fast without retry

#### Scenario: Install failure hard-aborts the whole run

- **WHEN** the toolchain install cannot succeed after its retry policy
- **THEN** the AMI_Converter fails the entire run with an integrity/installation error before entering the flavor loop, producing zero AMIs (the install is a gate, not an isolatable per-flavor unit)

### Requirement: Build result output and infrastructure cleanup

On successful registration the AMI_Converter SHALL write a per-flavor result JSON (`ami_id`, `snapshot_id`, `region`, `build_timestamp`, `pcr_measurements`) for each flavor. In its `finally` block the AMI_Converter SHALL close the SSH connection and securely delete the temporary SSH key, and SHALL NOT run `terraform destroy` — teardown of the shared Build_Instance is owned by the workflow's `always()` destroy step (see the workflow-owned lifecycle requirement), not by the script.

#### Scenario: Per-flavor result written

- **WHEN** a flavor's AMI registration succeeds
- **THEN** the AMI_Converter writes that flavor's result (including PCR4/PCR7 and an ISO 8601 `build_timestamp`) to its `--output-file` as 2-space-indented JSON

#### Scenario: Script finally closes SSH and deletes key only

- **WHEN** the conversion finishes or fails
- **THEN** the `finally` block closes SSH, overwrites the temp SSH key with random bytes before unlinking, and logs (without failing) any cleanup error, but does **not** invoke `terraform destroy` — infrastructure teardown is performed by the workflow job

## ADDED Requirements

### Requirement: Workflow-owned build instance lifecycle

The `build-ami` job SHALL own the Terraform lifecycle of the shared Build_Instance: a `terraform apply` step provisions it once and an `always()` `terraform destroy` step tears it down once, both in the **same job on the same runner** (the build-ami Terraform state is gitignored and local, so only the runner that ran apply can destroy). The destroy step SHALL retry with backoff (safe because destroy is idempotent/convergent) and, if it still cannot converge, SHALL fail loud rather than green-wash a possible orphan. The job SHALL declare an explicit `timeout-minutes` (e.g. `120`) rather than inheriting GitHub's 6 h default, and SHALL size the shared Build_Instance for a once-per-run compile (e.g. `c5.4xlarge`) rather than the former per-leg `c5.9xlarge`.

#### Scenario: Apply and destroy in one job on one runner

- **WHEN** the `build-ami` job runs
- **THEN** a `terraform apply` step and an `always()` `terraform destroy` step execute within the same job on the same runner, using the same variables, so teardown always has the local state that apply wrote

#### Scenario: Destroy retries and fails loud

- **WHEN** `terraform destroy` exits non-zero from a transient cause (API throttling, dependency/in-use race, eventual consistency)
- **THEN** the step retries with backoff, and if it still cannot converge it fails the job visibly rather than reporting success over a possible orphan

#### Scenario: Explicit job timeout and right-sized instance

- **WHEN** the `build-ami` job is configured
- **THEN** it sets an explicit `timeout-minutes` well under the 6 h default and provisions the shared instance at the right-sized type (e.g. `c5.4xlarge`), since the CPU-bound coldsnap compile now happens once per run rather than once per flavor

### Requirement: Self-terminating build instance

The shared Build_Instance SHALL carry a runner-independent self-destruct so it cannot bill indefinitely if the runner hard-dies, hits the ~6 h ceiling, or the `always()` destroy step itself fails. The Terraform build-instance definition SHALL set `user_data` that runs `shutdown -h +<TTL>`, set `instance_initiated_shutdown_behavior = "terminate"`, and attach a run-id tag so any orphan is identifiable and sweepable out-of-band. The TTL SHALL be coupled to the job `timeout-minutes` such that `worst-case run << timeout-minutes < TTL`, so the TTL fires only on true runner hard-death and never on a legitimate long run. This is the transient builder instance, not the runtime AMI, so the added `user_data` SHALL have no attestation/PCR impact.

#### Scenario: Instance self-terminates on runner death

- **WHEN** the runner hard-dies (or exceeds the ceiling) and the `always()` destroy never runs
- **THEN** the instance reaches its `shutdown -h +<TTL>` and, because `instance_initiated_shutdown_behavior = "terminate"`, terminates itself, bounding orphan billing to roughly the TTL

#### Scenario: TTL sits above the job timeout

- **WHEN** the self-destruct TTL and job `timeout-minutes` are configured
- **THEN** they are set as a pair holding `worst-case run << timeout-minutes < TTL` (e.g. `timeout-minutes: 120` with `shutdown -h +150`), so a hung-but-alive run is killed by the job timeout and its `always()` destroy runs before the TTL, while the TTL only ever fires on genuine runner hard-death

#### Scenario: Orphan is identifiable by run-id tag

- **WHEN** a build instance is provisioned
- **THEN** it is tagged with the run identifier so any residual orphan can be matched to its owning run (whose active/inactive state is queryable via the GitHub API) for an out-of-band sweep

### Requirement: Run-scoped build instance resource naming

The Terraform build-instance stack SHALL derive a single `run_id` variable, valued `${github.run_id}-${github.run_attempt}`, passed **identically** on `apply` and `destroy`, and SHALL use it to run-scope the account-unique resource names — the IAM role/policy/instance-profile names, the security-group `GroupName`, and the SSH `key_name` — so no two runs (including re-runs and any future overlapping runs) collide on account-global names. `run_attempt` is required because re-runs reuse `run_id`. This SHALL replace the existing non-deterministic `timestamp()`-derived `key_name` suffix with the deterministic, externally-known `run_id`. Resources whose uniqueness is not enforced by AWS (VPC, subnet, IGW, route table, the instance) SHALL only be tagged with the run id, not renamed.

#### Scenario: Account-unique names are run-scoped

- **WHEN** the build-instance stack is applied
- **THEN** the IAM role/policy/instance-profile names, the security-group `GroupName`, and the SSH `key_name` each incorporate `${github.run_id}-${github.run_attempt}`, so concurrent or re-run applies do not collide on `EntityAlreadyExists`/duplicate-name errors

#### Scenario: Deterministic suffix replaces timestamp()

- **WHEN** the SSH key resource is created
- **THEN** its `key_name` suffix comes from the passed-in `run_id` variable, not `formatdate(..., timestamp())`, yielding a deterministic, diff-stable, externally-knowable name

#### Scenario: Re-run gets distinct names from a prior orphan

- **WHEN** attempt 1 orphaned its resources and attempt 2 re-runs with the same `run_id`
- **THEN** the `run_attempt` component gives attempt 2 distinct names while attempt 1's orphan remains identifiable by its own tag

### Requirement: Two-pass multi-flavor build driver with wipe-and-reuse

AMI_Build_Script SHALL drive all selected flavors on the shared instance as a two-pass model over a reused artifacts directory. Before each flavor's pull the driver SHALL reset the shared artifacts working directory (wipe-and-reuse) so every flavor builds from a known-empty tree, and the artifacts base path SHALL be a single named parameter (default `~/artifacts`) rather than a re-hardcoded constant. In **Pass 1** the driver SHALL, sequentially per flavor, wipe → pull → verify → validate → `coldsnap upload`, capture the returned snapshot ID, then discard the `.raw` (the snapshot lives server-side once upload returns). Each `coldsnap upload` SHALL be invoked with `--tag run_id=<run_id>` so every snapshot is tagged with the run identifier at creation (coldsnap applies the tag on `StartSnapshot`, so even a snapshot abandoned mid-upload is tagged at birth), giving orphan snapshots the same run-scoped sweep key as orphan instances. In **Pass 2** the driver SHALL wait for all captured snapshots to reach `completed` and register each AMI. A Pass-1 failure SHALL exclude that flavor from Pass 2; a Pass-2 wait/register failure SHALL be recorded without aborting the other flavors. Because `wait_for_snapshot` and `register_ami` run against the runner's boto3 client rather than over SSH, a flavor whose upload returned a snapshot ID SHALL still be completed even if the build instance later dies.

#### Scenario: Wipe-and-reuse before each flavor

- **WHEN** the driver begins a flavor's build
- **THEN** it resets the shared artifacts directory first, so only one flavor's OCI blob + unpacked `.raw` occupies the root volume at a time and the "exactly one `.raw`" invariant in `build-output` is never violated by a prior flavor's leftover

#### Scenario: Pass 1 uploads sequentially and captures snapshot IDs

- **WHEN** Pass 1 runs
- **THEN** each flavor is processed one at a time (wipe → pull → verify → validate → `coldsnap upload`), the returned snapshot ID is captured, and the `.raw` is discarded before the next flavor, keeping uploads sequential and holding peak EBS `PutSnapshotBlock` concurrency to one flavor's workers

#### Scenario: Pass 2 waits and registers, isolated per flavor

- **WHEN** Pass 2 runs over the captured snapshot IDs
- **THEN** it waits for all snapshots to complete and registers each AMI, excluding any flavor that failed Pass 1, and a wait/register failure for one flavor does not abort the others

#### Scenario: Host death after upload does not lose the flavor

- **WHEN** the build instance dies after a flavor's `coldsnap upload` returned a snapshot ID
- **THEN** that flavor is still waited on and registered via the runner's boto3 client, and only in-flight and not-yet-started flavors are lost

#### Scenario: Snapshots are tagged with the run id for orphan attribution

- **WHEN** a flavor's `coldsnap upload` runs
- **THEN** it is invoked with `--tag run_id=<run_id>` so the resulting snapshot — including one abandoned mid-upload by a transport drop — carries the run identifier from creation, letting any orphan snapshot be matched to its owning run under the same run-scoped key as orphan instances (the automated reaper that consumes the tag is a separate, out-of-scope follow-up)

### Requirement: SSH command timeout and transport-liveness detection

`execute_remote_command` SHALL bound each remote command with transport-liveness/progress detection as the **primary** signal plus a generous wall-clock **backstop**, so a dead host raises an exception instead of spinning `exit_status_ready()` forever. The liveness check SHALL distinguish "slow but progressing" from "dead" so it does not false-abort legitimately long work (the ~5 min mostly-silent coldsnap compile, a 10–15 min 8 GB `coldsnap upload`); the wall-clock is only a comfortably-generous upper bound on a truly wedged channel. Keepalive (`transport.set_keepalive(30)`) is a dependency that carries the connection through silent stretches, not a substitute, because paramiko does not raise out of the exec read-loop on keepalive failure.

#### Scenario: Dead host raises instead of spinning forever

- **WHEN** the build instance dies mid-command and the channel never reports exit-ready
- **THEN** transport-liveness detection (or the wall-clock backstop) raises an exception, converting an infinite spin into a catchable failure so subsequent flavors are not silently blocked

#### Scenario: Long-but-progressing command is not aborted

- **WHEN** a legitimate long-running command (silent coldsnap compile, large upload) is still progressing on a live transport
- **THEN** it is not aborted by a short flat timer — liveness/progress keeps it running up to the generous wall-clock backstop

### Requirement: Failure taxonomy with reconnect-and-resume

The build driver SHALL classify each flavor's failure and react to its kind. An **application error** (non-zero exit, signature/validation failure, `RuntimeError`) means the host is healthy → the driver SHALL record that flavor as failed and **continue** to the next. A **transport/timeout error** (a raised paramiko or timeout exception) means the host is suspect → the driver SHALL first attempt a **bounded SSH reconnect** (reusing the existing connectivity retry loop); the reconnect outcome is the host-alive discriminator. If reconnect succeeds the driver SHALL, before wiping and starting the next flavor, make a best-effort attempt to terminate the abandoned remote `coldsnap` upload (e.g. `pkill`) over the fresh connection — because that process can otherwise survive the dropped channel, complete a stray server-side EBS snapshot the driver never captured, and run its upload workers concurrently with the next flavor's — and SHALL then resume the loop at the **next flavor** boundary (the in-flight flavor, whose snapshot ID was never captured, is recorded failed/indeterminate; wipe-and-reuse and the two-pass split make resuming clean for the instance-local and in-process state). If reconnect fails after its bounded attempts the driver SHALL stop attempting further Pass 1 flavors (marking them skipped), but SHALL still run Pass 2 for already-captured snapshots and SHALL still allow the workflow's `always()` destroy to run.

#### Scenario: Application error continues the loop

- **WHEN** a flavor fails with a non-zero exit, a signature/validation failure, or a `RuntimeError` on a healthy host
- **THEN** the driver records that flavor as failed and continues to the next flavor

#### Scenario: Transient transport drop reconnects and resumes

- **WHEN** a transport/timeout exception is raised but the host is actually alive (e.g. a transient TCP drop)
- **THEN** the driver's bounded reconnect succeeds and it resumes at the next flavor boundary, recording the interrupted flavor as failed/indeterminate while the remaining flavors still build

#### Scenario: Abandoned upload is terminated before resuming

- **WHEN** the reconnect follows a drop that interrupted a flavor's in-flight `coldsnap upload`
- **THEN** the driver makes a best-effort attempt to terminate the stale remote upload process before wiping and starting the next flavor, so the abandoned upload cannot complete a stray snapshot or add a second concurrent set of upload workers

#### Scenario: Genuine host death aborts Pass 1 but still finalizes

- **WHEN** the bounded reconnect fails after its attempts, indicating the host is genuinely dead
- **THEN** the driver stops further Pass 1 flavors and marks them skipped, but still runs Pass 2 for snapshot IDs already captured and still lets the workflow `always()` destroy tear the instance down
