output "backend_url" {
  description = "Cloud Run URL for devdna-backend"
  value       = google_cloud_run_v2_service.backend.uri
}

output "ast_engine_url" {
  description = "Cloud Run URL for devdna-ast-engine"
  value       = google_cloud_run_v2_service.ast_engine.uri
}

output "cloud_sql_connection_name" {
  description = "Cloud SQL connection name for Unix socket mounts"
  value       = google_sql_database_instance.postgres.connection_name
}

output "gcs_artifacts_bucket" {
  description = "Private Cloud Storage bucket for repository archives"
  value       = google_storage_bucket.artifacts.name
}

output "cloud_tasks_queue_id" {
  description = "Cloud Tasks queue resource ID for durable analysis jobs"
  value       = google_cloud_tasks_queue.analysis.id
}
