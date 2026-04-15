"""
LLM Service — Clinical Section Extraction
──────────────────────────────────────────
Calls OpenRouter (or Groq) to format transcribed doctor dictation
into structured JSON sections using clinical prompts.

Error handling:
- If LLM returns invalid JSON → save raw transcript as fallback
- If LLM call fails entirely → save raw transcript as fallback
- Never raise to caller — always return something usable
"""

import json
import logging
import os
import re
from typing import Any, Dict, Optional

import httpx

from app.casesheet.prompts import SECTION_PROMPTS, BASE_RULES, GLOBAL_MEDICAL_INSTRUCTION

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY  = os.getenv("OPENROUTER_API_KEY", "")

# Free model that works well for clinical JSON extraction
DEFAULT_MODEL   = os.getenv("LLM_MODEL", "mistralai/mistral-7b-instruct:free")

# Timeout for LLM calls — clinical extraction can be slow on free tier
LLM_TIMEOUT     = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))


class LLMService:
    """
    Async service for clinical section extraction via OpenRouter.
    One method: extract_section(section, transcript) → dict
    """

    async def extract_section(
        self, section: str, transcript: str
    ) -> Dict[str, Any]:
        """
        Send transcript to LLM for structured extraction of a clinical section.
        Falls back to raw transcript on any failure.
        """
        if not transcript.strip():
            return {"_raw": "", "_error": "empty transcript"}

        prompt = SECTION_PROMPTS.get(section)
        if not prompt:
            logger.warning(f"No prompt defined for section: {section}")
            return {"_raw": transcript, "_error": f"unknown section: {section}"}

        messages = [
            {
                "role": "system",
                "content": GLOBAL_MEDICAL_INSTRUCTION.strip(),
            },
            {
                "role": "user",
                "content": (
                    f"TRANSCRIPT:\n<<<\n{transcript}\n>>>\n\n"
                    f"{prompt}\n\n"
                    "IMPORTANT: Respond with ONLY valid JSON. No markdown, no explanation."
                ),
            },
        ]

        try:
            raw = await self._call_llm(messages)
            parsed = self._parse_json(raw)

            if parsed is None:
                logger.warning(
                    f"LLM returned non-JSON for section '{section}', saving raw transcript"
                )
                return {"_raw": transcript, "_llm_output": raw}

            logger.info(f"Section '{section}' extracted successfully")
            return parsed

        except httpx.TimeoutException:
            logger.error(f"LLM timeout for section '{section}'")
            return {"_raw": transcript, "_error": "llm_timeout"}

        except Exception as e:
            logger.error(f"LLM extraction failed for section '{section}': {e}")
            return {"_raw": transcript, "_error": str(e)}

    async def _call_llm(self, messages: list) -> str:
        """Make the HTTP call to OpenRouter and return raw content string."""
        if not OPENROUTER_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY not set in .env — "
                "LLM extraction unavailable"
            )

        headers = {
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://sgp.clinic",
            "X-Title":       "SGP Clinical AI",
        }

        payload = {
            "model":       DEFAULT_MODEL,
            "messages":    messages,
            "temperature": 0.1,     # low temp for consistent structured output
            "max_tokens":  1500,
        }

        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
            resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        return data["choices"][0]["message"]["content"]

    def _parse_json(self, raw: str) -> Optional[Any]:
        """
        Parse JSON from LLM output.
        Handles common LLM habits: markdown fences, leading/trailing text.
        Returns None if parsing fails completely.
        """
        # Strip markdown code fences
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()

        # Try direct parse
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON object/array from within the text
        json_match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        return None


# Module-level singleton
llm_service = LLMService()
