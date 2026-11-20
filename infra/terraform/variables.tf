variable "project_id" {
  description = "Google Cloud project ID"
  type        = string
}

variable "region" {
  description = "Primary Google Cloud region (e.g. us-central1)"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment name (staging or production)"
  type        = string
  default     = "production"
}

variable "backend_image" {
  description = "Immutable Artifact Registry image URI for devdna-backend"
  type        = string
}

variable "ast_engine_image" {
  description = "Immutable Artifact Registry image URI for devdna-ast-engine"
  type        = string
}

variable "cors_origins" {
  description = "Comma-separated allowed frontend HTTPS origins"
  type        = string
}

variable "sql_tier" {
  description = "Cloud SQL PostgreSQL machine tier (cost-controlled default: db-f1-micro)"
  type        = string
  default     = "db-f1-micro"
}

variable "deletion_protection" {
  description = "Prevent accidental Cloud SQL instance deletion"
  type        = bool
  default     = true
}
