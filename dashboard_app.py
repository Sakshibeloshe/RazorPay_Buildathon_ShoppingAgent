"""
Part 9: GEO Merchant Dashboard Server
======================================
Serves the minimalistic, modern merchant dashboard and API endpoints
for real-time pipeline interaction.

Run:
  python dashboard_app.py

Then open http://localhost:8000 in your browser.
"""

import os
import sys
import json
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
# Updated for 13-query discoverability benchmark (9/13 raw vs 11/13 structured)
from pydantic import BaseModel

# Import purchase pipeline logic
from purchase_pipeline import run_purchase, load_feed, FEED_FILE, AUDIT_LOG_FILE

app = FastAPI(title="GEO Merchant Dashboard")

DISCOVERABILITY_FILE = "discoverability_results.json"
DISCOVERABILITY_V2_FILE = "discoverability_results_v2.json"

from typing import Optional


class QueryRequest(BaseModel):
    query: str
    forced_product_id: Optional[str] = None

@app.get("/api/feed")
def get_feed():
    if not os.path.exists(FEED_FILE):
        return []
    with open(FEED_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

@app.get("/api/audit-log")
def get_audit_log():
    if not os.path.exists(AUDIT_LOG_FILE):
        return []
    with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return []

@app.get("/api/discoverability")
def get_discoverability():
    target_file = DISCOVERABILITY_V2_FILE if os.path.exists(DISCOVERABILITY_V2_FILE) else DISCOVERABILITY_FILE
    if not os.path.exists(target_file):
        return {}
    with open(target_file, "r", encoding="utf-8") as f:
        return json.load(f)

@app.post("/api/query")
def execute_query(req: QueryRequest):
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="Query string cannot be empty")
    feed = load_feed()
    entry = run_purchase(req.query.strip(), feed, forced_product_id=req.forced_product_id)
    return entry

# Serve index.html
@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Dashboard HTML file not found</h1>"

if __name__ == "__main__":
    print("Starting GEO Merchant Dashboard server at http://localhost:8000...")
    uvicorn.run("dashboard_app:app", host="127.0.0.1", port=8000, reload=True)