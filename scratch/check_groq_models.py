import asyncio
import os
import httpx
from app.config.settings import settings

async def check_groq():
    groq_key = getattr(settings, "GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
    headers = {"Authorization": f"Bearer {groq_key}"}
    async with httpx.AsyncClient() as client:
        res = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
        print("Groq Models Response:", res.status_code)
        if res.status_code == 200:
            data = res.json()
            models = [m["id"] for m in data.get("data", [])]
            print("\nACTIVE GROQ MODELS:")
            for m in sorted(models):
                print(f" - {m}")

if __name__ == "__main__":
    asyncio.run(check_groq())
