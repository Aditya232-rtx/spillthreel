# Staging inputs. No defaults for project/region/state bucket on purpose:
# nothing about GCP may be invented — the human supplies real values in
# terraform.tfvars (see terraform.tfvars.example). Only the region has a
# default (asia-south1, same continent as the Supabase project).
variable "project_id" {
  type        = string
  description = "GCP project id for staging."
}

variable "region" {
  type        = string
  default     = "asia-south1"
  description = "GCP region for staging."
}

variable "state_bucket" {
  type        = string
  description = "Pre-created GCS bucket holding terraform state (backend key below)."
}

variable "github_repo" {
  type        = string
  default     = "Aditya232-rtx/spillthreel"
  description = "owner/repo allowed to impersonate via Workload Identity."
}

variable "supabase_url" {
  type        = string
  description = "Staging Supabase project URL (public value)."
}

variable "supabase_publishable_key" {
  type        = string
  description = "Staging publishable key (public value, RLS still applies)."
  sensitive   = true
}

variable "cors_allowed_origins" {
  type        = string
  description = "Comma-separated origins for the staging API (CORS lockdown)."
}
