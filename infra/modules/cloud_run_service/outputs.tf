output "url" {
  value       = google_cloud_run_v2_service.service.uri
  description = "HTTPS URL of the service."
}

output "name" {
  value       = google_cloud_run_v2_service.service.name
  description = "Service name."
}
