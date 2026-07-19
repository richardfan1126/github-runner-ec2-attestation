## 1. Terraform: run-scoped naming, self-destruct, and orphan tagging

- [x] 1.1 Add a single `run_id` string variable to `terraform/build-ami/` (value `${github.run_id}-${github.run_attempt}`), documented as passed identically on `apply` and `destroy` (D9)
- [x] 1.2 Run-scope the account-unique resource names using `run_id`: the IAM role/policy/instance-profile names, the security-group `GroupName`, and the SSH `key_name`, keeping within IAM name constraints (`[\w+=,.@-]`, ≤128 chars) (D9)
- [x] 1.3 Replace the non-deterministic `formatdate(..., timestamp())` suffix in `ssh_key.tf` with the deterministic `run_id`-derived suffix (D9)
- [x] 1.4 Tag all build-ami resources (VPC, subnet, IGW, route table, instance, IAM, SG, key) with the run id — tag only, no rename for the non-account-unique resources (D9)
- [x] 1.5 Add `user_data` running `shutdown -h +150` and set `instance_initiated_shutdown_behavior = "terminate"` on the build instance (D2)
- [x] 1.6 Confirm the added `user_data` is on the transient builder instance only, with no runtime-attestation/PCR surface (D2, non-goal guard)
- [x] 1.7 Change the instance type default/parameter from `c5.9xlarge` to `c5.4xlarge` (D11)

## 2. Script: harden the SSH transport (`scripts/build-ami.py`)

- [x] 2.1 Add transport-liveness/progress detection to `execute_remote_command` as the primary signal, so a dead host raises instead of spinning `exit_status_ready()` forever (D6)
- [x] 2.2 Add a generous wall-clock backstop to `execute_remote_command`, sized so it never false-aborts a ~5 min silent compile or a 10–15 min 8 GB upload (D6)
- [x] 2.3 Verify keepalive (`transport.set_keepalive(30)`) stays in place as a dependency of the liveness check, not a substitute (D6)

## 3. Script: decouple provisioning and accept instance/run inputs

- [x] 3.1 Add CLI arguments for the pre-provisioned instance connection details (host + SSH private key path) and remove Terraform apply/provisioning from the script (D1)
- [x] 3.2 Remove `terraform destroy` from the script's `finally` block; keep only SSH close + secure temp-key deletion there (D1)
- [x] 3.3 Add a `run_id` argument used solely as an EBS-snapshot tag value — no Terraform, no resource naming in the script (D13)

## 4. Script: two-pass multi-flavor driver with wipe-and-reuse

- [x] 4.1 Parameterize the artifacts base path as a single named argument defaulting to `~/artifacts` (replace the hardcoded constant) (D4)
- [x] 4.2 Reset (wipe) the artifacts working directory at the start of each flavor iteration (D4)
- [x] 4.3 Implement Pass 1: per flavor sequentially wipe → pull → verify → validate → `coldsnap upload`, capture the returned snapshot ID, then discard the `.raw` (D5)
- [x] 4.4 Invoke `coldsnap upload` with `--tag run_id=<run_id>` for every flavor so each snapshot is tagged at `StartSnapshot` (D13)
- [x] 4.5 Implement Pass 2: batched wait for all captured snapshots to reach `completed`, then `register_ami` each, using the runner's boto3 client (D5, D8)
- [x] 4.6 Exclude any Pass-1-failed flavor from Pass 2; record a Pass-2 wait/register failure without aborting the other flavors (D5)
- [x] 4.7 Preserve per-flavor result output: write `ami_build_result-<flavor>.json` for each successful flavor (spec: Build result output)

## 5. Script: toolchain install-once gate

- [x] 5.1 Move the toolchain install (git/gcc, Rust, ORAS, gh, coldsnap compile) to run once before the flavor loop, keeping the existing per-tool verifications (D10)
- [x] 5.2 Treat install failure as a hard-abort of the whole run (zero results) — no per-flavor isolation for the install gate (D10)
- [x] 5.3 Retry transient install failures with backoff at per-step granularity; fail fast on deterministic failures without retry (D10, D7 generalization)

## 6. Script: failure taxonomy and reconnect-and-resume

- [x] 6.1 Classify each flavor failure: application error (non-zero exit / validation / `RuntimeError`) → record failed and continue; transport/timeout exception → host suspect (D7)
- [x] 6.2 On a transport/timeout error, attempt a bounded SSH reconnect reusing the existing connectivity retry loop; treat the reconnect outcome as the host-alive discriminator (D12)
- [x] 6.3 On successful reconnect, best-effort `pkill` the abandoned remote `coldsnap` upload over the fresh channel before wiping for the next flavor (D12)
- [x] 6.4 On successful reconnect, resume at the next-flavor boundary, recording the interrupted flavor as failed/indeterminate (D12)
- [x] 6.5 On reconnect failure after bounded attempts, stop further Pass 1 flavors (mark skipped) but still run Pass 2 for captured snapshots and still allow the workflow `always()` destroy (D7, D8)

