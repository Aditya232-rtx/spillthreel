# SpillTheReel staging environment.
#
# WRITE ONLY — never `apply` from an agent session. A human runs apply
# after creating the GCP project + state bucket (see root README).
# Postgres + Storage + Auth are Supabase-managed and intentionally absent.

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  # EMPTY secret shells; values are added by hand post-apply and never
  # appear here, in state beyond the shell, or in the repo.
  secret_ids = [
    "SUPABASE_SECRET_KEY",
    "DATABASE_URL",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "SCRAPECREATORS_API_KEY",
    "APIFY_API_TOKEN",
    "BRIGHTDATA_API_TOKEN",
    "YOUTUBE_API_KEY",
    "SENTRY_DSN",
  ]

  # Plain env shared by api + worker (no secrets in this map).
  base_env = {
    ENVIRONMENT               = "staging"
    LOG_LEVEL                 = "INFO"
    SUPABASE_URL              = var.supabase_url
    SUPABASE_PUBLISHABLE_KEY  = var.supabase_publishable_key
    SUPABASE_BUCKET_MEDIA     = "media"
    SUPABASE_BUCKET_EXPORTS   = "exports"
    GCP_PROJECT_ID            = var.project_id
    GCP_REGION                = var.region
    CLOUD_TASKS_QUEUE_INGEST  = "ingest"
    CLOUD_TASKS_QUEUE_IMPORT  = "import"
    CLOUD_TASKS_QUEUE_ENHANCE = "enhance"
    WORKER_ALLOWED_SA_EMAILS = jsonencode([
      google_service_account.api.email,
      google_service_account.dispatcher.email,
    ])
    GROQ_ASR_MODEL       = "whisper-large-v3-turbo"
    RATE_LIMIT_DEFAULT   = "60/minute"
    RATE_LIMIT_SAVES     = "10/minute"
    CORS_ALLOWED_ORIGINS = var.cors_allowed_origins
    # Express Mode API key handles generation; embeddings use ADC/full Vertex.
    GEMINI_BACKEND          = "vertex_api_key"
    GEMINI_LOCATION         = "global"
    GEMINI_MODEL_FLASH_LITE = "gemini-3.5-flash-lite"
    GEMINI_MODEL_FLASH      = "gemini-3.8-flash"
    GEMINI_MODEL_PRO        = "gemini-3.1-pro-preview"
  }

  secret_env = {
    SUPABASE_SECRET_KEY = "SUPABASE_SECRET_KEY"
    DATABASE_URL        = "DATABASE_URL"
    GEMINI_API_KEY      = "GEMINI_API_KEY"
    GROQ_API_KEY        = "GROQ_API_KEY"
    SENTRY_DSN          = "SENTRY_DSN"
  }

  # Recovery only connects to Postgres. It must not receive Supabase
  # service-role, model, scraper or telemetry credentials.
  recovery_secret_env = {
    DATABASE_URL = "DATABASE_URL"
  }

  # Extractor keys are worker-only (least privilege — api never sees them).
  # NOTE(2026-10-14 staging apply): SCRAPECREATORS_API_KEY shell is kept in
  # local.secret_ids but NOT mapped into the worker revision yet — the secret
  # has no versions (human hasn't signed up) and Cloud Run rejects a revision
  # referencing version "latest" with zero versions. Re-add the one line
  # below once a value exists (see residuals in apply report).
  worker_secret_env = merge(local.secret_env, {
    APIFY_API_TOKEN      = "APIFY_API_TOKEN"
    BRIGHTDATA_API_TOKEN = "BRIGHTDATA_API_TOKEN"
    YOUTUBE_API_KEY      = "YOUTUBE_API_KEY"
  })

  # Keep Secret Manager access at the individual secret boundary. The API,
  # worker and recovery runner receive different secret sets at runtime.
  runtime_secret_access = {
    for binding in flatten([
      [for secret_id in values(local.secret_env) : {
        runtime = "api"
        secret  = secret_id
      }],
      [for secret_id in values(local.worker_secret_env) : {
        runtime = "worker"
        secret  = secret_id
      }],
      [for secret_id in values(local.recovery_secret_env) : {
        runtime = "dispatcher"
        secret  = secret_id
      }],
    ]) : "${binding.runtime}/${binding.secret}" => binding
  }

  runtime_service_accounts = {
    api        = google_service_account.api.email
    worker     = google_service_account.worker.email
    dispatcher = google_service_account.dispatcher.email
  }
}

# Least-privilege runtime identities.
resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "spillthereel-api-staging"
  display_name = "SpillTheReel staging API"
}

resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "spillthereel-worker-staging"
  display_name = "SpillTheReel staging worker"
}

