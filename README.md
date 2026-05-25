# Zeta AI Portfolio Manager

An autonomous trading system that analyzes your Indian stock portfolio every 30 minutes during market hours and pushes actionable decisions to Telegram. Built with LangGraph, Claude Sonnet 4, and real-time data from Zerodha/Upstox.

## What it does

Zeta runs five parallel AI agents on each stock you hold:

- **Technical**: RSI, MACD, Bollinger Bands, EMA crossovers, support/resistance levels
- **Fundamental**: P/E, ROE, debt ratios scraped from Screener.in
- **Sentiment**: NLP scoring of news articles from MoneyControl, Economic Times, Zerodha Pulse
- **Risk**: Beta, VaR 95%, volatility, max drawdown, portfolio concentration
- **Arbitrage**: NSE/BSE price spread detection

After the agents finish, a bull/bear debate runs, followed by an orchestrator that makes the final call: **HOLD**, **SELL**, **GTT_STOP**, **GTT_TARGET**, or **ABSTAIN**.

Every decision lands in your Telegram chat with approve/reject buttons. Approve a GTT order, and it gets placed on Zerodha automatically.

## Architecture

Three Docker containers:

1. **agent-engine**: Runs analysis cycles, writes to MongoDB
2. **telegram-bot**: Handles user commands, pushes decisions, places orders
3. **dashboard**: Streamlit UI for viewing decisions and portfolio metrics

MongoDB Atlas stores decisions, system state, and GTT tracking. The system respects kill switches, market hours, and special trading days you configure via Telegram.

## Setup

### Prerequisites

- Docker and docker-compose
- MongoDB Atlas account (free tier works)
- Zerodha account with API access
- At least one LLM API key (Gemini, Groq, Anthropic, OpenAI, Mistral, or Cohere)

### Environment variables

Create a `.env` file:

```bash
# LLM API keys (at least one required)
GEMINI_API_KEY=your_key
GROQ_API_KEY=your_key
ANTHROPIC_API_KEY=your_key
OPENAI_API_KEY=your_key
MISTRAL_API_KEY=your_key
COHERE_API_KEY=your_key

# MongoDB
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/?appName=Cluster0

# Zerodha
ZERODHA_API_KEY=your_api_key
ZERODHA_API_SECRET=your_api_secret
ZERODHA_ACCESS_TOKEN=refreshed_daily_via_telegram
ZERODHA_ENABLE_ORDER_PLACEMENT=false  # Set to true when ready for live trading

# Upstox (optional, used as fallback for price data)
UPSTOX_API_KEY=your_key
UPSTOX_API_SECRET=your_secret
UPSTOX_ACCESS_TOKEN=refreshed_daily

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
CHAT_ID=your_telegram_chat_id

# Agent-engine container flag
SKIP_TELEGRAM_BOT=true
```

### Run it

```bash
docker-compose up -d --build
```

Three containers start:
- agent-engine on port (internal only)
- telegram-bot (internal only)
- dashboard on port 8501

Open your Telegram bot and send `/set_interval` to choose 15, 30, or 60 minutes. The system starts running cycles automatically during market hours (9:15 AM – 3:30 PM IST).

## Daily workflow

1. **Morning**: Send `/refresh_token` to your Telegram bot. Follow the Zerodha login link, paste the redirect URL back.
2. **Set interval**: `/set_interval` → pick 30 minutes (recommended).
3. **Wait**: Decisions arrive in Telegram every 30 minutes.
4. **Approve or reject**: Tap ✅ to approve a GTT order, ❌ to reject.
5. **Track**: Use `/check` to see pending decisions, `/status` for system health.

## Telegram commands

### Daily operations
- `/refresh_token` — Refresh Zerodha access token (expires 6 AM IST daily)
- `/refresh_upstox_token` — Refresh Upstox token
- `/check` — Show pending decisions
- `/status` — System status (interval, cycle count, next run times)

