# Security Review

Date: 2026-04-18

## Findings

### 1. Critical: OIDC authorization is not bound to the repository being executed

- `src/validation.py:177` accepts any OIDC token whose `repository` claim is present in `ALLOWED_REPOSITORIES`.
- `src/server.py:345` validates the OIDC token, but `src/server.py:415` then clones `body["repository_url"]` without verifying that the requested repository matches the validated claim.
- This allows a workflow from an allowed repository to present a valid token for that repository while requesting execution of a different repository that is reachable with the supplied GitHub token.
- Impact: the service can attest and execute code from a repository that was not actually authorized by the OIDC identity.

### 2. High: `MAX_CONCURRENT_EXECUTIONS` is configured but not enforced

- `src/config.py:20` requires `MAX_CONCURRENT_EXECUTIONS`.
- `src/server.py:376` validates request structure, but there is no concurrency gate before `src/server.py:476` creates a new execution record and `src/server.py:502` starts async execution.
- `src/execution_manager.py:40` always creates new execution records; it does not reject work when active executions exceed the configured cap.
- Impact: an authenticated caller can queue or start unbounded work and exhaust Docker, CPU, memory, or disk resources.

### 3. High: Execution output is stored in unbounded memory and is not reclaimed

- `src/output_collector.py:11` stores stdout and stderr in in-memory `bytearray` buffers.
- `src/output_collector.py:36` appends output without any size limit.
- `src/output_collector.py:126` has a `remove_output()` method, but I did not find any call site using it.
- `src/execution_manager.py:151` has `cleanup_expired()`, but I did not find it invoked anywhere.
- Impact: a script can exhaust process memory by printing large output, and completed executions can retain memory indefinitely.

### 4. Medium: GitHub tokens are exposed through clone URL handling

- `src/repository.py:105` builds `clone_url = f"https://{token}@github.com/{owner}/{repo}.git"`.
- `src/repository.py:112` passes that URL to `git clone`, exposing the token in process arguments and writing it into repository configuration.
- `src/script_executor.py:129` runs `chmod -R a+rX` on the cloned repository before execution so the unprivileged container user can read it.
- Because the whole repository is mounted read-only at `/workspace` (`src/script_executor.py:142`), the executed script can read `.git/config` and print the embedded token into stdout/stderr.
- Impact: repository credentials can be exfiltrated through command output returned by the service.

### 5. Medium: `MAX_SCRIPT_SIZE_BYTES` is dead configuration and never enforced

- `src/config.py:22` and `src/main.py:55` treat `MAX_SCRIPT_SIZE_BYTES` as an execution safety control.
- I did not find any enforcement path in request validation, repository checkout, or execution startup.
- `src/repository.py:175` only checks that the requested path exists; it does not check file size before execution.
- Impact: operators may believe large scripts are blocked when they are not, leaving an avoidable resource-exhaustion gap.

### 6. Medium: Public monitoring endpoints leak operational state on the internet-facing interface

- `terraform/deploy/main.tf:72` exposes the API port to `0.0.0.0/0`.
- `src/server.py:844` exposes `/health` without authentication and returns Docker availability, disk space, and active execution count.
- `src/server.py:904` exposes `/metrics` without authentication and returns execution totals, failure counts, and average duration.
- `src/server.py:199` exempts `/health` from rate limiting, and `/metrics` is still reachable anonymously.
- Impact: unauthenticated internet clients can profile system capacity, detect load, and time attacks or abuse accordingly.

### 7. Information: Default deployment is a public HTTP service, while the baked audience is not instance-specific

- `terraform/deploy/main.tf:72` allows inbound traffic from anywhere on port 8080 and `terraform/deploy/main.tf:114` assigns a public IP.
- `terraform/deploy/outputs.tf:28` publishes the endpoint as `http://<public-ip>:8080`.
- `kiwi-descriptions/root/etc/github-actions-remote-executor/env:25` bakes `EXPECTED_AUDIENCE=test-workflow` into the AMI instead of an instance-specific value.
- `README.md:31` describes `EXPECTED_AUDIENCE` as a value that should ensure tokens were issued for this specific Remote Executor instance.
- Impact: the deployed identity model does not match the documented trust model. Operators can easily deploy multiple instances that all accept the same OIDC audience, and the service is exposed over plain HTTP by default.

