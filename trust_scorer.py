"""
Component 3: Trust Scorer  (Build Guide Part 5)

For every structured product, this script:
  1. Sends ALL of a product's claims to the LLM in ONE batched call and asks
     whether the listing actually backs each one up with evidence, or whether
     they are just asserted.
     -> one JSON array: [{claim, score (0-100), reason}] per product.
     Batching is critical: sending one-call-per-claim across 30 products would
     fire 60-90+ rapid API calls and exhaust Groq rate limits before retries
     can recover, causing every claim to silently fall back to a neutral 50.

  2. Runs three deterministic (non-LLM) checks directly in code:
       - is a return policy mentioned?
       - is the price consistent across the listing?
       - is there any contact / business info anywhere in the listing?

  3. Combines the LLM claim-evidence average with the deterministic checks
     into one overall trust_score per product (0-100), weighted 70% claim
     evidence / 30% deterministic checks.

  4. Persists actual LLM errors in the output JSON instead of silently
     falling back to a neutral 50, so failures are visible and auditable.

Output: one JSON file per product in trust_scores/, e.g. trust_scores/prod_001.json
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
OUTPUT_DIR = "trust_scores"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- price-like figures written anywhere in the raw text ---
PRICE_PATTERNS = [
    re.compile(r"₹\s?([\d,]+(?:\.\d+)?)"),
    re.compile(r"([\d,]+(?:\.\d+)?)\s?(?:INR|Rupees|Rs\.?)", re.IGNORECASE),
    re.compile(r"\$\s?([\d,]+(?:\.\d+)?)"),
    re.compile(r"£\s?([\d,]+(?:\.\d+)?)"),
    re.compile(r"€\s?([\d,]+(?:\.\d+)?)"),
]

CONTACT_PATTERN = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+|\b(?:contact us|customer service|customer care|helpline|support@|call us|DM to order|cash on delivery)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

def _strip_think(text):
    """Remove <think>...</think> reasoning blocks from model output."""
    if "</think>" in text:
        return text[text.find("</think>") + len("</think>"):]
    return text


def clean_and_parse_json_object(text):
    """Extract the first complete JSON *object* from text."""
    clean = _strip_think(text)
    start = clean.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found. Snippet: {text[:200]}")
    candidate = clean[start:]
    candidate = re.sub(r",\s*([\]}])", r"\1", candidate)
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(candidate)
    return obj


def clean_and_parse_json_array(text):
    """Extract the first complete JSON *array* from text."""
    clean = _strip_think(text)
    start = clean.find("[")
    if start == -1:
        raise ValueError(f"No JSON array found. Snippet: {text[:200]}")
    candidate = clean[start:]
    candidate = re.sub(r",\s*([\]}])", r"\1", candidate)
    decoder = json.JSONDecoder()
    arr, _ = decoder.raw_decode(candidate)
    if not isinstance(arr, list):
        raise ValueError(f"Parsed value is not a list: {type(arr)}")
    return arr


# ---------------------------------------------------------------------------
# Batched claim scoring — ONE LLM call for all claims of one product
# ---------------------------------------------------------------------------

def score_all_claims(claims, listing_text, product_id, retries=3, delay=10):
    """
    Send all claims for a single product in ONE LLM call.
    Returns a list of {claim, score, reason} dicts.

    Rationale for batching:
      - 30 products × avg 3 claims = ~90 calls if done one-per-claim
      - Groq free-tier rate limit is ~30 req/min; 90 calls in quick succession
        hits the limit hard and causes every retry to fail too
      - One call per product = 30 calls total, well within limits
    """
    if not claims:
        return []

    claims_list = "\n".join(f"{i + 1}. \"{c}\"" for i, c in enumerate(claims))

    prompt = f"""You are a skeptical fact-checker for an e-commerce trust system.

Full product listing text:
\"\"\"{listing_text}\"\"\"

For EACH claim below, evaluate whether the listing provides ACTUAL supporting evidence
(a certification name, a named study, a specific verifiable mechanism, a named ingredient
with a known function, a precise technical spec) — OR whether it is just an unsupported
marketing assertion (vague language, exaggerated promises, nothing backing it up).

Claims to evaluate:
{claims_list}

Output ONLY a valid JSON array — one object per claim in the SAME ORDER as listed above.
Do NOT add any text before or after the array.

