"""
db/client.py — Single MongoDB connection for the entire Zeta system.

Everyone imports `db` from here instead of creating their own connection.

Usage:
    from db.client import db
    result = await db.decisions.find_one({"symbol": "HDFCBANK"})
"""

import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise EnvironmentError("MONGODB_URI not set. Check your .env file.")

# Single client instance reused across the app
_client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=8000)

# The Zeta database — has 3 collections: decisions, gtt_tracker, risk_budget
db = _client["zeta"]

# Convenience references so callers can do: from db.client import decisions_col
decisions_col   = db["decisions"]
gtt_col         = db["gtt_tracker"]
risk_budget_col = db["risk_budget"]