### 8. Medium: The AMI build path trusts network-fetched tool installers without integrity verification

- `scripts/build-ami.py:469` installs Rust via `curl ... | sh`.
- `scripts/build-ami.py:501` downloads ORAS from GitHub releases and installs it without checking a signature or checksum.
- `scripts/build-ami.py:543` adds the GitHub CLI package repo and installs from it directly.
- `scripts/build-ami.py:589` clones `awslabs/coldsnap` from GitHub and installs it from the fetched source.
- Impact: these steps are part of the trusted build path for the attestable AMI. A compromise of an upstream distribution channel or repository can backdoor the produced image before any later attestation step.

### 9. High: Encrypted requests are replayable because nonces are not validated

- `src/server.py:328` decrypts the `/execute` request and `src/server.py:345` authenticates it, but there is no nonce cache, sequence number, or one-time token check.
- `src/server.py:454` forwards the client nonce only into attestation generation.
- `src/server.py:624` and `src/server.py:638` do the same on `/execution/{id}/output`; `src/server.py:659` reads the nonce and `src/server.py:721` only includes it in output attestation.
- `src/attestation.py:126` and `src/attestation.py:291` show the nonce is written into the attestation command, not validated for uniqueness or freshness.
- Impact: anyone who can capture a valid encrypted request can replay it while the OIDC token remains valid, causing duplicate executions or repeated output fetches without learning the plaintext.

### 10. High: `/attest` is an unauthenticated, unthrottled NitroTPM operation

- `src/server.py:199` exempts `/attest` from rate limiting.
- `src/server.py:772` exposes `/attest` without authentication.
- `src/server.py:803` invokes `generate_attestation()` on every request, which calls the NitroTPM attestation binary.
- Impact: an internet client can repeatedly trigger expensive attestation work and degrade service availability even before reaching the authenticated execution flow.

### 11. High: `scripts/build-ami.py` is vulnerable to shell injection via `artifact_ref`

- `scripts/build-ami.py:49` only performs superficial format checks on `artifact_ref`; it does not reject shell metacharacters.
- `scripts/build-ami.py:694` interpolates `artifact_ref` directly into `oras pull` in a remote shell command.
- `scripts/build-ami.py:848` interpolates `artifact_ref`, `owner`, and `repo` directly into a multi-line remote shell command used during signature verification.
- Impact: a crafted artifact reference can execute arbitrary commands on the AMI build instance. Because that instance has an IAM role permitting snapshot and AMI operations, this can compromise the build pipeline and any image it produces.

### 12. Low: Logging context is global and mutable across concurrent requests

- `src/logging_config.py:15` stores log context in a single process-global dictionary.
- `src/server.py:172` and `src/server.py:192` set and clear that global context per request.
- `src/server.py:488` and `src/script_executor.py:97` also mutate the same global context from request handlers and background execution threads.
- Impact: concurrent requests and executions can overwrite each other’s `request_id` and `execution_id`, weakening auditability and making incident investigation less trustworthy in a security-sensitive service.

### 13. Medium: Execution shared keys appear to persist indefinitely in memory

- `src/server.py:483` stores a per-execution shared key in the encryption context map.
- `src/encryption.py:252` retains that key until `remove_encryption_context()` is called.
- `src/execution_manager.py:151` only removes expired contexts during `cleanup_expired()`, but I did not find any production call site that schedules or invokes that cleanup path.
- Impact: completed execution sessions can remain decryptable for the life of the process if a client-side shared key is later exposed, and long-lived processes accumulate stale authorization material in memory.

### 14. Medium: SSH-enabled debug images are published under the same artifact conventions as normal images

- `.github/workflows/build-attestable-image.yml:42` enables the SSH debug path when `workflow_dispatch` sets `enable_ssh=true`.
- `kiwi-descriptions/config.sh:23` then enables `sshd`, and `kiwi-descriptions/config.sh:51` grants `ec2-user` passwordless sudo in that image.
- `.github/workflows/build-attestable-image.yml:47` only adds a workflow summary warning; `.github/workflows/build-attestable-image.yml:92` generates the same tag format, and `.github/workflows/build-attestable-image.yml:114` pushes to the same `attestable-image` namespace without a dedicated debug marker annotation.
- `README.md:63` describes the resulting artifact reference using the standard production-looking format.
- Impact: an SSH-enabled, passwordless-sudo debug image can still be attested, published, and later consumed by the AMI build flow as if it were a normal artifact unless operators manually track the workflow context.

