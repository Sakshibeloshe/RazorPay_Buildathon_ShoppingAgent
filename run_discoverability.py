# """
# Part 7: Prove Discoverability
# =============================
# Build Guide reference: Part 7

# For each shopping query in shopping_queries_hard.json, the SAME query is sent to
# the LLM TWICE:
#   - "raw"        → given only the original, unstructured product text (control)
#   - "structured" → given the enriched feed.json entry (treatment)

# We then check whether the target product was recommended in each case and
# tally the counts.  The headline stat is:
#   "Mentioned in X/N queries BEFORE optimization, Y/N AFTER."

# Second finding:
#   Bucket results into HIGH trust (≥70) vs LOW trust (<70) to verify lift.

# Input:
#   feed.json                  — built by publish_feed.py (Part 6)
#   shopping_queries_hard.json — list of {query, target_product_id}

# Output:
#   discoverability_results_v2.json  — full per-query detail + summaries
# """

# import os
# import sys
# import json
# import time
# import re
# from dotenv import load_dotenv
# from groq import Groq

# if sys.platform.startswith("win"):
#     try:
#         sys.stdout.reconfigure(encoding="utf-8")
#     except AttributeError:
#         pass

# load_dotenv()
# api_key = os.getenv("GROQ_API_KEY")
# if not api_key:
#     print("Error: GROQ_API_KEY not found in .env file.")
#     sys.exit(1)

# client = Groq(api_key=api_key)
# MODEL_NAME = "qwen/qwen3.8-27b"
# #MODEL_NAME = "openai/gpt-oss-20b"
# FALLBACK_MODEL = "qwen/qwen3.8-27b"

# FEED_FILE = "feed.json"
# QUERIES_FILE = "shopping_queries_hard.json"
# OUTPUT_FILE = "discoverability_results.json"


# # ---------------------------------------------------------------------------
# # Catalog loaders
# # ---------------------------------------------------------------------------

# def load_feed():
#     """Returns {product_id: feed_record} from feed.json."""
#     with open(FEED_FILE, "r", encoding="utf-8") as f:
#         records = json.load(f)
#     return {r["product_id"]: r for r in records}


# def build_raw_catalog_text(feed):
#     """
#     The 'before' control: plain title + original raw description.
#     This is what a merchant's catalog looks like BEFORE GEO treatment.
#     """
#     lines = []
#     for r in feed.values():
#         title = r.get("title", "")
#         raw = r.get("original_raw_description", "")
#         lines.append(f"Title: {title}\nDescription: {raw}")
#     return "\n\n".join(lines)


# def build_structured_catalog_text(feed):
#     """
#     The 'after' treatment: the enriched, structured feed entry.
#     This is what the catalog looks like AFTER GEO treatment (feed.json).
#     Price and currency are formatted as an explicit string to prevent unit errors.
#     """
#     lines = []
#     for r in feed.values():
#         price_val = r.get("price")
#         currency = r.get("price_currency", "INR")
#         price_str = f"{price_val} {currency}" if price_val is not None else "missing"

#         entry = {
#             "title": r.get("title"),
#             "category": r.get("category"),
#             "key_attributes": r.get("key_attributes"),
#             "claims_made": r.get("claims_made"),
#             "price": price_str,
#             "availability": r.get("availability"),
#             "fulfillment_terms": r.get("fulfillment_terms"),
#         }
#         lines.append(json.dumps(entry, ensure_ascii=False))
#     return "\n".join(lines)


# # ---------------------------------------------------------------------------
# # LLM call
# # ---------------------------------------------------------------------------

# def ask_for_recommendation(query, catalog_text, retries=3, delay=5):
#     prompt = f"""You are an AI shopping agent that must recommend a product a downstream checkout system can safely act on.

# Catalog data:
# {catalog_text}

# User query: "{query}"

# Rules:
# 1. Only recommend a product if the catalog entry gives you clean, structured fields you can trust for a real purchase (price, availability, and return/fulfillment terms clearly stated as data, not buried in free-form prose, raw code, or an unlabeled data dump).
# 2. If a listing is unstructured, informal, or in a raw/unparsed format (e.g. embedded XML, YAML, or a JSON string with no clear labels a shopper would understand), treat it as NOT SAFE TO ACT ON, even if you can technically read its contents. Do not recommend it.
# 3. If no catalog entry safely and correctly matches the query, say so.