## 7. Workflow: restructure the `build-ami` job (`.github/workflows/build-attestable-image.yml`)

- [x] 7.1 Remove the per-flavor matrix (`max-parallel`) so `build-ami` is a single job (D1)
- [x] 7.2 Add a `terraform apply` provisioning step passing the `run_id` var and `instance_type=c5.4xlarge` (D1, D9, D11)
- [x] 7.3 Invoke the refactored script once against the provisioned instance, passing host, SSH key path, and `${github.run_id}-${github.run_attempt}` as the run id for snapshot tagging (D1, D13)
- [x] 7.4 Add an `always()` `terraform destroy` step in the same job on the same runner, passing the identical `run_id` var (D1, D9)
- [x] 7.5 Make the destroy step retry with backoff and fail loud if it cannot converge (D3)
- [x] 7.6 Add explicit `timeout-minutes: 120` to the job, paired with the `shutdown -h +150` TTL so `worst-case run << timeout-minutes < TTL` holds (D2)
- [x] 7.7 `update-flavors-lock` consumes the per-flavor results unchanged in read/merge logic (carry-forward preserved). NOTE: a single-job cannot dynamically emit N separate artifacts, so build-ami emits **one** `ami-build-results` artifact whose internal layout is `ami-build-result-<flavor>/ami_build_result.json`; only the `update-flavors-lock` download selector changed (`pattern` → `name`), the read path `ami-results/ami-build-result-<flavor>/ami_build_result.json` and the jq merge are byte-for-byte unchanged (spec: failure isolation carry-forward)

## 8. Verification

- [x] 8.1 `openspec validate single-instance-ami-build --json` passes
- [x] 8.2 Confirm the build-ami Terraform local state (with SSH key material) remains gitignored and is not committed (constraint guard) — `git check-ignore` confirms `terraform.tfstate{,.backup}` are ignored; `git ls-files` shows no tfstate/pem/key material tracked
- [ ] 8.3 Watch the first post-merge run: destroy step green **and** no run-id-tagged orphan instance or snapshot remains; per-flavor results and `flavors.lock` update correctly (Migration Plan step 4) — POST-MERGE observational; requires a live CI run
- [ ] 8.4 On that measured run, confirm the ~80-min worst-case estimate and the compile-gate time; adjust the `timeout-minutes`/TTL pair while holding `TTL ≈ timeout-minutes + 30` and the 8 GB gpu assumption (D2, D11 caveats) — POST-MERGE observational; requires a live CI run

## 9. Tests: align the suite to the single-instance interface

Not in the original scope; added after implementation revealed the suite encodes the pre-change contract (approved: update tests now).

- [x] 9.1 Terraform property tests: updated `instance_type` default (`c5.9xlarge`→`c5.4xlarge`), added `run_id` var, run-scoped-name, and `user_data`/`instance_initiated_shutdown_behavior` expectations (`tests/property/test_infrastructure_provisioning.py`: `test_variables_configuration`, new `test_self_terminating_build_instance`, new `test_account_unique_names_are_run_scoped`)
- [x] 9.2 Rewrote script-cleanup tests to `cleanup_script_resources` (no terraform destroy — workflow owns teardown); dropped provisioning-in-script assertions (`test_build_result_cleanup_properties.py`)
- [x] 9.3 Updated `upload_snapshot` call sites for the new `run_id` arg + `--tag run_id=`; retargeted signature-order tests to `build_flavor_pass1` (verify-before-download); patched install retry `time.sleep` (`test_snapshot_ami_properties.py`, `test_signature_verification_properties.py`, `test_tool_installation_properties.py`)
- [x] 9.4 Updated secure-SSH-key-deletion test for `cleanup_script_resources` (no terraform destroy) (`test_secure_ssh_key_deletion_properties.py`)
- [x] 9.5 Rewrote the build-ami CI-job structure test for the single-job shape (no matrix; single-instance invocation flags; always()-summary with folded debug warning; provision/destroy lifecycle + timeout-minutes) (`test_ami_build_ci_job_unit.py`)
- [x] 9.6 Rewrote the integration test for the new CLI + two-pass driver, incl. multi-flavor partial-failure isolation (`test_build_ami_integration.py`)
- [x] 9.7 Full suite green for this change: 1300 passed / 7 skipped; the only 2 failures are pre-existing network-flaky `test_repository_properties.py` git-clone tests (unrelated; pass in isolation)
