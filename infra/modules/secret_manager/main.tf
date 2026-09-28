# Empty secret shells only — no versions, no values, ever.
resource "google_secret_manager_secret" "shells" {
  for_each  = toset(var.secret_ids)
  project   = var.project_id
  secret_id = each.value

  replication {
    auto {}
  }
}
