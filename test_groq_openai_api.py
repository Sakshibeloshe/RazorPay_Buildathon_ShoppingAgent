import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

try:
    r = client.chat.completions.create(
        messages=[{"role": "user", "content": "Say hello in 3 words."}],
        model="openai/gpt-oss-20b",
        max_tokens=200,
        reasoning_effort="low",
    )
    print("SUCCESS:", r.choices[0].message.content)
except Exception as e:
    print("FAILED:", repr(e))