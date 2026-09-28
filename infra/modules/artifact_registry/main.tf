# Docker images for the api + worker Cloud Run services.
# Free tier: 500 MB storage — our two slim images fit comfortably.
resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.repository_id
  format        = "DOCKER"
}