### 15. Low: The AMI build instance SSH private key is exported through Terraform outputs

- `terraform/build-ami/ssh_key.tf:1` generates a new private key in Terraform.
- `terraform/build-ami/outputs.tf:11` exposes that private key as the `ssh_private_key` output (marked sensitive but still present in state).
- `scripts/build-ami.py:227` reads the private key from Terraform output and `scripts/build-ami.py:1215` writes it to disk for SSH access.
- Impact: the ephemeral build-instance credential is materialized in local Terraform state and process memory, increasing the blast radius of workstation or CI compromise during the AMI build flow.

### 16. Medium: OIDC policy is repo-scoped only and does not restrict branch, workflow, or trust level

- `src/validation.py:138` / `src/validation.py:239` verify signature, issuer, and audience.
- `src/validation.py:177` / `src/validation.py:278` then authorize solely on the `repository` claim.
- I did not find any checks on claims such as `sub`, `ref`, `ref_protected`, `environment`, or `job_workflow_ref`, even though the model comment in `src/models.py:96` shows `sub` carries branch/ref context.
- Impact: any workflow in an allowed repository that can mint a GitHub OIDC token for the configured audience is treated as equally trusted. That broadens authorization to include unprotected branches, less-trusted workflows, and other execution contexts that may not meet the intended security bar.

### 17. Medium: Artifact provenance verification is bound to repository identity, not a specific trusted workflow

- `scripts/build-ami.py:836` derives only `owner/repo` identity from the artifact reference.
- `scripts/build-ami.py:858` verifies the attestation with `gh attestation verify ... -R {identity}`.
- I did not find any verification of the producing workflow file, branch, ref protection status, or debug-image mode before accepting the artifact and converting it into an AMI.
- Impact: any attested artifact from the same repository can satisfy the provenance check, even if it was produced by a different workflow or a less-trusted repository state than operators intended to trust for AMI creation.

### 18. Low: `/execution/{id}/output` does not bind the execution record to the OIDC repository claim

- `src/server.py:638` validates the supplied OIDC token on the output endpoint.
- `src/server.py:672` then loads the execution record by `execution_id`, but I did not find any comparison between that execution’s repository and the validated OIDC claims.
- Impact: if an execution ID and its shared key are exposed, any token from any repository in `ALLOWED_REPOSITORIES` can be used to fetch that execution’s output; the endpoint does not enforce that the caller comes from the same repository that created the execution.

### 19. High: Container isolation relies on ambient Docker daemon defaults rather than a pinned hardened host configuration

- `kiwi-descriptions/config.sh:17` enables the Docker daemon in the AMI.
- I did not find a Docker `daemon.json`, user-namespace remap, custom seccomp profile, or SELinux/AppArmor policy under `kiwi-descriptions/root/etc`.
- `src/script_executor.py:118` applies some per-container flags, but host-level isolation behavior is otherwise whatever the distro Docker defaults are on the built AMI.
- Impact: the safety of running untrusted code depends heavily on ambient daemon/kernel defaults that are not explicitly hardened or locked by this project.

### 20. High: Execution containers do not explicitly drop Linux capabilities

- `src/script_executor.py:134` creates containers with `read_only=True`, `network_mode="none"`, `security_opt=["no-new-privileges"]`, and `user="nobody"`.
- I did not find `cap_drop=["ALL"]` or an explicit allowlist of capabilities.
- Impact: the container keeps Docker’s default capability set rather than a minimal one. Even with a non-root user, this is weaker than a deliberate least-privilege isolation model for untrusted script execution.

### 21. Medium: The full cloned repository, including VCS metadata, is exposed inside the execution container

- `src/script_executor.py:129` makes the cloned repository world-readable with `chmod -R a+rX`.
- `src/script_executor.py:142` bind-mounts the entire clone read-only at `/workspace`.
- Impact: executed scripts can read `.git/`, repository config, workflow files, and any other checked-out material in the clone. This weakens intra-job isolation and amplifies the credential-exposure issue already noted for `.git/config`.

