"""
Component: Upsell / Cross-sell Agent  (Build Guide Part 9 — optional bonus)

"Once every product has consistent structured attributes, 'customers who need
X also often need Y' can mostly be a lookup over your own feed.json (same
category, complementary use-case) rather than new infrastructure."

This module does exactly that:

  1. Classifies the purchased product into a broad category CLUSTER using
     keyword matching (not exact string equality -- category names are
     free-text from an LLM extraction and vary run to run).
  2. UPSELL candidates: same cluster, strictly pricier, trust_score >= floor.
  3. CROSS-SELL candidates: a *different* but complementary cluster (via a
     small hand-written adjacency table), trust_score >= floor.
  4. The same trust floor used by the Part 8 gate is reused here on purpose --
     this agent should never suggest something the gate itself would reject.
  5. Reason text defaults to a deterministic template (zero extra API calls).
     An optional single batched LLM call can polish the wording -- this is
     the guide's own "optional bonus," so it's opt-in, not required.
  6. If nothing clears the bar, that is stated explicitly. No fabricated
     pairings just to fill a UI slot.

Usage:
    from upsell_agent import suggest_upsell_cross_sell, load_feed

    feed = load_feed("feed.json")
    result = suggest_upsell_cross_sell("prod_003", feed)

Or from the command line:
    python upsell_agent.py prod_003
    python upsell_agent.py prod_003 --llm-pitch
"""

import os
import sys
import json
import argparse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

FEED_FILE = "feed.json"
TRUST_FLOOR = 70          # never suggest anything below this -- matches the
                            # spirit of the Part 8 gate's own trust bar
MAX_UPSELL = 1
MAX_CROSS_SELL = 2

# Rough display-only currency normalisation so "pricier" comparisons are
# meaningful across currencies. Same rates used in the Part 9 dashboard.
CURRENCY_RATES = {"INR": 1, "USD": 83, "EUR": 90, "unknown": 1}

# ---------------------------------------------------------------------------
# Category clustering -- keyword based, not exact string match, because the
# extraction LLM's "category" field is free text and drifts run to run
# ("Food" vs "Food & Beverage" vs "Beverages").
# ---------------------------------------------------------------------------

CLUSTER_KEYWORDS = {
    "food_beverage": ["food", "tea", "matcha", "snack", "beverage", "honey", "breakfast", "oats"],
    "personal_care": ["skin", "skincare", "beauty", "deodorant", "cosmetic", "hygiene"],
    "health":        ["health", "supplement", "vitamin", "fitness", "wellness"],
    "fitness":       ["fitness", "yoga", "gym", "exercise", "sport"],
    "electronics":   ["electronic", "gadget", "device", "speaker", "mouse", "keyboard", "kettle", "bulb", "microcontroller"],
    "wearables":     ["wearable", "ring", "watch", "tracker"],
    "apparel":       ["apparel", "clothing", "fashion", "jacket", "wear"],
    "home":          ["home", "kitchen", "furniture", "chair", "lunch box", "mug", "candle"],
    "books":         ["book", "picture book", "reading"],
    "accessories":   ["accessories", "glasses", "bottle", "bag", "wallet"],
}

# Undirected complementary pairs between clusters. Read both ways at lookup
# time -- no need to duplicate each pair in both directions here.
CLUSTER_COMPLEMENTS = [
    ("food_beverage", "home"),          # tea/matcha <-> kettles, mugs, kitchenware
    ("food_beverage", "health"),        # snacks/oats <-> supplements
    ("personal_care", "health"),        # skincare <-> wellness supplements
    ("fitness", "apparel"),             # yoga mat <-> workout wear
    ("fitness", "wearables"),           # yoga mat <-> fitness tracker
    ("wearables", "health"),            # tracker <-> supplements
    ("electronics", "accessories"),     # devices <-> cases/bags/pouches
    ("apparel", "accessories"),         # jacket <-> bags/wallets
    ("home", "electronics"),            # kitchen <-> kitchen electronics
]


