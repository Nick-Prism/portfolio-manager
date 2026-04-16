# Zeta — AI Portfolio Manager

Autonomous AI stock portfolio analyser for Indian retail investors on Zerodha.

## Team
| Person | Role |
|--------|------|
| Person 1 | Infrastructure & Cloud |
| Person 2 | Agent Engine & LangGraph |
| Person 3 | Data Ingestion & MongoDB |
| Person 4 | Dashboard & Telegram Bot |

## Deployment
```bash
git clone https://github.com/Nick-Prism/portfolio-manager.git zeta
cd zeta
source scripts/load_secrets.sh
docker compose up -d --build
docker compose ps
```

## Tech Stack
- GCP Compute Engine + Secret Manager + Cloud Logging
- Docker + Docker Compose
- LangGraph + LiteLLM
- MongoDB Atlas + Redis
- Streamlit + Plotly
- Zerodha Kite Connect via MCP
- Telegram Bot
