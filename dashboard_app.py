"""
Part 9: GEO Merchant Dashboard Server
======================================
Serves the minimalistic, modern merchant dashboard and API endpoints
for real-time pipeline interaction.

Run:
  python dashboard_app.py

Then open the URL printed in the terminal -- defaults to
http://localhost:8000 (set DASHBOARD_PORT in .env to change it).
"""

import os
import sys
import json
import re
import uuid
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
# Updated for 13-query discoverability benchmark (9/13 raw vs 11/13 structured)
from pydantic import BaseModel

# Import purchase pipeline logic
from purchase_pipeline import run_purchase, load_feed, FEED_FILE, AUDIT_LOG_FILE
from structure_catalog import extract_structured_data
from trust_scorer import score_product

# Upsell / cross-sell agent (Part 9 bonus) -- deterministic feed.json lookup,
# see upsell_agent.py for the full explanation of how it decides candidates.
from upsell_agent import suggest_upsell_cross_sell, annotate_feed

app = FastAPI(title="GEO Merchant Dashboard")

# Decisions that mean an actual (or pending) purchase is happening -- this is
# the only point in the flow where a cross-sell/upsell suggestion makes sense.
# A BLOCK or NO_MATCH means there's nothing to upsell alongside.
PURCHASE_DECISIONS = {"APPROVE", "HOLD_FOR_APPROVAL"}

DISCOVERABILITY_FILE = "discoverability_results.json"
DISCOVERABILITY_V2_FILE = "discoverability_results_v2.json"

from typing import Optional


class QueryRequest(BaseModel):
    query: str
    forced_product_id: Optional[str] = None


class CustomCatalogRequest(BaseModel):
    raw_text: str


def parse_custom_catalog_text(raw_text):
    """Split pasted listings on a separator line containing only ``---``."""
    chunks = [chunk.strip() for chunk in re.split(r"(?m)^\s*---\s*$", raw_text)]
    products = []
    for chunk in chunks:
        if not chunk:
            continue
        title = next(
            (line.strip() for line in chunk.splitlines() if line.strip()),
            "Untitled listing",
        )
        products.append({
            "id": f"custom_{uuid.uuid4().hex[:10]}",
            "title": title,
            "raw_description": chunk,
        })
    return products


@app.post("/api/geo-ready")
def make_catalog_geo_ready(req: CustomCatalogRequest):
    """Run pasted listings through the existing structuring and trust pipeline.

    Results are returned as an ephemeral preview; this endpoint never adds
    records to the published feed or writes a custom listing to the catalog.
    """
    if not req.raw_text or not req.raw_text.strip():
        raise HTTPException(status_code=400, detail="Paste at least one product listing first.")

    products = parse_custom_catalog_text(req.raw_text)
    if not products:
        raise HTTPException(status_code=400, detail="Could not find any product listings in the pasted text.")
    if len(products) > 10:
        raise HTTPException(
            status_code=400,
            detail="Please paste 10 or fewer listings at a time; each listing makes real LLM calls.",
        )

    results = []
    for product in products:
        try:
            # Tight retry budget for a LIVE request: a human is watching a
            # spinner, and a browser/proxy can drop a long-idle connection
            # well before the batch-script defaults (3 retries x up to ~36s
            # backoff, x2 fallback models) would finish across several
            # products. Fail fast into a visible per-product error instead.
            structured = extract_structured_data(product, retries=1, delay=3)
        except Exception as exc:
            structured = None
            print(f"  Custom catalog extraction failed for {product['id']}: {exc}", flush=True)

        if structured is None:
            results.append({
                "product_id": product["id"],
                "title": product["title"],
                "original_raw_description": product["raw_description"],
                "error": "Structuring failed after multiple attempts. Check the server terminal for the API error.",
            })
            continue

        try:
            trust = score_product(structured, retries=1, delay=3)
        except Exception as exc:
            print(f"  Custom catalog scoring failed for {product['id']}: {exc}", flush=True)
            results.append({
                **structured,
                "error": "Structuring succeeded, but trust scoring failed. Check the server terminal for the API error.",
            })
            continue

        results.append({
            **structured,
            "trust_score": trust.get("trust_score"),
            "trust_summary_reason": trust.get("summary_reason"),
            "claim_evidence_avg": trust.get("claim_evidence_avg"),
            "deterministic_checks": trust.get("deterministic_checks"),
            "claims_evaluated": trust.get("claims_evaluated"),
            "score_reliable": trust.get("score_reliable"),
        })

    return {"count": len(results), "results": results}

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

    # Only compute upsell/cross-sell once we know a purchase actually went
    # through or is pending approval -- never for BLOCK/NO_MATCH, and never
    # by asking an LLM to invent something; it's a lookup over the same feed
    # this request already loaded, reusing the trust scores already on it.
    matched_id = entry.get("matched_product_id")
    if matched_id and entry.get("decision") in PURCHASE_DECISIONS:
        annotated = annotate_feed(feed)
        entry["upsell"] = suggest_upsell_cross_sell(matched_id, annotated)
    else:
        entry["upsell"] = None

    return entry


@app.get("/api/upsell/{product_id}")
def get_upsell(product_id: str):
    """Standalone lookup, independent of a live query -- lets the frontend
    (e.g. the Feed JSON Inspector) show 'pairs well with' for any product,
    not just the one that was just purchased."""
    feed = load_feed()
    annotated = annotate_feed(feed)
    if product_id not in annotated:
        raise HTTPException(status_code=404, detail=f"{product_id} not found in feed")
    return suggest_upsell_cross_sell(product_id, annotated)

# Serve index.html
@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Dashboard HTML file not found</h1>"

if __name__ == "__main__":
    DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8001"))
    print(f"Starting GEO Merchant Dashboard server at http://localhost:{DASHBOARD_PORT}...")
    # ``reload=True`` spawns a Windows child process. In restricted desktop
    # environments that child can fail before binding the port, leaving the
    # browser with a misleading "Failed to fetch" error.
    uvicorn.run("dashboard_app:app", host="127.0.0.1", port=DASHBOARD_PORT)