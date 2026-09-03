"""
Component 1: Ingest & Structure  (Build Guide Part 3)

Reads raw_catalog.json (30 products in deliberately heterogeneous formats —
plain prose, JSON blobs, XML, YAML, pipe-separated, WhatsApp-style, emoji-heavy)
and sends each product through an LLM extraction prompt that produces a clean,
consistent JSON record.

Key changes vs the original 15-product version:
  - price_currency field added: the new catalog has INR, USD, EUR and GBP prices;
    the original prompt hardcoded "price in INR" which would silently zero-out
    all non-INR prices.
  - Prompt explicitly lists the different input formats to prime the model to
    handle them (the model already handles them fine, but naming them reduces
    hallucination on malformed inputs like the XML or YAML entries).

Output: structured_catalog/<product_id>.json per product.
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

RAW_CATALOG_FILE = "raw_catalog.json"
OUTPUT_DIR = "structured_catalog"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def clean_and_parse_json(text):
    """
    Strip <think>...</think> reasoning blocks then extract the FIRST complete
    JSON object using json.JSONDecoder.raw_decode().

    raw_decode() stops at the end of the first valid object and ignores any
    trailing text or sibling objects — this prevents the 'Extra data' error
    that trips up the naive rfind('}') approach.
    """
    clean = text
    if "</think>" in text:
        clean = text[text.find("</think>") + len("</think>"):]

    start = clean.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in response. Snippet: {text[:200]}")

    candidate = clean[start:]
    candidate = re.sub(r",\s*([\]}])", r"\1", candidate)

    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(candidate)
    return obj


def extract_structured_data(product, retries=3, delay=6):
    """
    Send one raw product listing to the LLM and return a clean structured dict.
    Idempotent: skips the product if a valid output file already exists.
    """
    output_file = os.path.join(OUTPUT_DIR, f"{product['id']}.json")

    if os.path.exists(output_file):
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "category" in data and "price" in data:
                print(f"  Skipping {product['id']}: already structured.")
                return data
        except Exception:
            pass  # corrupt file — re-process

    print(f"  Structuring {product['id']}: '{product['title']}'...", flush=True)

    prompt = f"""You are a data ingestion engine for a modern AI commerce search system.
Your job is to read messy, unstructured product descriptions — which may arrive in many
different formats (plain prose, JSON blobs, XML, YAML, pipe-separated text, WhatsApp
messages, emoji-heavy listings, or structured data embedded in a raw string) — and
extract a standardized set of fields from each one.

Raw Product Listing:
Title: {product['title']}
Description: {product['raw_description']}

Extract the following fields into a precise JSON object:
1. "category": string (e.g. Skincare, Wearables, Food, Electronics, Health, Home,
   Accessories, Apparel, Components, etc.)
2. "target_audience": string (who this product is for; "missing" if not stated)
3. "key_attributes": list of strings (specifications, ingredients, or primary features)
4. "claims_made": list of strings (marketing claims — things the seller asserts about
   the product's benefits or capabilities that could be true or false)
5. "price": number (extract the numeric price value; null if missing or ambiguous)
6. "price_currency": string (the currency of the price: "INR", "USD", "EUR", "GBP",
   or "unknown" — look for ₹/Rs/Rupees → INR, $/USD → USD, €/EUR → EUR, £/GBP → GBP)
7. "availability": string (e.g. "In stock", "Low stock", "Out of stock", "missing")
8. "fulfillment_terms": object containing:
   - "delivery_window": string (e.g. "2-3 days", "Ships within 24 hours", "missing")
   - "return_policy": string (the exact return/refund/exchange policy as stated;
     "missing" if not mentioned; "No returns" if explicitly refused)

CRITICAL INSTRUCTIONS:
- If a field is genuinely absent from the source text, use "missing" (or null for numbers).
  Do NOT invent or guess values.
- "claims_made" should be extractable assertions that a fact-checker could evaluate
  (e.g. "Clinically tested", "Waterproof to 50m", "Blocks 90% of blue light").
  Do NOT include obvious product descriptions (e.g. "made of leather") as claims.
- Output ONLY valid JSON matching this exact schema. No markdown. No preamble.

{{
  "category": "...",
  "target_audience": "...",
  "key_attributes": [],
  "claims_made": [],
  "price": 0,
  "price_currency": "...",
  "availability": "...",
  "fulfillment_terms": {{
    "delivery_window": "...",
    "return_policy": "..."
  }}
}}
"""

    for model_to_try in [MODEL_NAME, FALLBACK_MODEL]:
        for attempt in range(retries):
            try:
                completion = client.chat.completions.create(
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a precise data extraction agent. Output only valid, parseable JSON matching the exact schema provided.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    model=model_to_try,
                    max_tokens=2048,
                    temperature=0.1,
                )
                response_text = completion.choices[0].message.content
                structured = clean_and_parse_json(response_text)

                # Attach identity fields back
                structured["product_id"] = product["id"]
                structured["original_title"] = product["title"]
                structured["original_raw_description"] = product["raw_description"]

                return structured

            except Exception as e:
                error_msg = str(e)
                print(f"    Attempt {attempt + 1} failed for {product['id']} using {model_to_try}: {error_msg}", flush=True)
                if "rate_limit" in error_msg.lower() or "429" in error_msg:
                    wait = delay * (attempt + 1) * 2
                    print(f"    Rate limit — waiting {wait}s...", flush=True)
                    time.sleep(wait)
                elif attempt < retries - 1:
                    time.sleep(delay)

    return None


def main():
    if not os.path.exists(RAW_CATALOG_FILE):
        print(f"Error: {RAW_CATALOG_FILE} not found.")
        sys.exit(1)

    with open(RAW_CATALOG_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)

    print(f"Found {len(products)} products in raw_catalog.json.")
    print("Beginning ingestion & structuring pipeline...\n")

    successful = 0
    for product in products:
        structured = extract_structured_data(product)

        if structured:
            out_path = os.path.join(OUTPUT_DIR, f"{product['id']}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(structured, f, indent=4, ensure_ascii=False)
            currency = structured.get("price_currency", "?")
            price = structured.get("price")
            print(f"  -> {product['id']} saved  (price={price} {currency})\n", flush=True)
            successful += 1
            time.sleep(2)  # gentle on rate limits between products
        else:
            print(f"  !! Failed to extract data for {product['id']}\n", flush=True)

    print("=" * 60)
    print("Ingestion & Structuring Complete!")
    print(f"Successfully processed {successful}/{len(products)} products.")
    print(f"Records saved to '{OUTPUT_DIR}/' directory.")
    print("=" * 60)


if __name__ == "__main__":
    main()
