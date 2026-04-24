output "role_arn" {
  description = "ARN of the IAM role. Set this value as the vars.AWS_ROLE_ARN repository variable in GitHub (Settings → Secrets and variables → Actions → Variables)."
  value       = aws_iam_role.github_actions.arn
}
