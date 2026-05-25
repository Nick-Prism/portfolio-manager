from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()

# Support both env var names for compatibility
MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")

client = MongoClient(MONGO_URI) if MONGO_URI else None
db = client["zeta"] if client else None
collection = db["decisions"] if db is not None else None


def get_decisions():
    if collection is None:
        return []
    try:
        return list(collection.find().sort("timestamp", -1).limit(50))
    except Exception as e:
        print(f"Database error: {e}")
        return []


def update_decision_status(symbol, status):
    if collection is None:
        return
    collection.update_one(
        {"symbol": symbol, "approved": None},
        {"$set": {"approved": status}}
    )
