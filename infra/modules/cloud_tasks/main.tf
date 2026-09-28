# The three pipeline queues (TRD §13, PRD F8.11). Free tier covers
# ~1M operations/month — staging will use a tiny fraction of that.
#
# The `enhance` queue is deliberately throttled: bulk-import full-video
# processing must not trip Instagram anti-bot limits nor burn Gemini
# budget in bursts.
resource "google_cloud_tasks_queue" "ingest" {
  project  = var.project_id
  location = var.region
  name     = "ingest"

  rate_limits {
    max_concurrent_dispatches = 10
    max_dispatches_per_second = 5
  }

  retry_config {
    max_attempts       = 3
    max_retry_duration = "600s"
  }
}

resource "google_cloud_tasks_queue" "import" {
  project  = var.project_id
  location = var.region
  name     = "import"

  rate_limits {
    max_concurrent_dispatches = 2
    max_dispatches_per_second = 1
  }

  retry_config {
    max_attempts       = 3
    max_retry_duration = "3600s"
  }
}

resource "google_cloud_tasks_queue" "enhance" {
  project  = var.project_id
  location = var.region
  name     = "enhance"

  rate_limits {
    max_concurrent_dispatches = 2
    max_dispatches_per_second = 0.1
  }

  retry_config {
    max_attempts       = 3
    max_retry_duration = "3600s"
  }
}
