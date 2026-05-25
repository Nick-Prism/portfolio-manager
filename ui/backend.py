import os
from fastapi import FastAPI
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

_mongo_uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
_client = MongoClient(_mongo_uri) if _mongo_uri else None
_db = _client["zeta"] if _client else None


@app.post("/request-approval")
def request_approval(data: dict):
    if _db is None:
        return {"error": "Database not configured"}
    request_id = str(data["id"])
    _db.requests.insert_one({
        "request_id": request_id,
        "status": "pending",
        "data": data,
    })
    return {"message": "Approval requested"}
