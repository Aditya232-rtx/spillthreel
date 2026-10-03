variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "GCP region, e.g. asia-south1."
}

variable "service_name" {
  type        = string
  description = "Cloud Run service name (api, worker, cobalt)."
}

variable "image" {
  type        = string
  description = "Full container image URL (AR path or public image)."
}

variable "cpu" {
  type        = string
  default     = "1"
  description = "vCPU allocation."
}

variable "memory" {
  type        = string
  default     = "512Mi"
  description = "Memory allocation."
}

variable "min_instances" {
  type        = number
  default     = 0
  description = "Always 0 on staging: scale to zero, pay nothing idle."
}

variable "max_instances" {
  type        = number
  default     = 3
  description = "Hard cost cap for staging."
}

variable "concurrency" {
  type        = number
  default     = 80
  description = "Requests per instance."
}

variable "container_port" {
  type        = number
  default     = 8080
  description = "Container listening port (9000 for Cobalt)."
}

variable "service_account_email" {
  type        = string
  description = "Least-privilege runtime service account."
}

variable "env_vars" {
  type        = map(string)
  default     = {}
  description = "Plain (non-secret) environment variables."
}

variable "secret_env_vars" {
  type        = map(string)
  default     = {}
  description = "ENV name -> Secret Manager secret id (version 'latest')."
}

variable "allow_unauthenticated" {
  type        = bool
  default     = true
  description = "False for the internal-only worker service."
}

variable "invoker_members" {
  type        = list(string)
  default     = []
  description = "List of members (e.g., service accounts) to grant roles/run.invoker when allow_unauthenticated = false."
}