resource "google_service_account" "dispatcher" {
  project      = var.project_id
  account_id   = "spillthereel-dispatcher"
  display_name = "SpillTheReel staging outbox dispatcher"
}

# Provisioning is declarative only; this Terraform change has not been applied.
resource "google_project_service" "gemini_enterprise" {
  project            = var.project_id
  service            = "aiplatform.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloud_scheduler" {
  project            = var.project_id
  service            = "cloudscheduler.googleapis.com"
  disable_on_destroy = false
}

# Gemini inference requires only the predict permission. Keep model access
# narrower than roles/aiplatform.user for both API (answers/embeddings) and
# worker (summaries) runtimes.
resource "google_project_iam_custom_role" "gemini_predict" {
  project     = var.project_id
  role_id     = "spillthereelGeminiPredict"
  title       = "SpillTheReel Gemini predict"
  description = "Allows Gemini inference through Agent Platform."
  permissions = ["aiplatform.endpoints.predict"]
}

# The Vertex Express API key is bound to Google's express-mode runtime
# identity. Grant it inference only; do not retain the broader expressUser
# role (datasets, memories, reasoning engines and sandbox operations).
import {
  id = "projects/${var.project_id}/roles/spillthereelGeminiPredict"
  to = google_project_iam_custom_role.gemini_predict
}

resource "google_project_iam_member" "vertex_express_gemini_predict" {
  project = var.project_id
  role    = google_project_iam_custom_role.gemini_predict.name
  member  = "serviceAccount:vertex-express@${var.project_id}.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "dispatcher_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.dispatcher.email}"
}

