variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "secret_ids" {
  type        = list(string)
  description = <<-EOT
    Secret shells to create WITHOUT values. Values are added by hand
    (gcloud secrets versions add) after apply, so they never touch
    Terraform state, plan output, or this repo.
  EOT
  default = [
    "SUPABASE_SECRET_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "COBALT_API_KEY",
    "SENTRY_DSN",
  ]
}
