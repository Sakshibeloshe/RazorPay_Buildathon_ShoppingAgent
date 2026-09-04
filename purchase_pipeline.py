"""
Part 8: Prove It Works End to End
==================================
Build Guide reference: Part 8 (8a Buyer Agent, 8b Trust-Based Gate,
8c Razorpay Execution, 8d Audit Trail)

Given a shopping query, this pipeline:
  8a. Matches it to a product in feed.json (the buyer agent)
  8b. Gates the purchase using that product's trust_score + a spend limit
      and a currency check (plain if/else — no AI call here on purpose)
  8c. If approved, fires a real Razorpay TEST-MODE order
  8d. Logs every step to audit_log.json, regardless of outcome

Run:
  python purchase_pipeline.py                      -> runs the built-in demo batch
  python purchase_pipeline.py "your query here"     -> runs one query, LLM-matched

Requires in .env:
  GROQ_API_KEY=...
  RAZORPAY_KEY_ID=...
  RAZORPAY_KEY_SECRET=...
"""

import os
import sys
import json
import time
import re
from datetime import datetime, timezone
from dotenv import load_dotenv
from groq import Groq

try:
    import razorpay
except ImportError:
    razorpay = None

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    print("Error: GROQ_API_KEY not found in .env file.")
    sys.exit(1)

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")

groq_client = Groq(api_key=GROQ_API_KEY)
MODEL_NAME = "openai/gpt-oss-20b"
FALLBACK_MODEL = "qwen/qwen3.8-27b"
# Only gpt-oss models get reasoning_effort forced down to "low" -- their own
# default is "medium", which silently eats the whole max_tokens budget on
# hidden reasoning before writing any visible answer (this is exactly why
# typed queries were failing while the forced-ID demo buttons worked fine --
# those never call the LLM matcher at all). qwen/qwen3.8-27b's own default
# is already "none" (cheaper than "low"); forcing "low" onto it makes it
# generate hidden reasoning it otherwise wouldn't and can burn its budget
# too. Never pass reasoning_effort to qwen.
REASONING_EFFORT_BY_MODEL = {
    "openai/gpt-oss-20b": "low",
    "openai/gpt-oss-120b": "low",
}

FEED_FILE = "feed.json"
AUDIT_LOG_FILE = "audit_log.json"

# --- Policy constants (Part 8b) ---
SPEND_LIMIT = 5000          # INR — purchases above this get held, not auto-approved
APPROVE_TRUST_THRESHOLD = 60 # Products with trust >= 60 and price <= SPEND_LIMIT auto-approve
HOLD_TRUST_THRESHOLD = 50
SUPPORTED_CURRENCIES = {"INR"}   # Razorpay test-mode account here only accepts INR


# ---------------------------------------------------------------------------
# Shared: load the feed built in Part 6
# ---------------------------------------------------------------------------

def load_feed():
    if not os.path.exists(FEED_FILE):
        print(f"Error: {FEED_FILE} not found. Run publish_feed.py (Part 6) first.")
        sys.exit(1)
    with open(FEED_FILE, "r", encoding="utf-8") as f:
        records = json.load(f)
    return {r["product_id"]: r for r in records}


# ---------------------------------------------------------------------------
# 8a. The Buyer Agent
# ---------------------------------------------------------------------------

def call_llm(prompt, max_tokens=900, retries=3, delay=5):
    """Shared LLM caller with reasoning-model-safe settings and fallback."""
    for model_to_try in [MODEL_NAME, FALLBACK_MODEL]:
        for attempt in range(retries):
            try:
                kwargs = dict(
                    messages=[{"role": "user", "content": prompt}],
                    model=model_to_try,
                    max_tokens=max_tokens,
                    temperature=0.2,
                )
                effort = REASONING_EFFORT_BY_MODEL.get(model_to_try)
                if effort:
                    kwargs["reasoning_effort"] = effort
                completion = groq_client.chat.completions.create(**kwargs)
                content = completion.choices[0].message.content
                if content and content.strip():
                    return content
                print(f"    Empty response from {model_to_try} (attempt {attempt + 1}) — retrying...", flush=True)
            except Exception as e:
                msg = str(e)
                print(f"    LLM attempt {attempt + 1} failed with {model_to_try}: {msg}", flush=True)
                if "rate_limit" in msg.lower() or "429" in msg or "quota" in msg.lower():
                    wait_time = delay * (attempt + 1) * 2
                    print(f"    Rate limit hit — sleeping {wait_time}s...", flush=True)
                    time.sleep(wait_time)
            time.sleep(delay)
    return ""


