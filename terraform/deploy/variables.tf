variable "attestable_ami_id" {
  description = "ID of the Attestable AMI created by the build process"
  type        = string
}

variable "instance_type" {
  description = <<-EOT
    EC2 instance type (must be NitroTPM-compatible).
    For GPU workloads, the following NVIDIA GPU instance families support NitroTPM:
      G4dn (T4), G5 (A10G), G6 (L4), G6e (L40S), G6f, Gr6, Gr6f, G7e,
      P5 (H100), P5e, P5en, P6-B200, P6-B300.
    NitroTPM is auto-enabled via the AMI — no additional launch configuration is needed.
    See: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/enable-nitrotpm-prerequisites.html
  EOT
  type        = string
  default     = "c5.9xlarge"
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed to access SSH on port 22, only used when enable_ssh is true"
  type        = string
  default     = ""
}

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "enable_ssh" {
  description = "Enable SSH debug access (NOT for production)"
  type        = bool
  default     = false
}

variable "key_pair_name" {
  description = "EC2 key pair name for SSH access"
  type        = string
  default     = ""
}
