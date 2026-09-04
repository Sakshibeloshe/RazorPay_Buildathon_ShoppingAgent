"""
Part 7: Prove Discoverability
=============================
Build Guide reference: Part 7

PRE-GEO ("before"): a pure keyword-overlap matcher runs against the raw,
unstructured catalog text. No LLM, no reasoning -- this models what a naive
keyword/SEO-style search sees: literal string matches only. It cannot do
currency conversion, numeric threshold reasoning ("under 1500"), or
synonym/semantic matching ("Japanese" != "Japan" unless the exact word is
present). That's intentional -- it's the realistic baseline a dumb pre-GEO
search represents, and it costs zero tokens.

POST-GEO ("after"): the enriched, structured feed.json entry is given to an
LLM, which can actually reason -- convert currency, evaluate numeric
thresholds, and match intent semantically. This is where structuring is
supposed to pay off.

We then check whether the target product was recommended in each case and
tally the counts.  The headline stat is:
  "Mentioned in X/N queries BEFORE optimization, Y/N AFTER."

Second finding:
  Bucket results into HIGH trust (>=70) vs LOW trust (<70) to verify lift.

Input:
  feed.json                  - built by publish_feed.py (Part 6)
  shopping_queries_hard.json - list of {query, target_product_id}

Output:
  discoverability_results.json - full per-query detail + summaries
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
MODEL_NAME = "openai/gpt-oss-20b"
FALLBACK_MODEL = "qwen/qwen3.8-27b"

# Only gpt-oss models get reasoning_effort forced down to "low" -- their
# own default is "medium", so "low" is a real savings. qwen/qwen3.8-27b's
# own default is already "none" (cheaper than "low"); forcing "low" onto
# it makes it generate hidden reasoning it otherwise wouldn't, which can
# burn the entire token budget before it ever writes the visible answer,
# returning empty content with no error. Never pass reasoning_effort to qwen.
REASONING_EFFORT_BY_MODEL = {
    "openai/gpt-oss-20b": "low",
    "openai/gpt-oss-120b": "low",
}

FEED_FILE = "feed.json"
QUERIES_FILE = "shopping_queries_hard.json"
OUTPUT_FILE = "discoverability_results.json"


# ---------------------------------------------------------------------------
# PRE-GEO: pure keyword-overlap matcher, NO LLM call, NO tokens spent
# ---------------------------------------------------------------------------

KEYWORD_STOPWORDS = {
    "a", "an", "the", "for", "of", "and", "or", "with", "under", "over",
    "that", "this", "is", "are", "i", "need", "want", "my", "me", "to",
    "in", "on", "at", "has", "have", "explicitly", "allows", "allow",
    "even", "already", "used", "real", "priced", "price", "product",
    "products", "item", "items",
}


def _normalize_word(word):
    w = word.lower()
    if len(w) > 4 and w.endswith("s"):
        w = w[:-1]  # crude singularization so "returns"/"return" both count
    return w


def _extract_keywords(text):
    words = re.findall(r"[a-zA-Z0-9]+", text)
    return {
        _normalize_word(w) for w in words
        if w.lower() not in KEYWORD_STOPWORDS and len(w) > 1
    }


def keyword_match_batch(queries, feed):
    """
    Pure literal keyword overlap against each product's title + raw
    description. No semantics, no currency conversion, no numeric reasoning
    -- this is the realistic "before GEO" baseline (what a naive keyword
    search finds), computed entirely in Python with zero API calls.

    Returns {query_index (1-based): {"recommendation": title or "NONE", "reason": ...}}
    to match the shape the LLM batch function returns, so the rest of the
    pipeline doesn't need to know which path produced it.
    """
    catalog_keywords = [
        (r.get("title", ""), _extract_keywords(r.get("title", "") + " " + r.get("original_raw_description", "")))
        for r in feed.values()
    ]

    results = {}
    for i, query in enumerate(queries, start=1):
        query_kw = _extract_keywords(query)
        scores = [(title, len(query_kw & kw_set)) for title, kw_set in catalog_keywords]
        best_title, best_score = max(scores, key=lambda x: x[1]) if scores else ("NONE", 0)

        if best_score == 0:
            results[i] = {
                "recommendation": "NONE",
                "reason": "No literal keyword overlap found in any raw listing.",
            }
        else:
            results[i] = {
                "recommendation": best_title,
                "reason": f"Keyword overlap score={best_score} against raw listing text (literal match only, no reasoning).",
            }
    return results


# ---------------------------------------------------------------------------
# Catalog loader + POST-GEO structured catalog text
# ---------------------------------------------------------------------------

def load_feed():
    """Returns {product_id: feed_record} from feed.json."""
    with open(FEED_FILE, "r", encoding="utf-8") as f:
        records = json.load(f)
    return {r["product_id"]: r for r in records}


def build_structured_catalog_text(feed):
    """
    The 'after' treatment: the enriched, structured feed entry.
    Price and currency are formatted as an explicit string to prevent unit errors.
    """
    lines = []
    for r in feed.values():
        price_val = r.get("price")
        # Do NOT default to INR -- that silently mislabels USD/EUR/GBP prices
        # and causes wrong cross-currency matches (e.g. a $45.99 bottle being
        # read as "45.99 INR" and winning a totally unrelated ₹1000 query).
        currency = r.get("price_currency") or "unknown"
        price_str = f"{price_val} {currency}" if price_val is not None else "missing"

        entry = {
            "title": r.get("title"),
            "category": r.get("category"),
            "key_attributes": r.get("key_attributes"),
            "claims_made": r.get("claims_made"),
            "price": price_str,
            "warranty": r.get("warranty", "missing"),
            "availability": r.get("availability"),
            "fulfillment_terms": r.get("fulfillment_terms"),
        }
        lines.append(json.dumps(entry, ensure_ascii=False))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# POST-GEO: LLM call, reasons over structured data only
# ---------------------------------------------------------------------------

def ask_for_recommendations_batch(queries, catalog_text, retries=3, delay=5):
    """
    Sends ALL queries in a single call against the structured catalog text.
    Returns a dict {query_index (1-based int) -> {"recommendation": str, "reason": str}}.
    """
    numbered_queries = "\n".join(
        f'{i}. "{q}"' for i, q in enumerate(queries, start=1)
    )

    prompt = f"""You are an AI shopping agent that must recommend a product a downstream checkout system can safely act on.

