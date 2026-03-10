output "instance_id" {
  description = "ID of the build instance"
  value       = aws_instance.build_instance.id
}

output "instance_public_ip" {
  description = "Public IP address of the build instance"
  value       = aws_instance.build_instance.public_ip
}

output "ssh_private_key" {
  description = "Private SSH key for connecting to the build instance"
  value       = tls_private_key.build_instance_key.private_key_pem
  sensitive   = true
}

output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.build_vpc.id
}

output "security_group_id" {
  description = "ID of the security group"
  value       = aws_security_group.build_instance_sg.id
}
