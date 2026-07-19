variable "region" {
  description = "AWS region for the build instance"
  type        = string
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed to SSH to the build instance"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type for the build instance"
  type        = string
  # Right-sized for a once-per-run coldsnap compile (D11): the CPU-bound compile
  # now happens a single time before the flavor loop, not once per flavor, so the
  # former c5.9xlarge is oversized. Uploads are EBS-Direct-API-bound (fixed 64
  # workers), so they are instance-insensitive.
  default = "c5.4xlarge"
}

variable "run_id" {
  description = <<-EOT
    Run-scoped identifier, valued ${"$"}{github.run_id}-${"$"}{github.run_attempt},
    passed IDENTICALLY on apply and destroy (D9). Used to (a) run-scope the
    account-unique resource names (IAM role/policy/instance-profile, security-group
    GroupName, SSH key_name) so concurrent or re-run applies do not collide on
    account-global names, and (b) tag every resource so any runner-death orphan is
    attributable to its owning run and sweepable out-of-band. run_attempt is part of
    the value because re-runs reuse run_id.
  EOT
  type        = string
}
