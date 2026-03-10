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
  default     = "c5.9xlarge"
}
