"""
Part 6: Publish the Feed
========================
Build Guide reference: Part 6

Merges the three upstream outputs into one agent-readable catalog feed:
  - structured_catalog/<id>.json   (Part 3 — fields, attributes, price, fulfillment)
  - trust_scores/<id>.json         (Part 5 — trust_score, claims_evaluated, summary_reason)
  - intent_coverage                (Part 4 — generated here for any product that doesn't
                                    already have it attached to its structured record)

Output: feed.json — an array of fully-enriched product records, one per product.

This is the single artifact downstream scripts (Part 7 discoverability test,
Part 8 buyer agent + gate) consume.  Every field that Part 8 will act on
(trust_score, intent_coverage, price, availability, fulfillment_terms) is
present in this one file.

Intent coverage is generated on-the-fly here because no separate
map_intent.py script was written during Parts 3–5.  The generation prompt
exactly matches the build-guide spec: given the structured record, ask the
LLM whether 2–3 realistic shopper phrasings are covered, and record which
are and aren't answered.
"""

import os
import sys
import json
import time
import re
from dotenv import load_dotenv
from groq import Groq

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

load_dotenv()
api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    print("Error: GROQ_API_KEY not found in .env file.")
    sys.exit(1)

client = Groq(api_key=api_key)
MODEL_NAME = "qwen/qwen3.8-27b"
FALLBACK_MODEL = "openai/gpt-oss-20b"

STRUCTURED_DIR = "structured_catalog"
TRUST_DIR = "trust_scores"
OUTPUT_FILE = "feed.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_and_parse_json(text):
    """Strip <think>…</think> reasoning blocks then extract the first JSON object."""
    clean = text
    if "</think>" in text:
        clean = text[text.find("</think>") + len("</think>"):]
    start = clean.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found. Snippet: {text[:200]}")
    candidate = clean[start:]
    candidate = re.sub(r",\s*([\]}])", r"\1", candidate)
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(candidate)
    return obj


