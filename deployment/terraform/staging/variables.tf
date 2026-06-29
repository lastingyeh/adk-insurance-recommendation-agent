# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

variable "project_name" {
  type        = string
  description = "Project name used as a base for resource naming"
  default     = "insurance-agent"
}

variable "project_id" {
  type        = string
  description = "Google Cloud Project ID for resource deployment."
}

variable "region" {
  type        = string
  description = "Google Cloud region for resource deployment."
  default     = "us-central1"
}

variable "telemetry_logs_filter" {
  type        = string
  description = "Log Sink filter for capturing telemetry data. Captures logs with the `traceloop.association.properties.log_type` attribute set to `tracing`."
  default     = "labels.service_name=\"insurance-agent\" labels.type=\"agent_telemetry\""
}

variable "feedback_logs_filter" {
  type        = string
  description = "Log Sink filter for capturing feedback data. Captures logs where the `log_type` field is `feedback`."
  default     = "jsonPayload.log_type=\"feedback\" jsonPayload.service_name=\"insurance-agent\""
}

variable "model_name" {
  type        = string
  description = "The Gemini model version to use for standard chat interactions."
  default     = "gemini-2.5-flash"
}

variable "live_model_name" {
  type        = string
  description = "The Gemini model version to use for real-time (Live) streaming interactions."
  default     = "gemini-live-2.5-flash-native-audio"
}

variable "app_sa_roles" {
  description = "List of roles to assign to the application service account"
  type        = list(string)
  default = [
    "roles/cloudsql.client",
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
    "roles/storage.admin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
  ]
}

variable "backend_image" {
  type        = string
  description = "Backend container image URL"
  default     = "us-docker.pkg.dev/cloudrun/container/hello:latest"
}

variable "toolbox_image" {
  type        = string
  description = "Toolbox container image URL"
  default     = "us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:1.1.0"
}

variable "frontend_image" {
  type        = string
  description = "Frontend container image URL"
  default     = "us-docker.pkg.dev/cloudrun/container/hello:latest"
}

variable "app_name" {
  type        = string
  description = "Name of the application used in environment variables."
  default     = "app"
}

variable "enable_telemetry" {
  type        = string
  description = "Toggle for enabling cloud tracing and logging (1 for true, 0 for false)."
  default     = "1"
}

variable "enable_audit_log" {
  type        = string
  description = "Toggle for enabling audit logging (1 for true, 0 for false)."
  default     = "1"
}

variable "enable_pii_redaction" {
  type        = string
  description = "Toggle for enabling PII redaction (1 for true, 0 for false)."
  default     = "1"
}

variable "bq_analytics_dataset" {
  type        = string
  description = "BigQuery dataset name for agent analytics."
  default     = "agent_analytics"
}

variable "bq_location" {
  type        = string
  description = "BigQuery dataset location."
  default     = "US"
}


