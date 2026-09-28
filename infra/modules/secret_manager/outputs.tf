output "secret_ids" {
  value       = [for s in google_secret_manager_secret.shells : s.secret_id]
  description = "Secret ids ready for manual `versions add`."
}
