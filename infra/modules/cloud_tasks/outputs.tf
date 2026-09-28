output "queue_names" {
  value = [
    google_cloud_tasks_queue.ingest.name,
    google_cloud_tasks_queue.import.name,
    google_cloud_tasks_queue.enhance.name,
  ]
  description = "Pipeline queue names."
}
