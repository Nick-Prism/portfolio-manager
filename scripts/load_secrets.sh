#!/bin/bash
echo "Loading secrets from GCP Secret Manager..."
export ZERODHA_API_KEY=$(gcloud secrets versions access latest --secret=ZERODHA_API_KEY)
export ZERODHA_API_SECRET=$(gcloud secrets versions access latest --secret=ZERODHA_API_SECRET)
export MONGODB_URI=$(gcloud secrets versions access latest --secret=MONGODB_URI)
export TELEGRAM_BOT_TOKEN=$(gcloud secrets versions access latest --secret=TELEGRAM_BOT_TOKEN)
export LLM_API_KEY=$(gcloud secrets versions access latest --secret=LLM_API_KEY)
echo "✅ All secrets loaded"