Catalog data (clean, structured records -- every field is labeled):
{catalog_text}

Below is a numbered list of {len(queries)} independent user queries. Answer EACH one separately, using only the catalog data above. Do not let one query's answer influence another's -- evaluate each in isolation.

Queries:
{numbered_queries}

Rules (apply to every query):
1. Only recommend a product if the catalog entry gives you clean, labeled fields you can trust for whatever the SPECIFIC query is asking about. Don't demand fields the query didn't ask for -- e.g. if the query never mentions stock/availability, a missing "availability" field is NOT a reason to reject the product; only treat a field as required if the query's own wording depends on it (price fields are relevant to any budget constraint, return/warranty fields only matter if the query asks about returns/warranty, etc.).
2. Convert currencies and reason about numeric thresholds where needed (e.g. a price given in USD can satisfy an INR budget if the converted amount fits).
3. If no catalog entry safely and correctly matches a given query, say so for that query. If the ONLY reason you're unsure is a field the query didn't actually ask about, that is not grounds to say NONE.

Respond with ONLY a JSON array, no markdown fences, no preamble, no text before or after it. One object per query, in the same order as the numbered list, each shaped exactly like this:
[
  {{"query_index": 1, "recommendation": "<exact product title, or NONE>", "reason": "<one sentence>"}},
  {{"query_index": 2, "recommendation": "<exact product title, or NONE>", "reason": "<one sentence>"}}
]
"""
    last_error = None
    for model_to_try in [MODEL_NAME, FALLBACK_MODEL]:
        for attempt in range(retries):
            try:
                kwargs = dict(
                    messages=[{"role": "user", "content": prompt}],
                    model=model_to_try,
                    # One call now has to answer every query, so give it much
                    # more room than a single-query budget.
                    max_tokens=400 * max(len(queries), 1) + 500,
                    temperature=0.2,
                )
                effort = REASONING_EFFORT_BY_MODEL.get(model_to_try)
                if effort:
                    kwargs["reasoning_effort"] = effort
                completion = client.chat.completions.create(**kwargs)
                content = completion.choices[0].message.content
                if content and content.strip():
                    parsed = _parse_batch_json(content)
                    if parsed is not None:
                        return parsed
                    last_error = f"{model_to_try} returned content that wasn't valid/parseable JSON: {content[:300]!r}"
                    print(f"    {last_error}", flush=True)
                else:
                    last_error = (
                        f"{model_to_try} returned EMPTY content (no exception) -- "
                        f"likely burned its token budget on hidden reasoning."
                    )
                    print(f"    {last_error}", flush=True)
            except Exception as e:
                last_error = str(e)
                print(f"    LLM attempt {attempt + 1} failed with {model_to_try}: {last_error}", flush=True)
                if "rate_limit" in last_error.lower() or "429" in last_error or "tokens" in last_error.lower():
                    wait_time = delay * (attempt + 1) * 2
                    print(f"    Rate limit / token limit hit — sleeping {wait_time}s...", flush=True)
                    time.sleep(wait_time)
                elif attempt < retries - 1:
                    time.sleep(delay)

    # All retries across all models exhausted -- return an explicit failure
    # marker for every query rather than silently defaulting to NONE, so a
    # dead API call can't masquerade as "correctly found nothing".
    print(f"    ⚠ Batch call failed outright: {last_error}", flush=True)
    return {
        i: {"recommendation": f"__CALL_FAILED__: {last_error}", "reason": ""}
        for i in range(1, len(queries) + 1)
    }


def _parse_batch_json(content):
    """Strip optional markdown fences and parse the model's JSON array into
    a {query_index: {...}} dict. Returns None if parsing fails."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, list):
        return None

    result = {}
    for entry in data:
        if not isinstance(entry, dict) or "query_index" not in entry:
            continue
        try:
            idx = int(entry["query_index"])
        except (TypeError, ValueError):
            continue
        result[idx] = {
            "recommendation": entry.get("recommendation", "NONE"),
            "reason": entry.get("reason", ""),
        }
    return result if result else None


