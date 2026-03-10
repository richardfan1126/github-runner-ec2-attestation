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

  root_block_device {
    volume_size           = 30
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name = "build-ami-instance"
  }
}
