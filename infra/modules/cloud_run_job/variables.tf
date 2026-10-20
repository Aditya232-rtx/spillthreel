variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "Cloud Run region."
}

variable "job_name" {
  type        = string
  description = "Cloud Run Job name."
}

variable "image" {
  type        = string
  description = "Worker image containing the Python recovery runner."
}

variable "service_account_email" {
  type        = string
  description = "Runtime identity used by the job."
}

variable "command" {
  type        = list(string)
  default     = ["python"]
  description = "Container executable override."
}

variable "args" {
  type        = list(string)
  default     = ["-m", "app.tasks.dispatch_due"]
  description = "Arguments passed to the command."
}

variable "cpu" {
  type        = string
  default     = "1"
  description = "CPU limit for each job task."
}

variable "memory" {
  type        = string
  default     = "512Mi"
  description = "Memory limit for each job task."
}

variable "timeout" {
  type        = string
  default     = "240s"
  description = "Maximum runtime for each task. Keep below the scheduler interval."
}

variable "max_retries" {
  type        = number
  default     = 0
  description = "Cloud Run task retries. The next scheduled run recovers pending intents."
}

variable "task_count" {
  type        = number
  default     = 1
  description = "Number of tasks per execution. Recovery is intentionally single-task."
}

variable "parallelism" {
  type        = number
  default     = 1
  description = "Maximum tasks run at once within one execution."
}

variable "env_vars" {
  type        = map(string)
  default     = {}
  description = "Non-secret runtime environment."
}

variable "secret_env_vars" {
  type        = map(string)
  default     = {}
  description = "ENV name to Secret Manager secret id, exposed at latest version."
}

output "name" {
  value       = google_cloud_run_v2_job.job.name
  description = "Cloud Run Job name."
}

output "id" {
  value       = google_cloud_run_v2_job.job.id
  description = "Cloud Run Job resource id."
}