# Respond in exactly this format, with nothing after it:
# Final Recommendation: <exact product title, or NONE>
# Reason: <one sentence>
# """
#     for model_to_try in [MODEL_NAME, FALLBACK_MODEL]:
#         for attempt in range(retries):
#             try:
#                 completion = client.chat.completions.create(
#                     messages=[{"role": "user", "content": prompt}],
#                     model=model_to_try,
#                     max_tokens=700,
#                     temperature=0.2,
#                     reasoning_effort="low",
#                 )
#                 return completion.choices[0].message.content
#             except Exception as e:
#                 msg = str(e)
#                 print(f"    LLM attempt {attempt + 1} failed with {model_to_try}: {msg}", flush=True)
#                 if "rate_limit" in msg.lower() or "429" in msg or "tokens" in msg.lower():
#                     wait_time = delay * (attempt + 1) * 2
#                     print(f"    Rate limit / token limit hit — sleeping {wait_time}s...", flush=True)
#                     time.sleep(wait_time)
#                 elif attempt < retries - 1:
#                     time.sleep(delay)

#     return ""


# def title_mentioned(title, response_text):
#     """
#     Strict match: only looks at the 'Final Recommendation:' line, not the
#     model's full reasoning — avoids false positives from the model merely
#     discussing a product it's rejecting.
#     """
#     if not response_text:
#         return False
#     match = re.search(r"Final Recommendation:\s*(.+)", response_text)
#     if not match:
#         return False
#     final_line = match.group(1).strip().lower()
#     if final_line.startswith("none"):
#         return False
#     stopwords = {"the", "a", "an", "of", "for", "and", "pro", "grade", "with"}
#     words = [w.lower() for w in re.findall(r"[a-zA-Z]+", title)
#              if w.lower() not in stopwords and len(w) > 2]
#     if not words:
#         return title.lower() in final_line
#     hits = sum(1 for w in words if w in final_line)
#     return hits >= max(1, len(words) // 2)


# # ---------------------------------------------------------------------------
# # Main
# # ---------------------------------------------------------------------------

# def main():
#     # --- Load inputs ---
#     if not os.path.exists(FEED_FILE):
#         print(f"Error: {FEED_FILE} not found. Run publish_feed.py (Part 6) first.")
#         sys.exit(1)
#     if not os.path.exists(QUERIES_FILE):
#         print(f"Error: {QUERIES_FILE} not found.")
#         sys.exit(1)

#     feed = load_feed()
#     with open(QUERIES_FILE, "r", encoding="utf-8") as f:
#         queries = json.load(f)

#     raw_catalog_text = build_raw_catalog_text(feed)
#     structured_catalog_text = build_structured_catalog_text(feed)

#     print(f"Loaded {len(feed)} products from {FEED_FILE}.")
#     print(f"Running discoverability test on {len(queries)} queries...\n", flush=True)

#     # --- Run experiment ---
#     results = []
#     mentioned_raw = 0
#     mentioned_structured = 0
#     usable_count = 0

#     for item in queries:
#         query = item["query"]
#         target_id = item["target_product_id"]

#         if target_id not in feed:
#             print(f"  SKIP: target {target_id} not in feed.json — {query}", flush=True)
#             continue

#         target_title = feed[target_id]["title"]
#         trust_score = feed[target_id].get("trust_score")
#         usable_count += 1

#         print(f"[{usable_count}] Query:  \"{query}\"", flush=True)
#         print(f"     Target: {target_title}  (trust_score={trust_score})", flush=True)

#         # CONTROL — raw, unstructured catalog (before GEO)
#         raw_response = ask_for_recommendation(query, raw_catalog_text)
#         time.sleep(8)

#         # TREATMENT — enriched feed.json (after GEO)
#         structured_response = ask_for_recommendation(query, structured_catalog_text)
#         time.sleep(8)

#         raw_hit = title_mentioned(target_title, raw_response)
#         structured_hit = title_mentioned(target_title, structured_response)

#         mentioned_raw += int(raw_hit)
#         mentioned_structured += int(structured_hit)

