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
        Sid    = "EC2ResourceLevelActions"
        Effect = "Allow"
        Action = [
          "ec2:CreateSnapshot",
          "ec2:DeleteSnapshot",
          "ec2:ModifySnapshotAttribute",
          "ec2:RegisterImage",
          "ec2:DeregisterImage",
          "ec2:ModifyImageAttribute"
        ]
        Resource = [
          "arn:aws:ec2:${data.aws_region.current.name}::snapshot/*",
          "arn:aws:ec2:${data.aws_region.current.name}::image/*",
          "arn:aws:ec2:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:volume/*"
        ]
        Condition = {
          StringEquals = {
            "aws:RequestedRegion" = data.aws_region.current.name
          }
        }
      },
      {
        Sid    = "EC2DescribeActions"
        Effect = "Allow"
        Action = [
          "ec2:DescribeSnapshots",
          "ec2:DescribeSnapshotAttribute",
          "ec2:DescribeImages",
          "ec2:DescribeImageAttribute"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:RequestedRegion" = data.aws_region.current.name
          }
        }
      },
      {
        Sid    = "EC2CreateTags"
        Effect = "Allow"
        Action = [
          "ec2:CreateTags"
        ]
        Resource = [
          "arn:aws:ec2:${data.aws_region.current.name}::snapshot/*",
          "arn:aws:ec2:${data.aws_region.current.name}::image/*",
          "arn:aws:ec2:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:volume/*"
        ]
        Condition = {
          StringEquals = {
            "aws:RequestedRegion" = data.aws_region.current.name
          }
        }
      },
      {
        Sid    = "EBSDirectAPIActions"
        Effect = "Allow"
        Action = [
          "ebs:CompleteSnapshot",
          "ebs:GetSnapshotBlock",
          "ebs:ListChangedBlocks",
          "ebs:ListSnapshotBlocks",
          "ebs:PutSnapshotBlock",
          "ebs:StartSnapshot"
        ]
        Resource = "arn:aws:ec2:${data.aws_region.current.name}::snapshot/*"
        Condition = {
          StringEquals = {
            "aws:RequestedRegion" = data.aws_region.current.name
          }
        }
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
