output "repository" {
  # Docker pull/push path (the resource `name` is a projects/... string,
  # NOT a valid image reference — do not use it for images).
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
  description = "Docker path prefix for image pushes (region-docker.pkg.dev/...)."
}
