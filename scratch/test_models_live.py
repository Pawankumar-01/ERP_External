import asyncio
import os
import httpx
from app.config.settings import settings

async def test_live_models():
    gemini_key = getattr(settings, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
    groq_key = getattr(settings, "GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
    openrouter_key = settings.OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY", "")

    print(f"GEMINI KEY: {gemini_key[:8]}... (len={len(gemini_key)})")
    print(f"GROQ KEY: {groq_key[:8]}... (len={len(groq_key)})")
    print(f"OPENROUTER KEY: {openrouter_key[:8]}... (len={len(openrouter_key)})")

    test_messages = [
        {"role": "system", "content": "You are a clinical AI. Return JSON only."},
        {"role": "user", "content": "Extract chief complaint from: Patient complains of severe headache for 3 days."}
    ]

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Test Gemini models via Native & OpenAI endpoint
        gemini_test_models = [
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-2.0-flash-exp",
            "gemini-2.0-flash-lite-preview-02-05",
        ]
        print("\n--- TESTING GEMINI MODELS ---")
        if gemini_key:
            for m in gemini_test_models:
                # 1. Test OpenAI path
                url_openai = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                headers = {"Authorization": f"Bearer {gemini_key}", "Content-Type": "application/json"}
                payload = {"model": m, "messages": test_messages, "temperature": 0.1}
                try:
                    res = await client.post(url_openai, headers=headers, json=payload)
                    print(f"Gemini OpenAI [{m}]: HTTP {res.status_code} - {res.text[:120]}")
                except Exception as e:
                    print(f"Gemini OpenAI [{m}]: EXCEPTION {e}")

                # 2. Test Native REST path
                url_native = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={gemini_key}"
                native_payload = {
                    "contents": [{"parts": [{"text": "Extract chief complaint from: Patient complains of severe headache for 3 days."}]}],
                    "generationConfig": {"response_mime_type": "application/json"}
                }
                try:
                    res = await client.post(url_native, json=native_payload)
                    print(f"Gemini Native [{m}]: HTTP {res.status_code} - {res.text[:120]}")
                except Exception as e:
                    print(f"Gemini Native [{m}]: EXCEPTION {e}")

        print("\n--- TESTING GROQ MODELS ---")
        groq_test_models = [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "llama3-70b-8192",
            "llama3-8b-8192",
            "qwen-2.5-coder-32b",
            "deepseek-r1-distill-llama-70b"
        ]
        if groq_key:
            headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
            for m in groq_test_models:
                url = "https://api.groq.com/openai/v1/chat/completions"
                payload = {"model": m, "messages": test_messages, "temperature": 0.1, "response_format": {"type": "json_object"}}
                try:
                    res = await client.post(url, headers=headers, json=payload)
                    print(f"Groq [{m}]: HTTP {res.status_code} - {res.text[:120]}")
                except Exception as e:
                    print(f"Groq [{m}]: EXCEPTION {e}")

        print("\n--- TESTING OPENROUTER MODELS ---")
        openrouter_test_models = [
            "openrouter/free",
            "google/gemini-2.0-flash-lite-preview-02-05:free",
            "meta-llama/llama-3.1-8b-instruct:free",
            "mistralai/mistral-7b-instruct:free",
            "qwen/qwen-2.5-7b-instruct:free"
        ]
        if openrouter_key:
            headers = {
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://sgp.clinic",
            }
            for m in openrouter_test_models:
                url = "https://openrouter.ai/api/v1/chat/completions"
                payload = {"model": m, "messages": test_messages, "temperature": 0.1}
                try:
                    res = await client.post(url, headers=headers, json=payload)
                    print(f"OpenRouter [{m}]: HTTP {res.status_code} - {res.text[:120]}")
                except Exception as e:
                    print(f"OpenRouter [{m}]: EXCEPTION {e}")

if __name__ == "__main__":
    asyncio.run(test_live_models())