### 22. Medium: The host executor service is not strongly sandboxed, increasing post-escape impact

- `kiwi-descriptions/root/etc/systemd/system/github-actions-remote-executor.service:13` sets `NoNewPrivileges=false`.
- I did not find service hardening controls such as `PrivateTmp`, `ProtectSystem`, `ProtectHome`, `RestrictAddressFamilies`, `SystemCallFilter`, or a dedicated non-root service account in that unit.
- Impact: if a container breakout or Docker-daemon abuse occurs, the host-side executor process runs in a comparatively soft environment.

### 23. Medium: The execution container image is tag-based and not pinned or verified

- `kiwi-descriptions/root/etc/github-actions-remote-executor/env:28` sets `CONTAINER_IMAGE=python:3.11-slim`.
- `src/main.py:108` only ensures the configured image is present by pulling it if needed.
- I did not find digest pinning, signature verification, or an allowlist policy for execution images.
- Impact: upstream tag drift or registry compromise can silently change the code, packages, and isolation surface of the runtime container environment.

### 24. High: The AMI build instance IAM role is broadly wildcarded across EC2/EBS image operations

- `terraform/build-ami/iam.tf:24` grants the build instance `ec2:CreateSnapshot`, `ec2:DeleteSnapshot`, `ec2:RegisterImage`, `ec2:DeregisterImage`, `ec2:ModifyImageAttribute`, `ec2:ModifySnapshotAttribute`, and related actions on `Resource = "*"`.
- `terraform/build-ami/iam.tf:43` similarly grants EBS direct snapshot APIs on `Resource = "*"`.
- Impact: compromise of the build instance or its automation path gives account-wide image/snapshot manipulation capability rather than narrowly scoped permission to only the resources created for this build.

### 25. Medium: The trusted build path depends on mutable upstream build hosts and AMIs

- `.github/workflows/build-attestable-image.yml:23` runs on `ubuntu-latest`, which is a moving GitHub-hosted runner image.
- `terraform/build-ami/data.tf:6` selects `most_recent = true` for the Amazon Linux 2023 build instance AMI.
- `kiwi-descriptions/appliance.kiwi:43` uses the AL2023 `latest` mirrorlist path.
- Impact: reproducibility and provenance are weakened because materially different upstream build environments can be selected over time without any repo change.

### 26. Low: The builder image comments claim pinned package versions, but package installation is effectively floating

- `.github/docker/Dockerfile.kiwi-builder:2` says the builder uses pinned versions for reproducible builds.
- The base image tag is pinned, but `.github/docker/Dockerfile.kiwi-builder:9` installs DNF packages without explicit NEVRA version pinning.
- Impact: rebuilds against the same Dockerfile can still consume different package versions from the configured repositories, reducing build determinism in the trusted image-construction path.

## Coverage Gaps

- I did not find tests that assert the OIDC `repository` claim must match the requested `repository_url`.
- I did not find enforcement tests for `MAX_CONCURRENT_EXECUTIONS`.
- I did not find output-size or retention cleanup tests that would prevent the memory growth issue above.
- I did not find tests asserting `MAX_SCRIPT_SIZE_BYTES` is enforced.
- I did not find tests or deployment safeguards requiring TLS or an instance-specific OIDC audience.
- I did not find anti-replay tests for encrypted `/execute` or `/execution/{id}/output` requests.
- I did not find tests that reject shell metacharacters in `artifact_ref` before using it in the build pipeline.
- I did not find a production cleanup path that expires stale execution encryption contexts.
- I did not find machine-readable tagging or policy checks that distinguish SSH-enabled debug images from normal attestable images.
- I did not find tests constraining OIDC authorization to specific refs, protected branches, environments, or workflow identities.
- I did not find provenance checks that pin AMI conversion to a specific trusted workflow definition.
- I did not inspect a live built AMI, so I could not verify the effective Docker seccomp profile, capability set, SELinux/AppArmor enforcement, or user-namespace behavior at runtime.
- I did not perform dependency CVE triage against the locked package set or the mutable upstream build environments.
