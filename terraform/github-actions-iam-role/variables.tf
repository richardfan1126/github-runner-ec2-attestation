variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "github_org" {
  description = "GitHub organisation or user name that owns the repository (e.g. my-org)"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "github-runner-ec2-attestation"
}

variable "create_oidc_provider" {
  description = "Whether to create the GitHub Actions OIDC provider. Set to false if the provider already exists in this AWS account."
  type        = bool
  default     = true
}
