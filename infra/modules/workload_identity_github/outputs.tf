output "provider_name" {
  value       = google_iam_workload_identity_pool_provider.github.name
  description = "Set as repo variable GCP_WIF_PROVIDER for CI."
}

output "deployer_email" {
  value       = google_service_account.tf_deployer.email
  description = "Set as repo variable GCP_TF_SERVICE_ACCOUNT for CI."
}
