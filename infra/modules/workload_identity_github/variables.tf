variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "github_repo" {
  type        = string
  description = "GitHub repo allowed to impersonate, as 'owner/repo'."
}

variable "pool_id" {
  type        = string
  default     = "github-actions"
  description = "Workload Identity Pool id."
}

variable "deployer_roles" {
  type        = list(string)
  description = "Project-level roles for the Terraform deployer service account (least privilege)."
  default = [
    "roles/run.admin",
    "roles/iam.serviceAccountUser",
    "roles/artifactregistry.admin",
    "roles/cloudtasks.admin",
    "roles/secretmanager.admin",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.workloadIdentityPoolAdmin",
  ]
}
