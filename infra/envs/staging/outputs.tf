output "api_url" {
  value       = module.api.url
  description = "Public staging API base URL (set as EXPO_PUBLIC_API_URL)."
}

output "worker_url" {
  value       = module.worker.url
  description = "Internal worker URL (Cloud Tasks push target)."
  sensitive   = true
}

output "cobalt_url" {
  value       = module.cobalt.url
  description = "Staging Cobalt extractor URL (COBALT_BASE_URL for workers)."
  sensitive   = true
}

output "wif_provider" {
  value       = module.github_wif.provider_name
  description = "Set as repo variable GCP_WIF_PROVIDER for CI plans."
}

output "tf_deployer_email" {
  value       = module.github_wif.deployer_email
  description = "Set as repo variable GCP_TF_SERVICE_ACCOUNT for CI plans."
}
