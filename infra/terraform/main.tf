terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  prefix = "devdna-${var.environment}"
  apis = [
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "storage.googleapis.com",
    "cloudtasks.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# --- Container Registry ---
resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = "devdna"
  format        = "DOCKER"
  description   = "DevDNA production container images"
  depends_on    = [google_project_service.enabled]
}

# --- Least-Privilege Service Account ---
resource "google_service_account" "backend" {
  account_id   = "${local.prefix}-backend-sa"
  display_name = "DevDNA Backend Cloud Run Service Account (${var.environment})"
}

resource "google_project_iam_member" "backend_roles" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/secretmanager.secretAccessor",
    "roles/storage.objectAdmin",
    "roles/cloudtasks.enqueuer",
    "roles/logging.logWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.backend.email}"
}

# --- Cloud Storage (private, uniform access, 30-day artifact lifecycle) ---
resource "google_storage_bucket" "artifacts" {
  name                        = "${var.project_id}-${local.prefix}-artifacts"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.enabled]
}

# --- Cloud SQL PostgreSQL 16 (with automated backups + deletion protection) ---
resource "google_sql_database_instance" "postgres" {
  name                = "${local.prefix}-pg"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = var.deletion_protection

  settings {
    tier              = var.sql_tier
    disk_size         = 10
    disk_autoresize   = true
    availability_type = "ZONAL"

    backup_configuration {
      enabled                        = true
      start_time                     = "03:00"
      point_in_time_recovery_enabled = true
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_sql_database" "devdna" {
  name     = "devdna"
  instance = google_sql_database_instance.postgres.name
}

# --- Secret Manager Shells (populate secret versions out-of-band; never commit values) ---
resource "google_secret_manager_secret" "secrets" {
  for_each  = toset(["devdna-api-token", "devdna-database-url", "devdna-gemini-api-key", "devdna-worker-secret"])
  secret_id = "${each.value}-${var.environment}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

# --- Cloud Tasks Durable Queue ---
resource "google_cloud_tasks_queue" "analysis" {
  name     = "${local.prefix}-analysis-queue"
  location = var.region

  rate_limits {
    max_dispatches_per_second = 2
    max_concurrent_dispatches = 4
  }

  retry_config {
    max_attempts       = 3
    min_backoff        = "10s"
    max_backoff        = "120s"
    max_retry_duration = "600s"
  }

  depends_on = [google_project_service.enabled]
}

# --- Cloud Run v2: Backend API (untrusted experiments disabled fail-closed) ---
resource "google_cloud_run_v2_service" "backend" {
  name     = "${local.prefix}-backend"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.backend.email
    timeout         = "300s"

    scaling {
      min_instance_count = 0
      max_instance_count = 4
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.postgres.connection_name]
      }
    }

    containers {
      image = var.backend_image

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "DEVDNA_EXPERIMENTS_ENABLED"
        value = "false"
      }
      env {
        name  = "DEVDNA_LOG_FORMAT"
        value = "json"
      }
      env {
        name  = "DEVDNA_CORS_ORIGINS"
        value = var.cors_origins
      }
      env {
        name  = "DEVDNA_GCS_BUCKET"
        value = google_storage_bucket.artifacts.name
      }
      env {
        name  = "DEVDNA_CLOUD_TASKS_QUEUE"
        value = google_cloud_tasks_queue.analysis.id
      }
      env {
        name = "DEVDNA_API_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["devdna-api-token"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "DEVDNA_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["devdna-database-url"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["devdna-gemini-api-key"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "DEVDNA_WORKER_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["devdna-worker-secret"].secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get { path = "/api/health" }
        initial_delay_seconds = 5
        period_seconds        = 10
        failure_threshold     = 3
      }
      liveness_probe {
        http_get { path = "/api/ready" }
        period_seconds    = 30
        failure_threshold = 3
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

# --- Cloud Run v2: AST Engine ---
resource "google_cloud_run_v2_service" "ast_engine" {
  name     = "${local.prefix}-ast-engine"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    timeout = "60s"

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.ast_engine_image

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "AST_ENGINE_CORS_ORIGINS"
        value = var.cors_origins
      }
      env {
        name = "AST_ENGINE_API_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["devdna-api-token"].secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get { path = "/health" }
        initial_delay_seconds = 3
        period_seconds        = 10
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

# --- Cloud Monitoring: Backend Health Uptime Check ---
resource "google_monitoring_uptime_check_config" "backend_health" {
  display_name = "${local.prefix}-backend-health"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = "/api/ready"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = replace(google_cloud_run_v2_service.backend.uri, "https://", "")
    }
  }
}
