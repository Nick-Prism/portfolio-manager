"""
database/db/client.py — Single MongoDB connection for the entire Zeta system.

Everyone imports `db` from here instead of creating their own connection.

Usage:
    from database.db.client import db
    result = await db.decisions.find_one({"symbol": "HDFCBANK"})
"""

import os
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MONGODB_URI = os.getenv("MONGODB_URI")

if MONGODB_URI:
    from motor.motor_asyncio import AsyncIOMotorClient
    _client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
    db = _client["zeta"]
else:
    logging.getLogger(__name__).warning(
        "MONGODB_URI not set — database layer disabled. Set it in .env to enable MongoDB."
    )
    db = None

# Convenience references — will be None if MONGODB_URI is not set
decisions_col    = db["decisions"]    if db is not None else None
gtt_col          = db["gtt_tracker"]  if db is not None else None
risk_budget_col  = db["risk_budget"]  if db is not None else None
system_state_col = db["system_state"] if db is not None else None
logs_col         = db["logs"]         if db is not None else None


def get_db():
    """Return the motor database instance (or None if MONGODB_URI not set)."""
    return db
