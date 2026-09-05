"""
Isolated Groq API key test -- bypasses the dashboard, retries, and the
buyer agent entirely. Run this directly:

    python test_groq_key.py

If this fails, the problem is your GROQ_API_KEY / .env setup, not anything
in purchase_pipeline.py or dashboard_app.py.
"""

import os
from dotenv import load_dotenv

load_dotenv()

key = os.getenv("GROQ_API_KEY")

if not key:
    print("FAIL: GROQ_API_KEY not found in .env at all.")
    print("      Check that .env is in the same folder you're running this from,")
    print("      and that the line reads exactly: GROQ_API_KEY=gsk_...")
    raise SystemExit(1)

print(f"Found a key: {key[:8]}...{key[-4:]}  (length {len(key)})")

if key.strip() != key:
    print("WARNING: key has leading/trailing whitespace -- .env parsing usually")
    print("         strips this automatically, but worth checking the raw file.")

from groq import Groq
import groq as groq_module

client = Groq(api_key=key)

try:
    resp = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[{"role": "user", "content": "Say OK"}],
        max_tokens=10,
    )
    print("SUCCESS -- the key and model both work.")
    print("Response:", resp.choices[0].message.content)
except groq_module.AuthenticationError as e:
    print("FAIL -- AuthenticationError. This confirms the key itself is invalid/revoked:")
    print(" ", str(e))
    print()
    print("Fix: go to https://console.groq.com/keys, generate a NEW key, and")
    print("     replace GROQ_API_KEY in your .env with it exactly (no quotes needed).")
except groq_module.NotFoundError as e:
    print("FAIL -- NotFoundError. The key works, but this specific model isn't")
    print("        available to your account:")
    print(" ", str(e))
except groq_module.RateLimitError as e:
    print("FAIL -- RateLimitError. The key works, but you've hit a quota/rate limit:")
    print(" ", str(e))
except Exception as e:
    print(f"FAIL -- unexpected error ({type(e).__name__}):")
    print(" ", str(e))