data "aws_iam_policy_document" "github_actions_permissions" {

  # ── EC2: instance lifecycle ──────────────────────────────────────────────────
  # Terraform aws_instance apply/destroy + boto3 waiters used by build-ami.py
  statement {
    sid    = "EC2InstanceLifecycle"
    effect = "Allow"
    actions = [
      "ec2:RunInstances",
      "ec2:TerminateInstances",
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceStatus",
      "ec2:DescribeInstanceAttribute",
    ]
    resources = ["*"]
  }

  # ── EC2: key pair ────────────────────────────────────────────────────────────
  # Terraform aws_key_pair uses ImportKeyPair (not CreateKeyPair)
  statement {
    sid    = "EC2KeyPair"
    effect = "Allow"
    actions = [
      "ec2:ImportKeyPair",
      "ec2:DeleteKeyPair",
      "ec2:DescribeKeyPairs",
    ]
    resources = ["*"]
  }

  # ── EC2: security group ──────────────────────────────────────────────────────
  # Terraform aws_security_group; Terraform 5.x reads rules separately
  statement {
    sid    = "EC2SecurityGroup"
    effect = "Allow"
    actions = [
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSecurityGroupRules",
    ]
    resources = ["*"]
  }

  # ── EC2: VPC and networking ──────────────────────────────────────────────────
  # Terraform aws_vpc, aws_subnet, aws_internet_gateway, aws_route_table
  statement {
    sid    = "EC2VpcNetworking"
    effect = "Allow"
    actions = [
      "ec2:CreateVpc",
      "ec2:DeleteVpc",
      "ec2:DescribeVpcs",
      "ec2:ModifyVpcAttribute",
      "ec2:DescribeVpcAttribute",
      "ec2:CreateSubnet",
      "ec2:DeleteSubnet",
      "ec2:DescribeSubnets",
      "ec2:ModifySubnetAttribute",
      "ec2:CreateInternetGateway",
      "ec2:DeleteInternetGateway",
      "ec2:AttachInternetGateway",
      "ec2:DetachInternetGateway",
      "ec2:DescribeInternetGateways",
      "ec2:CreateRouteTable",
      "ec2:DeleteRouteTable",
      "ec2:CreateRoute",
      "ec2:DeleteRoute",
      "ec2:AssociateRouteTable",
      "ec2:DisassociateRouteTable",
      "ec2:DescribeRouteTables",
    ]
    resources = ["*"]
  }

  # ── EC2: tagging and data sources ────────────────────────────────────────────
  # ec2:CreateTags applied at resource creation; data sources used in data.tf
  statement {
    sid    = "EC2TaggingAndDataSources"
    effect = "Allow"
    actions = [
      "ec2:CreateTags",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeImages",
    ]
    resources = ["*"]
  }

  # ── EC2: snapshot waiter ─────────────────────────────────────────────────────
  # boto3 snapshot_completed waiter in wait_for_snapshot; snapshot is created
  # on the instance but the runner polls its completion status
  statement {
    sid    = "EC2SnapshotWaiter"
    effect = "Allow"
    actions = [
      "ec2:DescribeSnapshots",
    ]
    resources = ["*"]
  }

  # ── EC2: AMI registration ────────────────────────────────────────────────────
  # boto3 register_image call in register_ami, invoked directly by build-ami.py
  statement {
    sid    = "EC2RegisterImage"
    effect = "Allow"
    actions = [
      "ec2:RegisterImage",
    ]
    resources = ["*"]
  }

  # ── IAM: instance role and profile lifecycle ─────────────────────────────────
  # Terraform aws_iam_role, aws_iam_policy, aws_iam_instance_profile in
  # terraform/build-ami/iam.tf
  statement {
    sid    = "IAMRoleAndProfileLifecycle"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:TagRole",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:TagPolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:GetInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
    ]
    resources = ["*"]
  }

  # ── IAM: PassRole ────────────────────────────────────────────────────────────
  # Required so Terraform can attach the instance profile to the EC2 instance.
  # Scoped to the specific instance role created by terraform/build-ami/.
  statement {
    sid    = "IAMPassRole"
    effect = "Allow"
    actions = [
      "iam:PassRole",
    ]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/build-ami-instance-role",
    ]
  }

  # ── STS: identity check ──────────────────────────────────────────────────────
  # Used by the Terraform AWS provider and boto3 at startup
  statement {
    sid    = "STSGetCallerIdentity"
    effect = "Allow"
    actions = [
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "github_actions" {
  name        = "github-actions-ami-builder-policy"
  description = "Permissions required by the build-ami CI job to provision EC2 infrastructure and register AMIs"
  policy      = data.aws_iam_policy_document.github_actions_permissions.json

  tags = {
    Name = "github-actions-ami-builder-policy"
  }
}

resource "aws_iam_role_policy_attachment" "github_actions" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.github_actions.arn
}