resource "google_project_iam_member" "api_roles" {
  for_each = toset([
    "roles/cloudtasks.enqueuer",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "worker_roles" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "api_gemini_predict" {
  project = var.project_id
  role    = google_project_iam_custom_role.gemini_predict.name
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "worker_gemini_predict" {
  project = var.project_id
  role    = google_project_iam_custom_role.gemini_predict.name
  member  = "serviceAccount:${google_service_account.worker.email}"
}

# The recovery dispatcher only delivers durable intents to Cloud Tasks. It
# uses a dedicated identity with queue-scoped access. Cloud Tasks requires the
# caller to have actAs on the OIDC identity; here the caller and token identity
# are the same dispatcher, so this grant is scoped to that one service account.
resource "google_service_account_iam_member" "dispatcher_act_as_self" {
  service_account_id = google_service_account.dispatcher.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.dispatcher.email}"
}

resource "google_cloud_tasks_queue_iam_member" "dispatcher_enqueue" {
  for_each = toset(module.queues.queue_names)

  project  = var.project_id
  location = var.region
  name     = each.value
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.dispatcher.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_secret_access" {
  for_each = local.runtime_secret_access

  project    = var.project_id
  secret_id  = each.value.secret
  role       = "roles/secretmanager.secretAccessor"
  member     = "serviceAccount:${local.runtime_service_accounts[each.value.runtime]}"
  depends_on = [module.secrets]
}

module "images" {
  source        = "../../modules/artifact_registry"
  project_id    = var.project_id
  region        = var.region
  repository_id = "spillthereel"
}

module "secrets" {
  source     = "../../modules/secret_manager"
  project_id = var.project_id
  secret_ids = local.secret_ids
}

module "queues" {
  source     = "../../modules/cloud_tasks"
  project_id = var.project_id
  region     = var.region
}

module "api" {
  source                = "../../modules/cloud_run_service"
  project_id            = var.project_id
  region                = var.region
  service_name          = "spillthereel-api-staging"
  image                 = "${module.images.repository}/api:latest"
  cpu                   = "1"
  memory                = "512Mi"
  min_instances         = 0
  max_instances         = 3
  concurrency           = 80
  timeout               = "300s"
  service_account_email = google_service_account.api.email
  env_vars = merge(local.base_env, {
    # Cloud Tasks push target for ingest/import/enhance jobs.
    WORKER_BASE_URL = module.worker.url
    # SA email that mints Cloud Tasks OIDC tokens (enqueue.py oidc_token).
    # Must match the api runtime SA — worker grants it roles/run.invoker.
    CLOUD_TASKS_OIDC_SA_EMAIL = google_service_account.api.email
  })
  secret_env_vars       = local.secret_env
  allow_unauthenticated = true
  depends_on = [
    google_project_service.gemini_enterprise,
    google_secret_manager_secret_iam_member.runtime_secret_access,
  ]
}

module "worker" {
  source        = "../../modules/cloud_run_service"
  project_id    = var.project_id
  region        = var.region
  service_name  = "spillthereel-worker-staging"
  image         = "${module.images.repository}/worker:latest"
  cpu           = "2"
  memory        = "2Gi"
  min_instances = 0
  max_instances = 2
  concurrency   = 4
  # Full media ingestion can run longer than Cloud Run's 5-minute default.
  # Leave 5 minutes for Cloud Tasks to receive a response before its 30-minute
  # maximum HTTP dispatch deadline. The enqueue path sets that deadline.
  timeout               = "1500s"
  service_account_email = google_service_account.worker.email
  env_vars = merge(local.base_env, {
    # Selects the worker ASGI app in the shared Dockerfile CMD pattern.
    APP_MODULE = "app.worker:app"
    # Non-secret product decision: enables the paid scraper head on
    # IG/TikTok (settings.scraper_provider; default "none" is boot-safe
    # degraded). Secret token itself stays in BRIGHTDATA_API_TOKEN.
    SCRAPER_PROVIDER = "brightdata"
  })
  secret_env_vars       = local.worker_secret_env
  allow_unauthenticated = false
  depends_on = [
    google_project_service.gemini_enterprise,
    google_secret_manager_secret_iam_member.runtime_secret_access,
  ]
  # Cloud Tasks pushes mint OIDC tokens from the api runtime SA, so only
  # that SA may invoke the worker. NOTE: worker gets NO WORKER_BASE_URL
  # env on purpose — setting it to module.worker.url inside its own
  # module block is a Terraform self-reference cycle. Audience verification
  # still passes via the exact-request-URL match in _oidc_audience_ok
  # (audience == push URL == request URL); the base-prefix check is
  # optional hardening only.
  invoker_members = [
    "serviceAccount:${google_service_account.api.email}",
    "serviceAccount:${google_service_account.dispatcher.email}",
  ]
}

module "outbox_recovery" {
  source                = "../../modules/cloud_run_job"
  project_id            = var.project_id
  region                = var.region
  job_name              = "spillthereel-outbox-recovery-staging"
  image                 = "${module.images.repository}/worker:latest"
  service_account_email = google_service_account.dispatcher.email
  cpu                   = "1"
  memory                = "512Mi"
  timeout               = "240s"
  max_retries           = 0
  task_count            = 1
  parallelism           = 1
  command               = ["python"]
  args                  = ["-m", "app.tasks.dispatch_due"]
  env_vars = merge(local.base_env, {
    WORKER_BASE_URL = module.worker.url
    # dispatch_due_jobs uses the same OIDC task-token identity as API producers.
    CLOUD_TASKS_OIDC_SA_EMAIL = google_service_account.dispatcher.email
  })
  # The command only opens the database and dispatches Cloud Tasks.
  secret_env_vars = local.recovery_secret_env

  depends_on = [
    google_cloud_tasks_queue_iam_member.dispatcher_enqueue,
    google_secret_manager_secret_iam_member.runtime_secret_access,
    google_service_account_iam_member.dispatcher_act_as_self,
    google_project_iam_member.dispatcher_logging,
  ]
}

module "outbox_recovery_scheduler" {
  source                       = "../../modules/dispatch_scheduler"
  project_id                   = var.project_id
  region                       = var.region
  job_name                     = module.outbox_recovery.name
  scheduler_name               = "spillthereel-outbox-recovery-staging"
  scheduler_service_account_id = "spillthe-reel-obx-sched"
  schedule                     = "*/5 * * * *"
  time_zone                    = "Etc/UTC"
  depends_on                   = [google_project_service.cloud_scheduler]
}

# Structured runner output is intentionally limited to safe batch statistics.
# The success metric gives Monitoring a last-success signal. The second metric
# counts successful runs that report an oldest pending row; null is omitted by
# the filter, so an empty queue is a healthy run.
resource "google_logging_metric" "outbox_recovery_success" {
  project     = var.project_id
  name        = "outbox_recovery_success"
  description = "Successful outbox recovery runner batches."
  filter      = <<-EOT
    resource.type="cloud_run_job"
    resource.labels.job_name="${module.outbox_recovery.name}"
    jsonPayload.scanned>=0
    jsonPayload.failed=0
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_logging_metric" "outbox_recovery_pending" {
  project     = var.project_id
  name        = "outbox_recovery_pending"
  description = "Successful outbox recovery batches with a pending row."
  filter      = <<-EOT
    resource.type="cloud_run_job"
    resource.labels.job_name="${module.outbox_recovery.name}"
    jsonPayload.scanned>=0
    jsonPayload.oldest_pending_at:*
    jsonPayload.oldest_pending_at!=NULL_VALUE
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

module "github_wif" {
  source      = "../../modules/workload_identity_github"
  project_id  = var.project_id
  github_repo = var.github_repo
}