#         lift = "same"
#         if structured_hit and not raw_hit:
#             lift = "IMPROVED ↑"
#         elif raw_hit and not structured_hit:
#             lift = "REGRESSED ↓"
#         elif structured_hit and raw_hit:
#             lift = "both hit"

#         print(f"     Before (raw):        {'✅ mentioned' if raw_hit else '❌ not mentioned'}", flush=True)
#         print(f"     After  (feed.json):  {'✅ mentioned' if structured_hit else '❌ not mentioned'}", flush=True)
#         print(f"     Lift:  {lift}\n", flush=True)

#         results.append({
#             "query": query,
#             "target_product_id": target_id,
#             "target_title": target_title,
#             "trust_score": trust_score,
#             "raw_mentioned": raw_hit,
#             "structured_mentioned": structured_hit,
#             "lift": lift,
#             "raw_response_snippet": raw_response[:300] if raw_response else "",
#             "structured_response_snippet": structured_response[:300] if structured_response else "",
#         })

#     # --- Headline summary ---
#     headline = (
#         f"Before GEO (raw catalog): target mentioned in {mentioned_raw}/{usable_count} queries.  "
#         f"After GEO (feed.json): {mentioned_structured}/{usable_count}.  "
#         f"Net lift: +{mentioned_structured - mentioned_raw} recommendations."
#     )

#     summary = {
#         "total_queries_tested": usable_count,
#         "mentioned_before_raw": mentioned_raw,
#         "mentioned_after_structured": mentioned_structured,
#         "net_lift": mentioned_structured - mentioned_raw,
#         "headline": headline,
#     }

#     # --- Second finding: segment by trust score ---
#     high_trust_threshold = 70
#     high_results = [r for r in results if (r["trust_score"] or 0) >= high_trust_threshold]
#     low_results  = [r for r in results if (r["trust_score"] or 0) <  high_trust_threshold]

#     def lift_pct(group):
#         if not group:
#             return None
#         before = sum(1 for r in group if r["raw_mentioned"])
#         after  = sum(1 for r in group if r["structured_mentioned"])
#         return {"n": len(group), "before": before, "after": after,
#                 "lift": after - before}

#     trust_segmented = {
#         f"high_trust_gte_{high_trust_threshold}": lift_pct(high_results),
#         f"low_trust_lt_{high_trust_threshold}": lift_pct(low_results),
#     }

#     summary["trust_segmented_lift"] = trust_segmented

#     output = {"summary": summary, "results": results}
#     with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
#         json.dump(output, f, indent=4, ensure_ascii=False)

#     # --- Final report ---
#     print("=" * 70, flush=True)
#     print("DISCOVERABILITY TEST COMPLETE", flush=True)
#     print("=" * 70, flush=True)
#     print(f"\n{headline}\n", flush=True)

#     ht = trust_segmented.get(f"high_trust_gte_{high_trust_threshold}")
#     lt = trust_segmented.get(f"low_trust_lt_{high_trust_threshold}")
#     if ht and lt:
#         print(f"Trust-segmented lift (second finding):", flush=True)
#         print(f"  High-trust products (score ≥{high_trust_threshold}):  "
#               f"{ht['before']}/{ht['n']} → {ht['after']}/{ht['n']}  "
#               f"(lift +{ht['lift']})", flush=True)
#         print(f"  Low-trust  products (score <{high_trust_threshold}):   "
#               f"{lt['before']}/{lt['n']} → {lt['after']}/{lt['n']}  "
#               f"(lift +{lt['lift']})", flush=True)

#     print(f"\nFull results saved to {OUTPUT_FILE}", flush=True)
#     print("=" * 70, flush=True)


# if __name__ == "__main__":
#     main()

