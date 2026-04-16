#!/bin/bash
gcloud compute firewall-rules create zeta-allow-ssh \
    --allow=tcp:22 --target-tags=zeta-server \
    --description="SSH only inbound"
gcloud compute firewall-rules create zeta-allow-streamlit \
    --allow=tcp:8501 --target-tags=zeta-server \
    --source-ranges=0.0.0.0/0 \
    --description="Streamlit dashboard for demo"
echo "✅ Firewall rules created"
gcloud compute firewall-rules list --filter="name~zeta"