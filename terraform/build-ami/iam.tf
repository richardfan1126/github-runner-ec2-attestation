resource "aws_iam_role" "build_instance_role" {
  name = "build-ami-instance-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "build-ami-instance-role"
  }
}

resource "aws_iam_policy" "build_instance_policy" {
  name        = "build-ami-instance-policy"
  description = "Policy for AMI build instance to create snapshots and register AMIs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateSnapshot",
          "ec2:DeleteSnapshot",
          "ec2:DescribeSnapshots",
          "ec2:DescribeSnapshotAttribute",
          "ec2:ModifySnapshotAttribute",
          "ec2:RegisterImage",
          "ec2:DeregisterImage",
          "ec2:DescribeImages",
          "ec2:DescribeImageAttribute",
          "ec2:ModifyImageAttribute",
          "ec2:CreateTags"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ebs:CompleteSnapshot",
          "ebs:GetSnapshotBlock",
          "ebs:ListChangedBlocks",
          "ebs:ListSnapshotBlocks",
          "ebs:PutSnapshotBlock",
          "ebs:StartSnapshot"
        ]
        Resource = "*"
      }
    ]
  })

  tags = {
    Name = "build-ami-instance-policy"
  }
}

resource "aws_iam_role_policy_attachment" "build_instance_policy_attachment" {
  role       = aws_iam_role.build_instance_role.name
  policy_arn = aws_iam_policy.build_instance_policy.arn
}

resource "aws_iam_instance_profile" "build_instance_profile" {
  name = "build-ami-instance-profile"
  role = aws_iam_role.build_instance_role.name

  tags = {
    Name = "build-ami-instance-profile"
  }
}
