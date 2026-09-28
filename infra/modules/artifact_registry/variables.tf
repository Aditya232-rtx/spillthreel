variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "GCP region, e.g. asia-south1."
}

variable "repository_id" {
  type        = string
  default     = "spillthereel"
  description = "Artifact Registry Docker repository name."
}
