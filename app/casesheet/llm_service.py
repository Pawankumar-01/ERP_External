"""
LLM Service V1 - Clinical Section Extraction + Case Sheet Composition
=====================================================================

Drop-in replacement candidate for:
    app/casesheet/llm_service.py

Compatible with the existing router because extract_section(section, transcript)
keeps the same signature. Adds:
    - dynamic max_tokens from prompts.SECTION_MAX_TOKENS
    - compose_from_draft(prompt_name, draft, patient_context=None)
    - run_quality_check(check_name, draft, patient_context=None)

All failures return usable JSON instead of raising to the caller.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, Optional

import httpx

from app.casesheet.prompts import (
    SECTION_PROMPTS,
    COMPOSER_PROMPTS,
    QUALITY_PROMPTS,
    SECTION_MAX_TOKENS,
    GLOBAL_MEDICAL_INSTRUCTION,
)
from app.casesheet.protocols import enrich_section_data
from app.config.settings import settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PRIMARY_MODEL = settings.LLM_MODEL or os.getenv("LLM_MODEL", "google/gemma-4-31b-it:free")
_raw_candidates = [
    PRIMARY_MODEL,
    "google/gemma-4-31b-it:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-2-9b-it:free",
    "microsoft/phi-4:free",
    "mistralai/mistral-7b-instruct:free",
    "google/gemma-4-26b-a4b-it:free",
]
MODEL_CANDIDATES = []
for m in _raw_candidates:
    if m and m not in MODEL_CANDIDATES:
        MODEL_CANDIDATES.append(m)

LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
DEFAULT_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS_DEFAULT", "2000"))


class LLMService:
    """Async service for extraction, quality checks and full case-sheet composition."""

    def __init__(self):
        # Serialize LLM calls to prevent OpenRouter free tier concurrency 429 errors
        self._semaphore = asyncio.Semaphore(1)

    async def extract_section(self, section: str, transcript: str) -> Dict[str, Any]:
        """
        Send a single section transcript to LLM and return structured JSON.
        This preserves the existing service contract used by router.py.
        """
        if not transcript.strip():
            return {"_raw": "", "_error": "empty transcript"}

        prompt = SECTION_PROMPTS.get(section)
        if not prompt:
            logger.warning("No prompt defined for section: %s", section)
            return {"_raw": transcript, "_error": f"unknown section: {section}"}

        messages = [
            {"role": "system", "content": GLOBAL_MEDICAL_INSTRUCTION.strip()},
            {
                "role": "user",
                "content": (
                    f"TRANSCRIPT:\n<<<\n{transcript}\n>>>\n\n"
                    f"{prompt}\n\n"
                    "IMPORTANT: Respond with ONLY valid JSON. No markdown, no explanation."
                ),
            },
        ]

        raw_result = await self._safe_json_call(
            messages=messages,
            label=f"section:{section}",
            fallback={"_raw": transcript},
            max_tokens=SECTION_MAX_TOKENS.get(section, DEFAULT_MAX_TOKENS),
        )
        return enrich_section_data(section, raw_result)

    async def compose_from_draft(
        self,
        prompt_name: str,
        draft: Dict[str, Any],
        patient_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Compose a full case sheet, doctor review summary or ERP field mapper output.
        prompt_name examples:
            - final_case_sheet
            - doctor_review_summary
            - patient_friendly_summary
            - erpnext_field_mapper
        """
        prompt = COMPOSER_PROMPTS.get(prompt_name)
        if not prompt:
            return {"_error": f"unknown composer prompt: {prompt_name}"}

        payload = {
            "patient_context": patient_context or {},
            "draft": draft or {},
        }

        messages = [
            {"role": "system", "content": GLOBAL_MEDICAL_INSTRUCTION.strip()},
            {
                "role": "user",
                "content": (
                    f"FULL_CASESHEET_DRAFT_JSON:\n<<<\n{json.dumps(payload, ensure_ascii=False, default=str)}\n>>>\n\n"
                    f"{prompt}\n\n"
                    "IMPORTANT: Respond with ONLY valid JSON. No markdown, no explanation."
                ),
            },
        ]

        return await self._safe_json_call(
            messages=messages,
            label=f"composer:{prompt_name}",
            fallback={"_raw_draft": draft},
            max_tokens=SECTION_MAX_TOKENS.get(prompt_name, 6000),
        )

    async def run_quality_check(
        self,
        check_name: str,
        draft: Dict[str, Any],
        patient_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run a full-draft quality check.
        check_name examples:
            - missing_information_check
            - contradiction_check
            - red_flag_check
        """
        prompt = QUALITY_PROMPTS.get(check_name)
        if not prompt:
            return {"_error": f"unknown quality prompt: {check_name}"}

        payload = {
            "patient_context": patient_context or {},
            "draft": draft or {},
        }

        messages = [
            {"role": "system", "content": GLOBAL_MEDICAL_INSTRUCTION.strip()},
            {
                "role": "user",
                "content": (
                    f"FULL_CASESHEET_DRAFT_JSON:\n<<<\n{json.dumps(payload, ensure_ascii=False, default=str)}\n>>>\n\n"
                    f"{prompt}\n\n"
                    "IMPORTANT: Respond with ONLY valid JSON. No markdown, no explanation."
                ),
            },
        ]

        return await self._safe_json_call(
            messages=messages,
            label=f"quality:{check_name}",
            fallback={"_raw_draft": draft},
            max_tokens=SECTION_MAX_TOKENS.get(check_name, 3000),
        )

    async def _safe_json_call(
        self,
        messages: list,
        label: str,
        fallback: Dict[str, Any],
        max_tokens: int,
    ) -> Dict[str, Any]:
        try:
            raw = await self._call_llm(messages=messages, max_tokens=max_tokens)
            parsed = self._parse_json(raw)
            if parsed is None:
                logger.warning("LLM returned non-JSON for %s", label)
                return {**fallback, "_llm_output": raw, "_error": "invalid_json"}
            logger.info("LLM JSON success for %s", label)
            return parsed
        except httpx.TimeoutException:
            logger.error("LLM timeout for %s", label)
            return {**fallback, "_error": "llm_timeout"}
        except Exception as exc:
            logger.error("LLM call failed for %s: %s", label, exc)
            return {**fallback, "_error": str(exc)}

    async def _call_llm(self, messages: list, max_tokens: int) -> str:
        openrouter_key = settings.OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY", "")
        if not openrouter_key:
            raise RuntimeError("OPENROUTER_API_KEY not set in .env")

        headers = {
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://sgp.clinic",
            "X-Title": "SGP Clinical AI",
        }

        async with self._semaphore:
            async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
                for pass_num in (1, 2):
                    for model in MODEL_CANDIDATES:
                        payload = {
                            "model": model,
                            "messages": messages,
                            "temperature": 0.1,
                            "max_tokens": int(max_tokens),
                        }
                        try:
                            response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                            if response.status_code in (400, 404, 429, 502, 503, 504):
                                logger.warning("OpenRouter %s error (pass %s) for model %s: %s", response.status_code, pass_num, model, response.text)
                                if response.status_code == 429:
                                    # Longer backoff to let OpenRouter's token bucket reset
                                    await asyncio.sleep(2.5)
                                elif response.status_code in (502, 503, 504):
                                    await asyncio.sleep(1.0)
                                continue
                            response.raise_for_status()
                            data = response.json()
                            return data["choices"][0]["message"]["content"]
                        except httpx.HTTPError as err:
                            logger.warning("HTTP error on model %s (pass %s): %s", model, pass_num, err)
                            await asyncio.sleep(1.0)
                            continue

                    if pass_num == 1:
                        logger.warning("Pass 1 through all OpenRouter free models exhausted. Waiting 4s before Pass 2...")
                        await asyncio.sleep(4.0)

        raise RuntimeError(f"All configured OpenRouter models ({', '.join(MODEL_CANDIDATES)}) failed or were rate-limited (429/5xx).")

    def _parse_json(self, raw: str) -> Optional[Any]:
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        json_match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        return None


llm_service = LLMService()
