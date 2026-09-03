"""
Run this alone: python debug_groq_call.py

It does three things in isolation, each with the FULL exception printed
(not just str(e), which Groq/OpenAI-style clients often truncate):
  1. Confirms your API key authenticates at all (models.list — no chat, no tokens spent)
  2. One minimal chat call to qwen/qwen3.8-27b
  3. One minimal chat call to openai/gpt-oss-20b

Whichever step fails tells us exactly what's wrong:
  - Step 1 fails            -> API key is invalid/revoked, or .env isn't being read
  - Step 1 passes, 2/3 fail -> quota exhausted, rate limit, or a bad parameter
                                (the printed status code + body will say which)
"""
import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
api_key = os.getenv("GROQ_API_KEY")
print(f"Loaded key: {'YES, ' + str(len(api_key)) + ' chars' if api_key else 'NO KEY FOUND'}\n")

client = Groq(api_key=api_key)


def show_full_error(e):
    print(f"  Exception type: {type(e).__name__}")
    print(f"  str(e):         {e}")
    # groq-sdk (like openai-sdk) exceptions usually carry status_code + response body
    status = getattr(e, "status_code", None)
    if status is not None:
        print(f"  status_code:    {status}")
    body = getattr(e, "body", None) or getattr(e, "response", None)
    if body is not None:
        print(f"  body/response:  {body}")
    print()


print("=" * 60)
print("STEP 1: models.list() — tests auth only, no chat, no tokens")
print("=" * 60)
try:
    models = client.models.list()
    ids = [m.id for m in models.data]
    print(f"  OK — key is valid. {len(ids)} models visible.")
    for target in ("qwen/qwen3.8-27b", "openai/gpt-oss-20b"):
        print(f"  '{target}' in account's model list: {target in ids}")
except Exception as e:
    print("  FAILED — your API key itself is the problem, or .env isn't loading.")
    show_full_error(e)

print("=" * 60)
print("STEP 2: minimal chat call to qwen/qwen3.8-27b")
print("=" * 60)
try:
    r = client.chat.completions.create(
        messages=[{"role": "user", "content": "Say OK"}],
        model="qwen/qwen3.8-27b",
        max_tokens=20,
        reasoning_effort="low",
    )
    print(f"  OK — response: {r.choices[0].message.content!r}")
except Exception as e:
    print("  FAILED")
    show_full_error(e)

print("=" * 60)
print("STEP 3: minimal chat call to openai/gpt-oss-20b")
print("=" * 60)
try:
    r = client.chat.completions.create(
        messages=[{"role": "user", "content": "Say OK"}],
        model="openai/gpt-oss-20b",
        max_tokens=100,
        reasoning_effort="low",
    )
    print(f"  OK — response: {r.choices[0].message.content!r}")
except Exception as e:
    print("  FAILED")
    show_full_error(e)