### Trading controls
- `/resume_today` — Override kill day flag
- `/declare_holiday` — Mark today as holiday (no trading)
- `/special_day` — Declare tomorrow as special market day (custom hours)
- `/cancel_special_day` — Cancel special day declaration

### System controls
- `/start_system` — Restart after kill system
- `/set_pullback <pct>` — Set Track & Optimize threshold (default 0.5%)

### Decision workflow
After each cycle, decisions arrive automatically. Each has:
- ✅ **Approve** — Place the GTT order on Zerodha
- ❌ **Reject** — Discard the decision
- ☰ **Menu** — Batch controls, kill options, interval change

## How decisions work

Every decision includes:
- **Symbol** and **exchange**
- **Decision type**: HOLD, SELL, GTT_STOP, GTT_TARGET, ABSTAIN
- **Confidence score**: 0–100%
- **GTT price**: Stop-loss or target price (if applicable)
- **Bull argument**: Why you should hold or buy
- **Bear argument**: Why you should sell or reduce
- **Reasoning**: Final verdict from the orchestrator
- **Technical/Fundamental/Sentiment/Risk signals**: Full breakdown

Decisions are written to MongoDB and pushed to Telegram. You approve or reject. Approved GTT orders get placed via Zerodha API.

## Track & Optimize

After you approve a GTT_TARGET decision, the system tracks the stock's price. If it hits your pullback threshold (default 0.5%) from the peak, it re-analyzes the stock immediately and pushes a new decision.

Example: You approve GTT_TARGET at ₹100. Stock hits ₹105 (5% gain). Then it drops to ₹104.475 (0.5% pullback from ₹105). System re-analyzes and might suggest GTT_STOP to lock in gains.

## Kill switches

- **Kill Day**: Stops all trading for today. Use `/resume_today` to override.
- **Kill System**: Pauses the entire system. Use `/start_system` to restart.

Both are set via Telegram menu buttons. Useful when you want to pause trading without stopping the containers.

## Project structure

```
agents/
  graph.py          LangGraph directed async graph
  technical.py      RSI, MACD, Bollinger, EMA, ATR, VWAP
  fundamental.py    P/E, ROE, D/E scraper (Screener.in)
  sentiment.py      NLP scoring over news articles
  risk.py           Beta, VaR 95%, volatility, max drawdown
  arbitrage.py      NSE/BSE spread detection
  debate.py         Bull/Bear sub-agents
  orchestrator.py   Final decision logic

llm/
  router.py         LiteLLM provider router with fallback chain
  schemas.py        Pydantic v2 typed output schemas

database/
  db/
    client.py       MongoDB connection singleton
    models.py       Pydantic schemas for MongoDB documents
    queries.py      Common queries
  data/
    fetchers.py     Multi-source price/fundamental data
    news.py         News scraping (MoneyControl, ET, Zerodha Pulse)
    indicators.py   Technical indicator calculations

mcp/
  tools.py          Zerodha API wrapper (holdings, orders, GTT)
  upstox_client.py  Upstox API wrapper
  zerodha_login.py  Token refresh script
  upstox_login.py   Upstox token refresh

bot/
  telegram_bot.py   Telegram bot with decision approval workflow

ui/
  app.py            Streamlit dashboard entry point
  pages/            Dashboard pages (analysis, decisions, portfolio, risk)
  utils/db.py       Dashboard MongoDB queries

main.py             Agent-engine entry point
docker-compose.yml  Three-container orchestration
```

## LLM provider fallback

The system tries providers in this order:
1. Gemini Flash 2.0
2. Groq Llama 3.3 70B
3. Claude Haiku 3.5
4. GPT-4o Mini
5. Mistral Small
6. Cohere Command R

If all fail, it returns stub responses so the pipeline doesn't break. Set at least one API key in `.env`.

## Data sources

