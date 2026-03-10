resource "tls_private_key" "build_instance_key" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "build_instance_key" {
  key_name   = "build-ami-key-${formatdate("YYYYMMDDhhmmss", timestamp())}"
  public_key = tls_private_key.build_instance_key.public_key_openssh

  tags = {
    Name = "build-ami-key"
  }
}
