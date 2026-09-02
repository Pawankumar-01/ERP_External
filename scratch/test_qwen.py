import asyncio
import os
import httpx
from app.config.settings import settings

async def test_groq_new_models():
    groq_key = getattr(settings, "GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
    headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
    
    test_models = [
        "qwen/qwen3.8-27b",
        "qwen/qwen3.6-27b",
        "openai/gpt-oss-120b",
        "groq/compound-mini",
        "groq/compound"
    ]
    
    messages = [
        {"role": "system", "content": "You are a clinical AI. Return JSON only."},
        {"role": "user", "content": 'Extract chief complaint into {"chief_complaint": "..."} from: Patient has severe headache for 3 days.'}
    ]

    async with httpx.AsyncClient(timeout=10.0) as client:
        for m in test_models:
            url = "https://api.groq.com/openai/v1/chat/completions"
            payload = {"model": m, "messages": messages, "temperature": 0.1, "response_format": {"type": "json_object"}}
            try:
                res = await client.post(url, headers=headers, json=payload)
                print(f"Groq [{m}]: HTTP {res.status_code} -> {res.text[:150]}")
            except Exception as e:
                print(f"Groq [{m}]: ERROR {e}")

if __name__ == "__main__":
    asyncio.run(test_groq_new_models())
