variable "attestable_ami_id" {
  description = "ID of the Attestable AMI created by the build process"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type (must be NitroTPM-compatible)"
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
