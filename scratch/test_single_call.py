import asyncio
import logging
logging.basicConfig(level=logging.INFO)

from app.casesheet.llm_service import llm_service

async def test():
    res = await llm_service.extract_section(
        "chief_complaint",
        "Patient complains of severe lower back pain radiating to left leg for 3 months. Worse with prolonged sitting."
    )
    print("\n--- EXTRACTION RESULT ---")
    print(res)

if __name__ == "__main__":
    asyncio.run(test())