def build_agent_catalog_text(feed):
    """Compact catalog view for matching — full feed.json is overkill for this call."""
    lines = []
    for r in feed.values():
        lines.append(json.dumps({
            "product_id": r["product_id"],
            "title": r.get("title"),
            "category": r.get("category"),
            "key_attributes": r.get("key_attributes"),
            "price": r.get("price"),
            "price_currency": r.get("price_currency") or "unknown",
        }, ensure_ascii=False))
    return "\n".join(lines)


def match_product(query, feed, forced_product_id=None):
    """
    8a. Returns a 'purchase intent' dict: {query, matched_product_id, requested_price}
    or {query, matched_product_id: None} if nothing matched.

    forced_product_id lets you bypass the LLM for a guaranteed demo case —
    useful when you need a specific APPROVE/HOLD/BLOCK outcome on stage.
    """
    if forced_product_id:
        if forced_product_id not in feed:
            return {"query": query, "matched_product_id": None}
        return {"query": query, "matched_product_id": forced_product_id}

    catalog_text = build_agent_catalog_text(feed)
    prompt = f"""You are a buyer agent matching a shopper's request to one product.

Catalog:
{catalog_text}

User query: "{query}"

Pick the single best-matching product_id from the catalog above. If nothing
reasonably matches, say NONE.

Respond in exactly this format, nothing else:
Product ID: <product_id or NONE>
"""
    response = call_llm(prompt, max_tokens=300)
    match = re.search(r"Product ID:\s*(\S+)", response or "")
    product_id = match.group(1).strip() if match else None
    if not product_id or product_id.upper() == "NONE" or product_id not in feed:
        return {"query": query, "matched_product_id": None}
    return {"query": query, "matched_product_id": product_id}


# ---------------------------------------------------------------------------
# 8b. The Trust-Based Gate
# ---------------------------------------------------------------------------

def find_alternative(feed, blocked_product_id, category=None):
    """
    Picks the highest-trust, currency-supported, in-budget product as a
    graceful-failure alternative. Prefers the same category first.
    """
    candidates = [
        r for pid, r in feed.items()
        if pid != blocked_product_id
        and r.get("price_currency", "INR") in SUPPORTED_CURRENCIES
        and (r.get("trust_score") or 0) >= APPROVE_TRUST_THRESHOLD
        and (r.get("price") or 0) <= SPEND_LIMIT
    ]
    if not candidates:
        return None

    if category:
        same_category = [r for r in candidates if r.get("category") == category]
        if same_category:
            candidates = same_category

    candidates.sort(key=lambda r: r.get("trust_score") or 0, reverse=True)
    best = candidates[0]
    return {"product_id": best["product_id"], "title": best.get("title")}


def gate_decision(product, feed):
    """
    8b. Plain, deterministic if/else logic. No AI call here on purpose —
    money decisions should be boring and auditable, not another model guess.

    Returns a dict: {decision, reason, alternative}
    decision is one of: APPROVE, HOLD_FOR_APPROVAL, BLOCK
    """
    trust_score = product.get("trust_score") or 0
    price = product.get("price")
    currency = product.get("price_currency", "INR")
    category = product.get("category")
    product_id = product["product_id"]

    # Currency check first — a real, structural reason to fail gracefully,
    # separate from trust. This account can only settle INR test-mode orders.
    if currency not in SUPPORTED_CURRENCIES:
        alt = find_alternative(feed, product_id, category)
        return {
            "decision": "BLOCK",
            "reason": f"Listed in {currency}, which this merchant account cannot "
                      f"settle directly. Needs currency conversion before purchase.",
            "alternative": alt,
        }

    if price is None:
        alt = find_alternative(feed, product_id, category)
        return {
            "decision": "BLOCK",
            "reason": "No price found on this listing — cannot safely authorize a payment amount.",
            "alternative": alt,
        }

    if trust_score >= APPROVE_TRUST_THRESHOLD and price <= SPEND_LIMIT:
        return {"decision": "APPROVE", "reason": f"Trust score {trust_score} and price ₹{price} within policy.", "alternative": None}

    if trust_score >= APPROVE_TRUST_THRESHOLD and price > SPEND_LIMIT:
        return {
            "decision": "HOLD_FOR_APPROVAL",
            "reason": f"Trust score {trust_score} is high, but price ₹{price} exceeds the ₹{SPEND_LIMIT} auto-approve limit.",
            "alternative": None,
        }

    if trust_score >= HOLD_TRUST_THRESHOLD:
        return {
            "decision": "HOLD_FOR_APPROVAL",
            "reason": f"Trust score {trust_score} is moderate — needs a human check before this purchase proceeds.",
            "alternative": None,
        }

    alt = find_alternative(feed, product_id, category)
    return {
        "decision": "BLOCK",
        "reason": f"Trust score {trust_score} is too low — claims on this listing are largely unsupported.",
        "alternative": alt,
    }


# ---------------------------------------------------------------------------
# 8c. Razorpay Execution (the APPROVE path)
# ---------------------------------------------------------------------------

