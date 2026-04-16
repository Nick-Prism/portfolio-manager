#!/bin/bash
echo "Deploying Zeta..."
source scripts/load_secrets.sh
docker compose up -d --build
sleep 5
docker compose ps
echo " Done — dashboard at http://localhost:8501"