[
  {{"claim": "...", "score": <integer 0-100, 0=pure assertion, 100=fully evidenced>, "reason": "<one sentence>"}},
  ...
]
"""
    last_error = None
    for model_to_try in [MODEL_NAME, FALLBACK_MODEL]:
        for attempt in range(retries):
            try:
                completion = client.chat.completions.create(
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a precise, skeptical evidence-checking agent. Output only valid JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    model=model_to_try,
                    max_tokens=1200,
                    temperature=0.1,
                )
                result = clean_and_parse_json_array(completion.choices[0].message.content)

                # Validate: one entry per claim
                if len(result) != len(claims):
                    raise ValueError(
                        f"Expected {len(claims)} claim results, got {len(result)}"
                    )
                # Normalise: ensure every entry has required keys
                validated = []
                for i, entry in enumerate(result):
                    validated.append({
                        "claim": claims[i],
                        "score": int(entry.get("score", 50)),
                        "reason": entry.get("reason", "No reason provided."),
                        "llm_evaluated": True,
                    })
                return validated

            except Exception as e:
                last_error = str(e)
                print(f"    batch-score attempt {attempt + 1} failed for {product_id} with {model_to_try}: {last_error}", flush=True)
                if "rate_limit" in last_error.lower() or "429" in last_error:
                    wait = delay * (attempt + 1) * 2
                    print(f"    rate limit hit — waiting {wait}s before retry...", flush=True)
                    time.sleep(wait)
                elif attempt < retries - 1:
                    time.sleep(delay)

    # All retries exhausted — return explicit failure records (NOT silent neutral 50)
    print(f"    !! All retries failed for {product_id}. Error: {last_error}")
    return [
        {
            "claim": c,
            "score": None,
            "reason": f"LLM evaluation failed after {retries} attempts. Error: {last_error}",
            "llm_evaluated": False,
        }
        for c in claims
    ]


# ---------------------------------------------------------------------------
# Deterministic checks (no LLM)
# ---------------------------------------------------------------------------

def deterministic_checks(structured, raw_description):
    checks = {}

    # 1. Return policy present?
    return_policy = (structured.get("fulfillment_terms") or {}).get(
        "return_policy", "missing"
    )
    checks["return_policy_present"] = (
        bool(return_policy) and return_policy.strip().lower() not in ("missing", "null", "none")
    )

    # 2. Price consistent between structured field and every price-like figure in raw text?
    structured_price = structured.get("price")
    found_prices = set()
    for pattern in PRICE_PATTERNS:
        for m in pattern.findall(raw_description):
            normalized = (m if isinstance(m, str) else m).replace(",", "")
            try:
                found_prices.add(float(normalized))
            except (ValueError, TypeError):
                continue

    if not found_prices:
        price_consistent = structured_price is not None
    elif len(found_prices) > 1:
        # Multiple different price figures — potential inconsistency
        price_consistent = False
    else:
        only_price = next(iter(found_prices))
        price_consistent = structured_price is not None and abs(only_price - float(structured_price)) < 0.02
    checks["price_consistent"] = price_consistent
    checks["prices_found_in_text"] = sorted(found_prices)

    # 3. Any contact / business info anywhere in the listing?
    checks["contact_info_present"] = bool(CONTACT_PATTERN.search(raw_description))

    passed = sum(
        [
            checks["return_policy_present"],
            checks["price_consistent"],
            checks["contact_info_present"],
        ]
    )
    checks["deterministic_score"] = round((passed / 3) * 100)
    return checks


# ---------------------------------------------------------------------------
# Per-product scoring
# ---------------------------------------------------------------------------

def score_product(structured):
    product_id = structured["product_id"]
    title = structured.get("original_title", "")
    raw_description = structured.get("original_raw_description", "")
    claims = structured.get("claims_made") or []

    print(f"  Scoring {product_id}: '{title}' ({len(claims)} claim(s))...")

    # --- Batched LLM evaluation ---
    claims_evaluated = score_all_claims(claims, raw_description, product_id)

    # Compute average only over claims that were actually evaluated
    evaluated_scores = [
        c["score"] for c in claims_evaluated if c.get("llm_evaluated") and c["score"] is not None
    ]
    failed_count = len(claims) - len(evaluated_scores)

    if evaluated_scores:
        claim_evidence_avg = round(sum(evaluated_scores) / len(evaluated_scores))
    elif not claims:
        claim_evidence_avg = 100  # no claims → nothing unsupported
    else:
        claim_evidence_avg = None  # all calls failed — do NOT fabricate a number

    # --- Deterministic checks ---
    det = deterministic_checks(structured, raw_description)

    # --- Combined trust score ---
    if claim_evidence_avg is not None:
        trust_score = round(0.7 * claim_evidence_avg + 0.3 * det["deterministic_score"])
        score_reliable = failed_count == 0
    else:
        # Claim scoring completely failed; fall back to deterministic only
        trust_score = det["deterministic_score"]
        score_reliable = False

    # --- Human-readable summary ---
    summary_bits = []
    if failed_count > 0:
        summary_bits.append(
            f"WARNING: {failed_count}/{len(claims)} claim(s) could not be LLM-evaluated — "
            f"score may be unreliable."
        )
    if evaluated_scores:
        weakest = min(claims_evaluated, key=lambda c: (c["score"] if c["score"] is not None else 999))
        if weakest.get("score") is not None and weakest["score"] < 50:
            summary_bits.append(
                f"Weakest claim: \"{weakest['claim']}\" — {weakest['reason']}"
            )
    if not det["return_policy_present"]:
        summary_bits.append("No return policy stated.")
    if not det["price_consistent"]:
        summary_bits.append("Price inconsistent or missing across the listing.")
    if not det["contact_info_present"]:
        summary_bits.append("No contact/business info found.")
    summary_reason = (
        " ".join(summary_bits)
        if summary_bits
        else "Claims are evidence-backed and listing basics are present."
    )

    return {
        "product_id": product_id,
        "title": title,
        "claims_evaluated": claims_evaluated,
        "claim_evidence_avg": claim_evidence_avg,
        "failed_claim_evaluations": failed_count,
        "score_reliable": score_reliable,
        "deterministic_checks": det,
        "trust_score": trust_score,
        "summary_reason": summary_reason,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not os.path.isdir(STRUCTURED_DIR):
        print(
            f"Error: {STRUCTURED_DIR}/ not found. Run structure_catalog.py (Part 3) first."
        )
        sys.exit(1)

    files = sorted(f for f in os.listdir(STRUCTURED_DIR) if f.endswith(".json"))
    if not files:
        print(f"Error: no structured product files found in {STRUCTURED_DIR}/.")
        sys.exit(1)

    print(f"Found {len(files)} structured products. Beginning trust scoring...\n")
    print("  Strategy: ONE batched LLM call per product (not one-per-claim).")
    print("  This keeps total API calls ≤ number of products, avoiding rate limits.\n")

    results = []
    for fname in files:
        with open(os.path.join(STRUCTURED_DIR, fname), "r", encoding="utf-8") as f:
            structured = json.load(f)

        out_path = os.path.join(OUTPUT_DIR, fname)
        if os.path.exists(out_path):
            print(f"  Skipping {structured['product_id']}: already scored.")
            with open(out_path, "r", encoding="utf-8") as f:
                results.append(json.load(f))
            continue

        result = score_product(structured)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=4, ensure_ascii=False)

        ts_display = str(result["trust_score"]) if result["trust_score"] is not None else "N/A"
        reliable = "" if result["score_reliable"] else " ⚠ (score unreliable — some claims failed)"
        print(f"    -> trust_score={ts_display}{reliable}  saved to {out_path}\n", flush=True)
        results.append(result)

        # Pause between products to respect Groq rate limits
        time.sleep(2)

    print("=" * 65)
    print("Trust Scoring Complete!")
    print(f"{'PRODUCT':<12} {'TRUST':>5}  {'RELIABLE':>8}  TITLE")
    print("-" * 65)
    for r in sorted(results, key=lambda r: (r["trust_score"] or 0)):
        ts = str(r["trust_score"]) if r["trust_score"] is not None else "N/A"
        reliable = "yes" if r.get("score_reliable") else "NO ⚠"
        print(f"  {r['product_id']:<10} {ts:>5}  {reliable:>8}  {r['title']}")
    print("=" * 65)


if __name__ == "__main__":
    main()