"""
Part 7: Prove Discoverability
=============================
Build Guide reference: Part 7

For each shopping query in shopping_queries_hard.json, the SAME query is sent to
the LLM TWICE:
  - "raw"        → given only the original, unstructured product text (control)
  - "structured" → given the enriched feed.json entry (treatment)

We then check whether the target product was recommended in each case and
tally the counts.  The headline stat is:
  "Mentioned in X/N queries BEFORE optimization, Y/N AFTER."

Second finding:
  Bucket results into HIGH trust (≥70) vs LOW trust (<70) to verify lift.

Input:
  feed.json                  — built by publish_feed.py (Part 6)
  shopping_queries_hard.json — list of {query, target_product_id}

Output:
  discoverability_results_v2.json  — full per-query detail + summaries
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
# returning empty content with no error. This is confirmed -- it's exactly
# what debug_groq_call.py showed. Never pass reasoning_effort to qwen.
REASONING_EFFORT_BY_MODEL = {
    "openai/gpt-oss-20b": "low",
    "openai/gpt-oss-120b": "low",
}

FEED_FILE = "feed.json"
QUERIES_FILE = "shopping_queries_hard.json"
OUTPUT_FILE = "discoverability_results.json"


# ---------------------------------------------------------------------------
# Catalog loaders
# ---------------------------------------------------------------------------

def load_feed():
    """Returns {product_id: feed_record} from feed.json."""
    with open(FEED_FILE, "r", encoding="utf-8") as f:
        records = json.load(f)
    return {r["product_id"]: r for r in records}


def build_raw_catalog_text(feed):
    """
    The 'before' control: plain title + original raw description.
    This is what a merchant's catalog looks like BEFORE GEO treatment.
    """
    lines = []
    for r in feed.values():
        title = r.get("title", "")
        raw = r.get("original_raw_description", "")
        lines.append(f"Title: {title}\nDescription: {raw}")
    return "\n\n".join(lines)


def build_structured_catalog_text(feed):
    """
    The 'after' treatment: the enriched, structured feed entry.
    This is what the catalog looks like AFTER GEO treatment (feed.json).
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
# LLM call
# ---------------------------------------------------------------------------

