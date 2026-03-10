resource "aws_security_group" "build_instance_sg" {
  name        = "build-ami-instance-sg"
  description = "Security group for AMI build instance"
  vpc_id      = aws_vpc.build_vpc.id

  ingress {
    description = "SSH from allowed CIDR"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "build-ami-instance-sg"
  }
}
