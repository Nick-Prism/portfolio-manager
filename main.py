import asyncio
import argparse
import logging
import os

try:
    import google.cloud.logging
    gcp_client = google.cloud.logging.Client()
    gcp_client.setup_logging()
except Exception:
    logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("zeta")

async def run_cycle():
    logger.info("Zeta analysis cycle started")
    try:
        from mcp.tools import get_holdings
        holdings = get_holdings()
        logger.info(f"Fetched {len(holdings)} holdings from Zerodha")
        # from agents.graph import run_graph
        # results = await run_graph(holdings)
        # from db.client import save_decision
        # await save_decision(results)
        logger.info("Zeta analysis cycle complete")
    except Exception as e:
        logger.error(f"Cycle failed: {e}", exc_info=True)

def main():
    parser = argparse.ArgumentParser(description="Zeta AI Portfolio Manager")
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    if args.run_once:
        asyncio.run(run_cycle())
    else:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        scheduler.add_job(run_cycle, "interval", minutes=args.interval)
        scheduler.start()
        logger.info(f"Scheduler started — every {args.interval} minutes")
        try:
            asyncio.get_event_loop().run_forever()
        except KeyboardInterrupt:
            scheduler.shutdown()

if __name__ == "__main__":
    main()