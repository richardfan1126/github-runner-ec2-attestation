resource "aws_instance" "build_instance" {
  ami                         = data.aws_ami.amazon_linux_2023.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public_subnet.id
  vpc_security_group_ids      = [aws_security_group.build_instance_sg.id]
  iam_instance_profile        = aws_iam_instance_profile.build_instance_profile.name
  key_name                    = aws_key_pair.build_instance_key.key_name
  associate_public_ip_address = true

  metadata_options {
    http_tokens = "required"
  }

  # Runner-independent self-destruct (D2). The workflow's always() destroy cannot
  # fire on runner hard-death, the ~6 h ceiling force-kill, an exceeded cancellation
  # grace, or the destroy step itself erroring — so the instance schedules its own
  # shutdown at boot. Paired with instance_initiated_shutdown_behavior = "terminate"
  # below, the TTL bounds orphan billing to ~2.5 h and ONLY fires on true runner
  # hard-death (invariant: worst-case run << job timeout-minutes < TTL). This is the
  # transient BUILDER instance, not the runtime AMI, so this user_data has NO
  # attestation/PCR impact (the AMI is built from the pulled image, not this host).
  user_data = <<-EOT
    #!/bin/bash
    shutdown -h +150
  EOT

  # So the scheduled `shutdown -h` above terminates (not just stops) the instance.
  instance_initiated_shutdown_behavior = "terminate"

  root_block_device {
    volume_size           = 30
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name   = "build-ami-instance"
    run_id = var.run_id
  }
}
