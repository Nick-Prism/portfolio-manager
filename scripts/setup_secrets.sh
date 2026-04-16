#!/bin/bash
PROJECT_ID=$(gcloud config get-value project)
gcloud services enable secretmanager.googleapis.com

read -p "Enter ZERODHA_API_KEY: " ZERODHA_API_KEY
echo -n "$ZERODHA_API_KEY" | gcloud secrets create ZERODHA_API_KEY --data-file=-

read -p "Enter ZERODHA_API_SECRET: " ZERODHA_API_SECRET
echo -n "$ZERODHA_API_SECRET" | gcloud secrets create ZERODHA_API_SECRET --data-file=-

read -p "Enter MONGODB_URI: " MONGODB_URI
echo -n "$MONGODB_URI" | gcloud secrets create MONGODB_URI --data-file=-

read -p "Enter TELEGRAM_BOT_TOKEN: " TELEGRAM_BOT_TOKEN
echo -n "$TELEGRAM_BOT_TOKEN" | gcloud secrets create TELEGRAM_BOT_TOKEN --data-file=-

read -p "Enter LLM_API_KEY: " LLM_API_KEY
echo -n "$LLM_API_KEY" | gcloud secrets create LLM_API_KEY --data-file=-

gcloud iam service-accounts create zeta-sa --display-name="Zeta Service Account"
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:zeta-sa@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:zeta-sa@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/logging.logWriter"
echo " Secrets and service account created"