- **Price data**: Upstox API → NSE scrape → yfinance (fallback chain)
- **Fundamentals**: Screener.in web scraping
- **News**: MoneyControl, Economic Times, Zerodha Pulse RSS feeds
- **Benchmark**: Nifty 50 (^NSEI via yfinance)

## MongoDB collections

- `decisions`: Every decision generated by the system
- `gtt_tracker`: Tracks approved GTT orders and pullback monitoring
- `system_state`: Interval, kill flags, special days, reanalyse queue
- `risk_budget`: Portfolio-level risk limits (future use)
- `logs`: System logs for debugging

## Running a single analysis

Test the system without waiting for scheduled cycles:

```bash
# Analyze entire portfolio once
docker exec portfolio-manager-main_agent-engine_1 python main.py --run-once

# Analyze one stock
docker exec portfolio-manager-main_agent-engine_1 python main.py --symbol HDFCBANK --run-once
```

Decisions print to stdout and get written to MongoDB (but not pushed to Telegram in `--run-once` mode).

## Dashboard

Open `http://your-server-ip:8501` to view:

- **Home**: System status, recent decisions, portfolio summary
- **Analysis**: Technical/fundamental/sentiment/risk breakdown per stock
- **Decisions**: Filterable table of all decisions (approved, pending, rejected)
- **Portfolio**: Holdings, allocation, P&L
- **Risk**: Portfolio-level risk metrics, concentration analysis

## Troubleshooting

### Zerodha token fails
```bash
# Check if token is in MongoDB
docker exec portfolio-manager-main_agent-engine_1 python -c "
import asyncio
from database.db.client import get_db

async def check():
    db = get_db()
    state = await db.system_state.find_one({'_id': 'zeta_state'})
    print('Token:', state.get('zerodha_access_token')[:20] if state else 'NOT FOUND')

asyncio.run(check())
"

# Refresh via Telegram
# Send /refresh_token, follow the flow
```

### Continuous loop not running
```bash
# Check logs
docker-compose logs --tail=50 agent-engine

# Verify interval is set
docker exec portfolio-manager-main_agent-engine_1 python -c "
import asyncio
from database.db.client import get_db

async def check():
    db = get_db()
    state = await db.system_state.find_one({'_id': 'zeta_state'})
    print('Interval:', state.get('interval_minutes') if state else 'NOT SET')

asyncio.run(check())
"

# Restart container
docker-compose restart agent-engine
```

### Decisions not arriving in Telegram
```bash
# Check if decisions are in MongoDB
docker exec portfolio-manager-main_agent-engine_1 python -c "
import asyncio
from database.db.client import get_db

async def check():
    db = get_db()
    count = await db.decisions.count_documents({})
    print(f'Total decisions: {count}')
    
    latest = await db.decisions.find_one(sort=[('timestamp', -1)])
    if latest:
        print(f\"Latest: {latest.get('symbol')} - {latest.get('decision')}\")

asyncio.run(check())
"

# Check Telegram bot logs
docker-compose logs --tail=50 telegram-bot
```

### ContainerConfig error
```bash
# Clean up corrupted metadata
docker-compose down
docker system prune -f
docker-compose up -d
```

## Security notes

- Never commit `.env` to git (already in `.gitignore`)
- Zerodha/Upstox tokens expire daily at 6 AM IST — refresh via Telegram
- Set `ZERODHA_ENABLE_ORDER_PLACEMENT=false` until you're ready for live trading
- MongoDB connection string contains credentials — keep it private
- Telegram bot token gives full control — don't share it

## License

MIT

## Contributing

This is a personal project, but feel free to fork and adapt. If you find bugs or have suggestions, open an issue.

## Disclaimer

This system places real trades on your Zerodha account when you approve decisions. Use at your own risk. Past performance doesn't guarantee future results. The AI agents are not financial advisors. You're responsible for every trade you approve.

Start with `ZERODHA_ENABLE_ORDER_PLACEMENT=false` and paper trade for a few weeks before going live.
