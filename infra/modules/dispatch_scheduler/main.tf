resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = var.scheduler_service_account_id
  display_name = "${var.job_name} scheduler"
}

resource "google_cloud_run_v2_job_iam_member" "invoke" {
  project  = var.project_id
  location = var.region
  name     = var.job_name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "trigger" {
  project          = var.project_id
  region           = var.region
  name             = var.scheduler_name
  description      = "Runs the bounded outbox recovery Cloud Run Job."
  schedule         = var.schedule
  time_zone        = var.time_zone
  attempt_deadline = "30s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${var.job_name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

output "service_account_email" {
  value       = google_service_account.scheduler.email
  description = "Identity Cloud Scheduler uses to start the Cloud Run Job."
}

output "schedule" {
  value       = google_cloud_scheduler_job.trigger.schedule
  description = "Configured Cloud Scheduler cron schedule."
}