def normalize_cluster(category):
    """Map a free-text category string to one of our clusters, or None."""
    if not category:
        return None
    text = category.lower()
    for cluster, keywords in CLUSTER_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return cluster
    return None


def complementary_clusters(cluster):
    out = set()
    for a, b in CLUSTER_COMPLEMENTS:
        if a == cluster:
            out.add(b)
        elif b == cluster:
            out.add(a)
    return out


def price_in_inr(product):
    currency = product.get("price_currency") or product.get("currency") or "unknown"
    price = product.get("price")
    if price is None:
        return None
    return price * CURRENCY_RATES.get(currency, 1)


# ---------------------------------------------------------------------------
# Feed loading
# ---------------------------------------------------------------------------

def annotate_feed(feed):
    """
    Accepts EITHER shape and returns a dict keyed by product_id with
    _cluster/_price_inr precomputed:
      - a list of records (e.g. straight from feed.json), or
      - a dict already keyed by product_id (e.g. purchase_pipeline.load_feed()
        returns {product_id: record, ...} -- this is the shape dashboard_app.py
        actually passes in).

    Safe to call repeatedly / on every request -- it doesn't mutate shared
    state beyond adding two derived keys to each record's own dict, and it's
    pure lookup math, no I/O.
    """
    records = feed.values() if isinstance(feed, dict) else feed
    annotated = {}
    for r in records:
        r = dict(r)  # don't mutate the caller's original records
        r["_cluster"] = normalize_cluster(r.get("category"))
        r["_price_inr"] = price_in_inr(r)
        annotated[r["product_id"]] = r
    return annotated


def load_feed(path=FEED_FILE):
    """Standalone file-loading path, used by the CLI. If you already have a
    feed list in memory (e.g. from purchase_pipeline.load_feed()), call
    annotate_feed(that_list) directly instead of re-reading the file."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return annotate_feed(raw)


# ---------------------------------------------------------------------------
# Deterministic candidate lookup
# ---------------------------------------------------------------------------

def find_upsell_candidates(product, feed, trust_floor=TRUST_FLOOR, limit=MAX_UPSELL):
    cluster = product["_cluster"]
    base_price = product["_price_inr"]
    if cluster is None or base_price is None:
        return []

    candidates = [
        p for p in feed.values()
        if p["product_id"] != product["product_id"]
        and p["_cluster"] == cluster
        and (p.get("trust_score") or 0) >= trust_floor
        and p["_price_inr"] is not None
        and p["_price_inr"] > base_price
    ]
    # Cheapest step-up first -- more believable than jumping straight to
    # the most expensive item in the category.
    candidates.sort(key=lambda p: (p["_price_inr"], -1 * (p.get("trust_score") or 0)))
    return candidates[:limit]


def find_cross_sell_candidates(product, feed, trust_floor=TRUST_FLOOR, limit=MAX_CROSS_SELL):
    cluster = product["_cluster"]
    if cluster is None:
        return []
    allowed = complementary_clusters(cluster)
    if not allowed:
        return []

    candidates = [
        p for p in feed.values()
        if p["product_id"] != product["product_id"]
        and p["_cluster"] in allowed
        and (p.get("trust_score") or 0) >= trust_floor
    ]
    candidates.sort(key=lambda p: -(p.get("trust_score") or 0))
    return candidates[:limit]


# ---------------------------------------------------------------------------
# Reason text -- deterministic template by default
# ---------------------------------------------------------------------------

def template_upsell_reason(base, candidate):
    delta = candidate["_price_inr"] - base["_price_inr"]
    return (
        f"A step up from {base['title']} in the same category, at a "
        f"trust score of {candidate.get('trust_score')} — about ₹{round(delta)} more."
    )


def template_cross_sell_reason(base, candidate):
    return (
        f"Frequently paired with {base['title']} based on category "
        f"compatibility ({base['_cluster']} + {candidate['_cluster']}), "
        f"trust score {candidate.get('trust_score')}."
    )


# ---------------------------------------------------------------------------
# Optional: single batched LLM call to polish the pitch wording.
# Off by default. Only fires if candidates were already found deterministically
# above -- the LLM is never asked to invent or choose candidates, only to
# phrase ones that already passed the trust floor.
# ---------------------------------------------------------------------------

def llm_polish_pitches(base, upsell_list, cross_sell_list):
    try:
        from dotenv import load_dotenv
        from groq import Groq
    except ImportError:
        print("  (groq/dotenv not installed -- falling back to template reasons)")
        return None

    load_dotenv()
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("  (no GROQ_API_KEY -- falling back to template reasons)")
        return None

    client = Groq(api_key=api_key)
    items = (
        [{"role": "upsell", "title": c["title"], "trust_score": c.get("trust_score")} for c in upsell_list]
        + [{"role": "cross_sell", "title": c["title"], "trust_score": c.get("trust_score")} for c in cross_sell_list]
    )
    if not items:
        return None

    prompt = f"""A customer just bought: "{base['title']}".

