variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "Cloud Scheduler and Cloud Run region."
}

variable "job_name" {
  type        = string
  description = "Existing Cloud Run Job to invoke."
}

variable "scheduler_name" {
  type        = string
  description = "Cloud Scheduler job name."
}

variable "scheduler_service_account_id" {
  type        = string
  description = "Service account id used only for Scheduler-to-Job invocation."
}

variable "schedule" {
  type        = string
  default     = "*/5 * * * *"
  description = "Cron schedule for outbox recovery."
}

variable "time_zone" {
  type        = string
  default     = "Etc/UTC"
  description = "IANA time zone for the cron schedule."
}