def create_test_order(product):
    """
    8c. Fires a real Razorpay TEST-MODE order for an approved product.
    Returns {status, order_id, raw} or {status: 'error', reason}.
    """
    if razorpay is None:
        return {"status": "error", "reason": "razorpay package not installed"}
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return {"status": "error", "reason": "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET missing from .env"}

    client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    amount_paise = int(round(product["price"] * 100))
    receipt = f"rcpt_{product['product_id']}_{int(time.time())}"

    try:
        order = client.order.create({
            "amount": amount_paise,
            "currency": product.get("price_currency", "INR"),
            "receipt": receipt,
            "notes": {
                "product_id": product["product_id"],
                "title": product.get("title", ""),
                "trust_score": str(product.get("trust_score")),
            },
        })
        return {"status": "created", "order_id": order.get("id"), "raw": order}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


# ---------------------------------------------------------------------------
# 8d. The Audit Trail
# ---------------------------------------------------------------------------

def append_audit_entry(entry):
    log = []
    if os.path.exists(AUDIT_LOG_FILE):
        try:
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                log = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            log = []
    log.append(entry)
    with open(AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Orchestration: run one query through 8a -> 8b -> 8c -> 8d
# ---------------------------------------------------------------------------

def run_purchase(query, feed, forced_product_id=None):
    print(f"\nQuery: \"{query}\"", flush=True)

    # 8a
    intent = match_product(query, feed, forced_product_id=forced_product_id)
    product_id = intent["matched_product_id"]

    if not product_id:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "matched_product_id": None,
            "decision": "NO_MATCH",
            "reason": "No product in the catalog matched this request.",
            "alternative": None,
            "payment": None,
        }
        append_audit_entry(entry)
        print("  No match found in catalog.", flush=True)
        return entry

    product = feed[product_id]
    print(f"  Matched: {product.get('title')} (trust_score={product.get('trust_score')}, "
          f"price={product.get('price')} {product.get('price_currency', 'INR')})", flush=True)

    # 8b
    gate = gate_decision(product, feed)
    print(f"  Decision: {gate['decision']} — {gate['reason']}", flush=True)
    if gate["alternative"]:
        print(f"  Suggested alternative: {gate['alternative']['title']}", flush=True)

    # 8c
    payment = None
    if gate["decision"] == "APPROVE":
        payment = create_test_order(product)
        if payment["status"] == "created":
            print(f"  Razorpay order created: {payment['order_id']}", flush=True)
        else:
            print(f"  Razorpay order FAILED: {payment['reason']}", flush=True)

    # 8d — log all fields required by the build guide spec:
    # timestamp, product, claim, evidence_check_result, trust_score, decision, reason, payment_status
    claims_evaluated = product.get("claims_evaluated", [])
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "matched_product_id": product_id,
        "matched_title": product.get("title"),
        "claim": [c.get("claim") for c in claims_evaluated] if claims_evaluated else [],
        "evidence_check_result": product.get("trust_summary_reason", ""),
        "trust_score": product.get("trust_score"),
        "price": product.get("price"),
        "currency": product.get("price_currency", "INR"),
        "decision": gate["decision"],
        "reason": gate["reason"],
        "alternative": gate["alternative"],
        "payment_status": payment.get("status") if payment else None,
        "payment": payment,
    }
    append_audit_entry(entry)
    return entry


# ---------------------------------------------------------------------------
# Demo batch — guarantees you see APPROVE, HOLD, and both BLOCK types
# ---------------------------------------------------------------------------

DEMO_QUERIES = [
    # APPROVE case: high trust, in budget, INR (triggers Razorpay order creation)
    {"query": "A skincare product under 1500 rupees", "forced_product_id": "prod_004"},
    # HOLD case: high trust but over the spend limit
    {"query": "A titanium-build smart ring with a warranty", "forced_product_id": "prod_003"},
    # BLOCK (trust) case: the Morrowen-style listing
    {"query": "An anti-aging product that works fast", "forced_product_id": "prod_001"},
    # BLOCK (currency) case: USD-priced listing on an INR-only account
    {"query": "A titanium water bottle with a warranty", "forced_product_id": "prod_006"},
]


def main():
    feed = load_feed()
    print(f"Loaded {len(feed)} products from {FEED_FILE}.")

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        run_purchase(query, feed)
    else:
        print(f"No query given — running the {len(DEMO_QUERIES)}-case demo batch "
              f"(APPROVE / HOLD / BLOCK-trust / BLOCK-currency)...\n")
        for case in DEMO_QUERIES:
            run_purchase(case["query"], feed, forced_product_id=case.get("forced_product_id"))
            time.sleep(2)

    print(f"\nDone. Full audit trail in {AUDIT_LOG_FILE}.")


if __name__ == "__main__":
    main()