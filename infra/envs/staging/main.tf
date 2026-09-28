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
    "COGNEE_API_KEY",
    "COBALT_API_KEY",
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
    GEMINI_MODEL_FLASH_LITE   = "gemini-2.5-flash-lite"
    GEMINI_MODEL_FLASH        = "gemini-2.5-flash"
    GEMINI_MODEL_PRO          = "gemini-2.5-pro"
    GROQ_ASR_MODEL            = "whisper-large-v3-turbo"
    COGNEE_BASE_URL           = "https://api.cognee.ai"
    RATE_LIMIT_DEFAULT        = "60/minute"
    RATE_LIMIT_SAVES          = "10/minute"
    CORS_ALLOWED_ORIGINS      = var.cors_allowed_origins
  }

  secret_env = {
    SUPABASE_SECRET_KEY = "SUPABASE_SECRET_KEY"
    DATABASE_URL        = "DATABASE_URL"
    GEMINI_API_KEY      = "GEMINI_API_KEY"
    GROQ_API_KEY        = "GROQ_API_KEY"
    COGNEE_API_KEY      = "COGNEE_API_KEY"
    COBALT_API_KEY      = "COBALT_API_KEY"
    SENTRY_DSN          = "SENTRY_DSN"
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

resource "google_project_iam_member" "api_roles" {
  for_each = toset([
    "roles/secretmanager.secretAccessor",
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
    "roles/secretmanager.secretAccessor",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.worker.email}"
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
  service_account_email = google_service_account.api.email
  env_vars = merge(local.base_env, {
    # Cloud Tasks push target for ingest/import/enhance jobs.
    WORKER_BASE_URL = module.worker.url
  })
  secret_env_vars       = local.secret_env
  allow_unauthenticated = true
}

module "worker" {
  source                = "../../modules/cloud_run_service"
  project_id            = var.project_id
  region                = var.region
  service_name          = "spillthereel-worker-staging"
  image                 = "${module.images.repository}/worker:latest"
  cpu                   = "2"
  memory                = "2Gi"
  min_instances         = 0
  max_instances         = 2
  concurrency           = 4
  service_account_email = google_service_account.worker.email
  env_vars              = local.base_env
  secret_env_vars       = local.secret_env
  allow_unauthenticated = false
}

# Cobalt on Cloud Run (scale-to-zero) instead of GKE Autopilot: cheaper
# and simpler for staging. Upstream image, unmodified (TRD §12.1).
#
# GKE alternative — switch if extractor traffic gets blocked by egress IP
# reputation (shared Cloud Run egress IPs can land on blocklists) or if
# sustained QPS makes per-request billing exceed a small Autopilot
# cluster. Then: provision GKE Autopilot via a gke_cobalt module, point
# COBALT_BASE_URL at its internal LB, keep everything else identical.
module "cobalt" {
  source                = "../../modules/cloud_run_service"
  project_id            = var.project_id
  region                = var.region
  service_name          = "spillthereel-cobalt-staging"
  image                 = "ghcr.io/imputnet/cobalt:10"
  cpu                   = "1"
  memory                = "512Mi"
  min_instances         = 0
  max_instances         = 2
  concurrency           = 20
  container_port        = 9000
  service_account_email = google_service_account.worker.email
  env_vars = {
    API_URL  = "http://localhost:9000/"
    API_PORT = "9000"
  }
  allow_unauthenticated = true
}

module "github_wif" {
  source      = "../../modules/workload_identity_github"
  project_id  = var.project_id
  github_repo = var.github_repo
}
