output "repository" {
  value       = google_artifact_registry_repository.images.name
  description = "Full repository path for image pushes."
}