Here are pre-approved suggestions (already trust-checked -- do not add, remove, or replace any of them):
{json.dumps(items)}

For each item, write ONE short, natural, non-pushy sentence a checkout screen could show, explaining why it pairs with the purchase. Do not invent any product not in the list above.

Output ONLY a JSON array, same order as input, no markdown fences:
[{{"title": "...", "pitch": "..."}}]
"""
    try:
        completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="qwen/qwen3.8-27b",
            max_tokens=400,
            temperature=0.4,
        )
        text = completion.choices[0].message.content.strip()
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        data = json.loads(text)
        return {d["title"]: d["pitch"] for d in data if "title" in d and "pitch" in d}
    except Exception as e:
        print(f"  (LLM pitch polish failed, falling back to templates: {e})")
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def suggest_upsell_cross_sell(product_id, feed, trust_floor=TRUST_FLOOR, use_llm_pitch=False):
    """
    Returns:
    {
      "based_on": {"product_id", "title"},
      "upsell": [{"product_id","title","price","trust_score","reason"}],
      "cross_sell": [...],
      "upsell_available": bool,
      "cross_sell_available": bool,
    }
    """
    product = feed.get(product_id)
    if product is None:
        return {
            "based_on": {"product_id": product_id, "title": None},
            "upsell": [],
            "cross_sell": [],
            "upsell_available": False,
            "cross_sell_available": False,
            "note": f"Product {product_id} not found in feed.",
        }

    upsell_candidates = find_upsell_candidates(product, feed, trust_floor)
    cross_sell_candidates = find_cross_sell_candidates(product, feed, trust_floor)

    pitches = None
    if use_llm_pitch and (upsell_candidates or cross_sell_candidates):
        pitches = llm_polish_pitches(product, upsell_candidates, cross_sell_candidates)

    def build(candidate, is_upsell):
        reason = None
        if pitches and candidate["title"] in pitches:
            reason = pitches[candidate["title"]]
        if not reason:
            reason = template_upsell_reason(product, candidate) if is_upsell else template_cross_sell_reason(product, candidate)
        return {
            "product_id": candidate["product_id"],
            "title": candidate["title"],
            "price": candidate.get("price"),
            "currency": candidate.get("price_currency") or candidate.get("currency"),
            "trust_score": candidate.get("trust_score"),
            "reason": reason,
        }

    return {
        "based_on": {"product_id": product["product_id"], "title": product["title"]},
        "upsell": [build(c, True) for c in upsell_candidates],
        "cross_sell": [build(c, False) for c in cross_sell_candidates],
        "upsell_available": bool(upsell_candidates),
        "cross_sell_available": bool(cross_sell_candidates),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Upsell / cross-sell agent")
    parser.add_argument("product_id", help="product_id that was just approved/purchased")
    parser.add_argument("--feed", default=FEED_FILE)
    parser.add_argument("--trust-floor", type=int, default=TRUST_FLOOR)
    parser.add_argument("--llm-pitch", action="store_true", help="polish reasons with one batched LLM call")
    args = parser.parse_args()

    feed = load_feed(args.feed)
    result = suggest_upsell_cross_sell(
        args.product_id, feed, trust_floor=args.trust_floor, use_llm_pitch=args.llm_pitch
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()