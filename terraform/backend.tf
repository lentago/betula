terraform {
  # Remote state in solidago's shared tfstate bucket, isolated key. CI reaches
  # it through the betula-github-actions-terraform OIDC role (S3 r/w on this
  # key, the tfstate CMK, and the lock table only; lentago/solidago#207).
  backend "s3" {
    bucket         = "solidago-tfstate-365184644049"
    key            = "betula/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "solidago-tfstate-lock"
    encrypt        = true
  }
}
