# Lets GitHub Actions authenticate to GCP WITHOUT long-lived keys.
# The CI plan job mints a short-lived OIDC token that impersonates the
# deployer service account below. Manual step 0 (human, once): apply
# this module with owner credentials, then store the outputs as repo
# variables GCP_WIF_PROVIDER + GCP_TF_SERVICE_ACCOUNT.
resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = var.pool_id
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  attribute_condition = "assertion.repository == '${var.github_repo}'"
}

resource "google_service_account" "tf_deployer" {
  project      = var.project_id
  account_id   = "terraform-deployer"
  display_name = "Terraform deployer (GitHub Actions impersonation only)"
}

resource "google_service_account_iam_member" "wif_impersonation" {
  service_account_id = google_service_account.tf_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

resource "google_project_iam_member" "deployer_roles" {
  for_each = toset(var.deployer_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.tf_deployer.email}"
}
