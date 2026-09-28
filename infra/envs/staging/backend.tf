# Remote state in GCS. Bucket NAME comes from -backend-config at init
# time (backend blocks take no variables):
#   terraform init -backend-config="bucket=<state_bucket>"
terraform {
  backend "gcs" {
    prefix = "staging"
  }
}
