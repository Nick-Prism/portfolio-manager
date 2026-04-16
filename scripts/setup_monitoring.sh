#!/bin/bash
gcloud services enable monitoring.googleapis.com
gcloud services enable logging.googleapis.com
echo "✅ Cloud Logging and Monitoring enabled"
echo "Logs: https://console.cloud.google.com/logs"
echo "Monitoring: https://console.cloud.google.com/monitoring"