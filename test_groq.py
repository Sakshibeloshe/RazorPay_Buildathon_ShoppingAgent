import os
import sys
from dotenv import load_dotenv
from groq import Groq

# Ensure Windows terminal can print unicode characters/emojis
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# Load environment variables from .env file
load_dotenv()

api_key = os.getenv("GROQ_API_KEY")

if not api_key or api_key == "your_groq_api_key_here":
    print("WARNING: Please replace 'your_groq_api_key_here' in the .env file with your actual Groq API key.")
    exit(1)

print("Initializing Groq client...")
try:
    client = Groq(api_key=api_key)

    models = [
        "qwen/qwen3.6-27b",
        "openai/gpt-oss-20b",
    ]
    successful_models = []

    for model in models:
        print(f"\nSending 'Hello' to Groq model: {model}...")
        try:
            chat_completion = client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": "Hello! Reply with a friendly, short greeting and confirm that the API connection is working.",
                    }
                ],
                model=model,
            )

            print("\n--- Response from Groq API ---")
            print(chat_completion.choices[0].message.content)
            print("-------------------------------")
            successful_models.append(model)
        except Exception as e:
            print(f"ERROR for {model}: {e}")

    print(f"\nConnection tests successful for {len(successful_models)}/{len(models)} model(s).")

except Exception as e:
    print(f"Error connecting to Groq API: {e}")
    print("\nPlease verify that your API key is correct and that you have an internet connection.")
