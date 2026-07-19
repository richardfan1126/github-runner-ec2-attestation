resource "tls_private_key" "build_instance_key" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "build_instance_key" {
  # Run-scoped, deterministic key_name (D9): replaces the previous
  # formatdate(..., timestamp()) suffix, which was non-deterministic, not
  # externally knowable, and could produce spurious apply/destroy diffs. Sourcing
  # the suffix from run_id makes the name deterministic, diff-stable, and shared
  # with the other account-unique resources.
  key_name   = "build-ami-key-${var.run_id}"
  public_key = tls_private_key.build_instance_key.public_key_openssh

  tags = {
    Name   = "build-ami-key"
    run_id = var.run_id
  }
}
