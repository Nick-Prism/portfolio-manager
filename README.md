# Zeta — Person 2: Agent Engine

## What's in here

```
agents/
  graph.py          LangGraph directed async graph — parallel fan-out + orchestrator
  technical.py      RSI, MACD, Bollinger, EMA, ATR, VWAP via pandas-ta + LLM interpretation
  fundamental.py    PE, ROE, D/E scraper (Screener.in) + LLM verdict
  sentiment.py      NLP scoring over news articles via LLM
  risk.py           Beta, VaR 95%, volatility, max drawdown + LLM risk level
  debate.py         Bull / Bear sub-agents (two independent LLM calls)
  orchestrator.py   Final HOLD / SELL / GTT_TARGET / GTT_STOP / ABSTAIN decision

llm/
  router.py         LiteLLM provider router — Gemini Flash → Groq Llama 3.3 → Claude Haiku
  schemas.py        Pydantic v2 typed output schemas for every agent

main.py             Entry point — mock portfolio, scheduler, --run-once flag
requirements.txt
```

## Local setup

```bash
# 1. Create venv
python -m venv .venv && source .venv/bin/activate

# 2. Install deps
pip install -r requirements.txt

# 3. Set at least one LLM API key
export GEMINI_API_KEY=your_key_here
# or: export GROQ_API_KEY=...
# or: export ANTHROPIC_API_KEY=...

# 4. Run a single analysis cycle (mock portfolio, prints to stdout)
python main.py --run-once

# 5. Analyse one stock
python main.py --symbol HDFCBANK --run-once

# 6. Continuous mode (30-min cycles)
python main.py --interval 30
```

## No API key? No problem

If no LLM API key is set, `llm/router.py` returns stub responses so you can
test the full pipeline structure (graph wiring, data flow, schema validation)
without any external calls.

## Integration points for other team members

### P3 (Data & DB)
- In `main.py → analyse_holding()`, replace `fundamentals=None` and `articles=None`
  with calls to your `data/fetchers.py` and `data/news.py`
- Uncomment the MongoDB write block in `_output_result()` once `db/client.py` is ready

### P1 (Infrastructure)
- In `main.py → _get_holdings()`, uncomment the Zerodha MCP import once
  `mcp/zerodha_mcp.py` is ready

### P4 (Dashboard)
- `CycleResult` (from `llm/schemas.py`) is the document P3 writes to MongoDB
  and P4 reads from the `decisions` collection — the schema is your contract