def generate_intent_coverage(structured, retries=3, delay=5):
    """
    Ask the LLM for 3 realistic shopper phrasings for this product, then
    check whether the structured record answers each framing.

    Returns:
        {
          "phrasings": [
            {"query": "...", "covered": true/false, "gap": "..." or null},
            ...
          ],
          "covered_count": int,
          "total_count": int,
          "coverage_pct": int   # 0-100
        }
    """
    title = structured.get("original_title", "")
    category = structured.get("category", "")
    key_attributes = structured.get("key_attributes", [])
    claims = structured.get("claims_made", [])
    audience = structured.get("target_audience", "")

    record_summary = json.dumps({
        "title": title,
        "category": category,
        "target_audience": audience,
        "key_attributes": key_attributes,
        "claims_made": claims,
        "price": structured.get("price"),
        "availability": structured.get("availability"),
        "fulfillment_terms": structured.get("fulfillment_terms"),
    }, ensure_ascii=False)

    prompt = f"""You are an expert in GEO (Generative Engine Optimization) — the AI-era equivalent of SEO.

Here is a structured product record:
{record_summary}

Step 1: Write exactly 3 realistic, natural-language shopping queries that a real person
might type into an AI assistant when looking for a product like this.
Each query should use DIFFERENT phrasing / intent angle (e.g. one benefit-focused,
one attribute-focused, one use-case-focused).

Step 2: For each query, decide:
  - "covered": true if the structured record contains enough information to confidently
    answer that query framing, false if important information is missing.
  - "gap": if covered is false, a one-sentence description of what's missing; null if covered.

Output ONLY valid JSON, no markdown, no preamble:
{{
  "phrasings": [
    {{"query": "...", "covered": true, "gap": null}},
    {{"query": "...", "covered": false, "gap": "Missing X"}},
    {{"query": "...", "covered": true, "gap": null}}
  ]
}}
"""
    for model_to_try in [MODEL_NAME, FALLBACK_MODEL]:
        for attempt in range(retries):
            try:
                completion = client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": "You are a precise GEO analyst. Output only valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    model=model_to_try,
                    max_tokens=600,
                    temperature=0.2,
                )
                result = clean_and_parse_json(completion.choices[0].message.content)
                phrasings = result.get("phrasings", [])
                covered_count = sum(1 for p in phrasings if p.get("covered", False))
                total = len(phrasings)
                return {
                    "phrasings": phrasings,
                    "covered_count": covered_count,
                    "total_count": total,
                    "coverage_pct": round((covered_count / total) * 100) if total else 0,
                }
            except Exception as e:
                msg = str(e)
                print(f"    intent-coverage attempt {attempt + 1} failed with {model_to_try}: {msg}", flush=True)
                if "rate_limit" in msg.lower() or "429" in msg:
                    time.sleep(delay * (attempt + 1) * 2)
                elif attempt < retries - 1:
                    time.sleep(delay)
            else:
                return {
                    "phrasings": [],
                    "covered_count": 0,
                    "total_count": 0,
                    "coverage_pct": 0,
                    "error": "Could not generate intent coverage (LLM call failed).",
                }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not os.path.isdir(STRUCTURED_DIR):
        print(f"Error: {STRUCTURED_DIR}/ not found. Run structure_catalog.py (Part 3) first.")
        sys.exit(1)
    if not os.path.isdir(TRUST_DIR):
        print(f"Error: {TRUST_DIR}/ not found. Run trust_scorer.py (Part 5) first.")
        sys.exit(1)

    # Load all structured records
    structured_map = {}
    for fname in os.listdir(STRUCTURED_DIR):
        if fname.endswith(".json"):
            path = os.path.join(STRUCTURED_DIR, fname)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            structured_map[data["product_id"]] = data

    # Load all trust scores
    trust_map = {}
    for fname in os.listdir(TRUST_DIR):
        if fname.endswith(".json"):
            path = os.path.join(TRUST_DIR, fname)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            trust_map[data["product_id"]] = data

    product_ids = sorted(structured_map.keys())
    print(f"Found {len(structured_map)} structured records, {len(trust_map)} trust scores.")
    print(f"Building feed.json for {len(product_ids)} products...\n")

    feed = []
    for pid in product_ids:
        structured = structured_map[pid]
        trust = trust_map.get(pid, {})

        title = structured.get("original_title", pid)

        # --- Intent Coverage (Part 4) ---
        # Check if intent_coverage was already attached during structuring;
        # if not, generate it now.
        if "intent_coverage" in structured:
            intent_coverage = structured["intent_coverage"]
            print(f"  {pid}: using existing intent_coverage.")
        else:
            print(f"  {pid}: generating intent_coverage for '{title}'...")
            intent_coverage = generate_intent_coverage(structured)
            covered = intent_coverage.get("covered_count", 0)
            total = intent_coverage.get("total_count", 0)
            print(f"    -> {covered}/{total} phrasings covered ({intent_coverage.get('coverage_pct', 0)}%)")
            time.sleep(2)  # rate-limit guard

        # --- Merge into one record ---
        record = {
            # Identity
            "product_id": pid,
            "title": title,

            # Part 3 — structured fields
            "category": structured.get("category"),
            "target_audience": structured.get("target_audience"),
            "key_attributes": structured.get("key_attributes", []),
            "claims_made": structured.get("claims_made", []),
            "price": structured.get("price"),
            "price_currency": structured.get("price_currency", "INR"),  # Root cause #1 fix
            "availability": structured.get("availability"),
            "fulfillment_terms": structured.get("fulfillment_terms", {}),

            # Part 4 — intent coverage
            "intent_coverage": intent_coverage,

            # Part 5 — trust score
            "trust_score": trust.get("trust_score"),
            "trust_summary_reason": trust.get("summary_reason"),
            "claim_evidence_avg": trust.get("claim_evidence_avg"),
            "deterministic_checks": trust.get("deterministic_checks", {}),
            "claims_evaluated": trust.get("claims_evaluated", []),

            # Raw description kept for Part 7 (before/after test)
            "original_raw_description": structured.get("original_raw_description", ""),
        }

        feed.append(record)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(feed, f, indent=4, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"Feed published! {len(feed)} products written to {OUTPUT_FILE}")
    print("=" * 60)
    print(f"\n{'PRODUCT':<12} {'TRUST':>5}  {'INTENT COV':>10}  TITLE")
    print("-" * 70)
    for r in sorted(feed, key=lambda x: (x["trust_score"] or 0), reverse=True):
        cov = r["intent_coverage"].get("coverage_pct", "?")
        ts = r["trust_score"] if r["trust_score"] is not None else "N/A"
        print(f"  {r['product_id']:<10} {str(ts):>5}  {str(cov) + '%':>10}  {r['title']}")
    print("=" * 60)
    print(f"\nThis feed.json is the input for Part 7 (discoverability test)")
    print(f"and Part 8 (buyer agent + gate + Razorpay).")


if __name__ == "__main__":
    main()