def title_mentioned(title, response_text):
    """
    Strict match: only looks at the 'Final Recommendation:' line, not the
    model's full reasoning — avoids false positives from the model merely
    discussing a product it's rejecting.
    """
    if not response_text or response_text.startswith("__CALL_FAILED__"):
        return False
    match = re.search(r"Final Recommendation:\s*(.+)", response_text)
    if not match:
        return False
    final_line = match.group(1).strip().lower()
    if final_line.startswith("none"):
        return False
    stopwords = {"the", "a", "an", "of", "for", "and", "pro", "grade", "with"}
    words = [w.lower() for w in re.findall(r"[a-zA-Z]+", title)
             if w.lower() not in stopwords and len(w) > 2]
    if not words:
        return title.lower() in final_line
    hits = sum(1 for w in words if w in final_line)
    return hits >= max(1, len(words) // 2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not os.path.exists(FEED_FILE):
        print(f"Error: {FEED_FILE} not found. Run publish_feed.py (Part 6) first.")
        sys.exit(1)
    if not os.path.exists(QUERIES_FILE):
        print(f"Error: {QUERIES_FILE} not found.")
        sys.exit(1)

    feed = load_feed()
    with open(QUERIES_FILE, "r", encoding="utf-8") as f:
        queries = json.load(f)

    structured_catalog_text = build_structured_catalog_text(feed)

    print(f"Loaded {len(feed)} products from {FEED_FILE}.")
    print(f"Running discoverability test on {len(queries)} queries "
          f"(pre-GEO: keyword matcher, 0 API calls | post-GEO: 1 batched LLM call)...\n", flush=True)

    usable_items = []
    for item in queries:
        target_id = item["target_product_id"]
        if target_id not in feed:
            print(f"  SKIP: target {target_id} not in feed.json — {item['query']}", flush=True)
            continue
        usable_items.append(item)

    query_texts = [item["query"] for item in usable_items]

    # --- PRE-GEO: keyword matcher, zero tokens ---
    print("Running keyword matcher on RAW catalog for all queries (no LLM)...", flush=True)
    raw_batch = keyword_match_batch(query_texts, feed)

    # --- POST-GEO: one batched LLM call, reasons over structured data ---
    print("Calling model on STRUCTURED feed for all queries...", flush=True)
    structured_batch = ask_for_recommendations_batch(query_texts, structured_catalog_text)
    time.sleep(8)

    results = []
    mentioned_raw = 0
    mentioned_structured = 0
    usable_count = 0

    for i, item in enumerate(usable_items, start=1):
        query = item["query"]
        target_id = item["target_product_id"]
        target_title = feed[target_id]["title"]
        trust_score = feed[target_id].get("trust_score")
        usable_count += 1

        print(f"[{usable_count}] Query:  \"{query}\"", flush=True)
        print(f"     Target: {target_title}  (trust_score={trust_score})", flush=True)

        raw_entry = raw_batch.get(i, {"recommendation": "NONE", "reason": "missing from batch"})
        structured_entry = structured_batch.get(i, {"recommendation": "__CALL_FAILED__: missing from batch response", "reason": ""})

        raw_response = f"Final Recommendation: {raw_entry['recommendation']}\nReason: {raw_entry['reason']}"
        structured_response = f"Final Recommendation: {structured_entry['recommendation']}\nReason: {structured_entry['reason']}"

        # The keyword matcher never "fails outright" the way an API call
        # can -- it always returns a deterministic result (possibly NONE).
        raw_failed = False
        structured_failed = str(structured_entry["recommendation"]).startswith("__CALL_FAILED__")
        if structured_failed:
            print(f"     ⚠ STRUCTURED entry failed: {structured_entry['recommendation']}", flush=True)

        raw_hit = title_mentioned(target_title, raw_response)
        structured_hit = title_mentioned(target_title, structured_response)

        mentioned_raw += int(raw_hit)
        mentioned_structured += int(structured_hit)

        lift = "same"
        if structured_hit and not raw_hit:
            lift = "IMPROVED ↑"
        elif raw_hit and not structured_hit:
            lift = "REGRESSED ↓"
        elif structured_hit and raw_hit:
            lift = "both hit"

        print(f"     Before (keyword match): {'✅ mentioned' if raw_hit else '❌ not mentioned'}  -- {raw_entry['reason']}", flush=True)
        print(f"     After  (LLM+feed.json): {'✅ mentioned' if structured_hit else '❌ not mentioned'}  -- {structured_entry['reason']}", flush=True)
        print(f"     Lift:  {lift}\n", flush=True)

        results.append({
            "query": query,
            "target_product_id": target_id,
            "target_title": target_title,
            "trust_score": trust_score,
            "raw_mentioned": raw_hit,
            "structured_mentioned": structured_hit,
            "raw_call_failed": raw_failed,
            "structured_call_failed": structured_failed,
            "lift": lift,
            "raw_response_snippet": raw_response[:300] if raw_response else "",
            "structured_response_snippet": structured_response[:300] if structured_response else "",
        })

    total_raw_failures = sum(1 for r in results if r["raw_call_failed"])
    total_structured_failures = sum(1 for r in results if r["structured_call_failed"])

    headline = (
        f"Before GEO (keyword match on raw catalog): target mentioned in {mentioned_raw}/{usable_count} queries.  "
        f"After GEO (LLM + feed.json): {mentioned_structured}/{usable_count}.  "
        f"Net lift: +{mentioned_structured - mentioned_raw} recommendations."
    )
    if total_raw_failures or total_structured_failures:
        headline += (
            f"  ⚠ WARNING: {total_raw_failures} raw call(s) and {total_structured_failures} "
            f"structured call(s) failed outright (see *_call_failed fields) -- "
            f"these 0s are NOT real 'not mentioned' results, re-run before trusting this number."
        )

    summary = {
        "total_queries_tested": usable_count,
        "mentioned_before_raw": mentioned_raw,
        "mentioned_after_structured": mentioned_structured,
        "net_lift": mentioned_structured - mentioned_raw,
        "total_raw_call_failures": total_raw_failures,
        "total_structured_call_failures": total_structured_failures,
        "headline": headline,
    }

    high_trust_threshold = 70
    high_results = [r for r in results if (r["trust_score"] or 0) >= high_trust_threshold]
    low_results = [r for r in results if (r["trust_score"] or 0) < high_trust_threshold]

    def lift_pct(group):
        if not group:
            return None
        before = sum(1 for r in group if r["raw_mentioned"])
        after = sum(1 for r in group if r["structured_mentioned"])
        return {"n": len(group), "before": before, "after": after, "lift": after - before}

    trust_segmented = {
        f"high_trust_gte_{high_trust_threshold}": lift_pct(high_results),
        f"low_trust_lt_{high_trust_threshold}": lift_pct(low_results),
    }
    summary["trust_segmented_lift"] = trust_segmented

    output = {"summary": summary, "results": results}
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4, ensure_ascii=False)

    print("=" * 70, flush=True)
    print("DISCOVERABILITY TEST COMPLETE", flush=True)
    print("=" * 70, flush=True)
    print(f"\n{headline}\n", flush=True)

    ht = trust_segmented.get(f"high_trust_gte_{high_trust_threshold}")
    lt = trust_segmented.get(f"low_trust_lt_{high_trust_threshold}")
    if ht and lt:
        print(f"Trust-segmented lift (second finding):", flush=True)
        print(f"  High-trust products (score >={high_trust_threshold}):  "
              f"{ht['before']}/{ht['n']} -> {ht['after']}/{ht['n']}  "
              f"(lift +{ht['lift']})", flush=True)
        print(f"  Low-trust  products (score <{high_trust_threshold}):   "
              f"{lt['before']}/{lt['n']} -> {lt['after']}/{lt['n']}  "
              f"(lift +{lt['lift']})", flush=True)

    print(f"\nFull results saved to {OUTPUT_FILE}", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    main()