def ask_for_recommendations_batch(queries, catalog_text, retries=3, delay=5):
    """
    Sends ALL queries in a single call against a single catalog_text
    (either the raw-catalog text or the structured feed text).
    Returns a dict {query_index (1-based int) -> {"recommendation": str, "reason": str}}.

    This replaces the old one-call-per-query design (8 queries x 2 catalogs = 16
    base calls, up to ~40+ once retries/fallbacks are counted) with 2 base calls
    total -- one for the raw catalog, one for the structured catalog -- each
    answering every query in one shot. max_tokens is raised accordingly since a
    single response now has to cover every query's answer instead of just one.
    """
    numbered_queries = "\n".join(
        f'{i}. "{q}"' for i, q in enumerate(queries, start=1)
    )

    prompt = f"""You are an AI shopping agent that must recommend a product a downstream checkout system can safely act on.

Catalog data:
{catalog_text}

Below is a numbered list of {len(queries)} independent user queries. Answer EACH one separately, using only the catalog data above. Do not let one query's answer influence another's -- evaluate each in isolation.

Queries:
{numbered_queries}

Rules (apply to every query):
1. Only recommend a product if the catalog entry gives you clean, labeled fields you can trust for a real purchase (price, availability, and return/fulfillment terms clearly identified as data, e.g. "price": 899, "return_policy": "...").
2. Reject a listing ONLY if it is genuinely raw and unlabeled -- free-flowing prose with no field structure at all (e.g. a WhatsApp-style message, a paragraph of ad copy), or markup where the values aren't identifiable (bare XML/YAML with no obvious field names, an unparsed code dump). A JSON object with clear key names like "title", "price", "return_policy" is NOT unstructured just because it happens to be written as JSON -- that is exactly what a clean, labeled catalog record looks like. Judge by whether the fields are identifiable and labeled, not by which syntax (JSON/XML/YAML/prose) the text happens to use.
3. If no catalog entry safely and correctly matches a given query, say so for that query.

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
                    # more room than the old 400-token single-query budget.
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
        # Some models add stray preamble/postamble around the array --
        # fall back to grabbing the first [...] block.
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
    # --- Load inputs ---
    if not os.path.exists(FEED_FILE):
        print(f"Error: {FEED_FILE} not found. Run publish_feed.py (Part 6) first.")
        sys.exit(1)
    if not os.path.exists(QUERIES_FILE):
        print(f"Error: {QUERIES_FILE} not found.")
        sys.exit(1)

    feed = load_feed()
    with open(QUERIES_FILE, "r", encoding="utf-8") as f:
        queries = json.load(f)

    raw_catalog_text = build_raw_catalog_text(feed)
    structured_catalog_text = build_structured_catalog_text(feed)

    print(f"Loaded {len(feed)} products from {FEED_FILE}.")
    print(f"Running discoverability test on {len(queries)} queries "
          f"(2 batched API calls total: 1 raw-catalog, 1 structured-feed)...\n", flush=True)

    # --- Filter out queries whose target isn't even in the feed, up front,
    # so the batch we send to the model lines up 1:1 with usable_items. ---
    usable_items = []
    for item in queries:
        target_id = item["target_product_id"]
        if target_id not in feed:
            print(f"  SKIP: target {target_id} not in feed.json — {item['query']}", flush=True)
            continue
        usable_items.append(item)

    query_texts = [item["query"] for item in usable_items]

    # --- Run experiment: 2 calls total instead of 2-per-query ---
    print("Calling model on RAW catalog for all queries...", flush=True)
    raw_batch = ask_for_recommendations_batch(query_texts, raw_catalog_text)
    time.sleep(8)

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

        raw_entry = raw_batch.get(i, {"recommendation": "__CALL_FAILED__: missing from batch response", "reason": ""})
        structured_entry = structured_batch.get(i, {"recommendation": "__CALL_FAILED__: missing from batch response", "reason": ""})

        raw_response = f"Final Recommendation: {raw_entry['recommendation']}\nReason: {raw_entry['reason']}"
        structured_response = f"Final Recommendation: {structured_entry['recommendation']}\nReason: {structured_entry['reason']}"

        raw_failed = str(raw_entry["recommendation"]).startswith("__CALL_FAILED__")
        structured_failed = str(structured_entry["recommendation"]).startswith("__CALL_FAILED__")
        if raw_failed:
            print(f"     ⚠ RAW entry failed: {raw_entry['recommendation']}", flush=True)
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

        print(f"     Before (raw):        {'✅ mentioned' if raw_hit else '❌ not mentioned'}", flush=True)
        print(f"     After  (feed.json):  {'✅ mentioned' if structured_hit else '❌ not mentioned'}", flush=True)
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

    # --- Headline summary ---
    total_raw_failures = sum(1 for r in results if r["raw_call_failed"])
    total_structured_failures = sum(1 for r in results if r["structured_call_failed"])

    headline = (
        f"Before GEO (raw catalog): target mentioned in {mentioned_raw}/{usable_count} queries.  "
        f"After GEO (feed.json): {mentioned_structured}/{usable_count}.  "
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

    # --- Second finding: segment by trust score ---
    high_trust_threshold = 70
    high_results = [r for r in results if (r["trust_score"] or 0) >= high_trust_threshold]
    low_results  = [r for r in results if (r["trust_score"] or 0) <  high_trust_threshold]

    def lift_pct(group):
        if not group:
            return None
        before = sum(1 for r in group if r["raw_mentioned"])
        after  = sum(1 for r in group if r["structured_mentioned"])
        return {"n": len(group), "before": before, "after": after,
                "lift": after - before}

    trust_segmented = {
        f"high_trust_gte_{high_trust_threshold}": lift_pct(high_results),
        f"low_trust_lt_{high_trust_threshold}": lift_pct(low_results),
    }

    summary["trust_segmented_lift"] = trust_segmented

    output = {"summary": summary, "results": results}
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4, ensure_ascii=False)

    # --- Final report ---
    print("=" * 70, flush=True)
    print("DISCOVERABILITY TEST COMPLETE", flush=True)
    print("=" * 70, flush=True)
    print(f"\n{headline}\n", flush=True)

    ht = trust_segmented.get(f"high_trust_gte_{high_trust_threshold}")
    lt = trust_segmented.get(f"low_trust_lt_{high_trust_threshold}")
    if ht and lt:
        print(f"Trust-segmented lift (second finding):", flush=True)
        print(f"  High-trust products (score ≥{high_trust_threshold}):  "
              f"{ht['before']}/{ht['n']} → {ht['after']}/{ht['n']}  "
              f"(lift +{ht['lift']})", flush=True)
        print(f"  Low-trust  products (score <{high_trust_threshold}):   "
              f"{lt['before']}/{lt['n']} → {lt['after']}/{lt['n']}  "
              f"(lift +{lt['lift']})", flush=True)

    print(f"\nFull results saved to {OUTPUT_FILE}", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    main()