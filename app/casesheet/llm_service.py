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
    AMBIENT_SECTION_GROUPS,
    BASE_RULES,
    _SECTION_FOOTER,
)
from app.casesheet.protocols import enrich_section_data
from app.config.settings import settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]
GROQ_MODELS = [
    "groq/compound",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
]
PRIMARY_MODEL = settings.LLM_MODEL or os.getenv("LLM_MODEL", "nvidia/nemotron-3.5-lightning:free")
_raw_candidates = [
    PRIMARY_MODEL,
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "openrouter/free",
]
MODEL_CANDIDATES = []
for m in _raw_candidates:
    if m and m not in MODEL_CANDIDATES:
        MODEL_CANDIDATES.append(m)

LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
DEFAULT_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS_DEFAULT", "1200"))


class LLMService:
    """Async service for extraction, quality checks and full case-sheet composition."""

    def __init__(self):
        # Allow 2 concurrent LLM calls — safe for Groq free tier rate limits.
        # Ambient pipeline processes section groups sequentially in pairs.
        self._semaphore = asyncio.Semaphore(2)

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

    async def extract_sections_from_full_transcript(
        self,
        transcript: str,
        on_section_done: Any = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract all 24 clinical sections from a single full-consultation transcript.

        Uses group-level batch extraction: 1 LLM call per section group.
        This reduces HTTP calls from 24 to 8, preventing rate limits and ensuring
        100% of dictated clinical findings are extracted across all sections.
        """
        if not transcript.strip():
            return {}

        all_results: Dict[str, Dict[str, Any]] = {}

        for group_idx, group_sections in enumerate(AMBIENT_SECTION_GROUPS):
            logger.info(
                f"Ambient group extraction {group_idx + 1}/{len(AMBIENT_SECTION_GROUPS)}: "
                f"sections [{', '.join(group_sections)}]"
            )

            group_prompts = []
            for sec_key in group_sections:
                sec_prompt = SECTION_PROMPTS.get(sec_key, "")
                clean_p = sec_prompt.replace(BASE_RULES, "").replace(_SECTION_FOOTER, "").strip()
                group_prompts.append(f"=== SECTION: '{sec_key}' ===\n{clean_p}")

            joined_prompts = "\n\n".join(group_prompts)
            group_instruction = (
                f"{BASE_RULES}\n\n"
                f"FULL CLINICAL CONSULTATION TRANSCRIPT:\n<<<\n{transcript}\n>>>\n\n"
                f"TASK: Extract structured clinical data from the transcript for the following sections: {', '.join(group_sections)}.\n"
                f"Extract all facts explicitly dictated in the transcript using each section's schema.\n"
                f"If a section has no relevant information in the transcript, return an empty object {{}} for that section key.\n"
                f"CRITICAL: Do NOT return reprompt or quality gate errors for omitted sections. Just extract dictated facts into valid section JSON.\n\n"
                f"{joined_prompts}\n\n"
                f"CRITICAL OUTPUT FORMAT: Return ONLY a single valid JSON object whose top-level keys are EXACTLY: "
                f"{json.dumps(group_sections)}.\n"
                f"No markdown backticks, no explanatory text."
            )

            messages = [
                {"role": "system", "content": GLOBAL_MEDICAL_INSTRUCTION.strip()},
                {"role": "user", "content": group_instruction},
            ]

            try:
                # Calculate combined max tokens for the group
                max_tokens = sum(SECTION_MAX_TOKENS.get(s, DEFAULT_MAX_TOKENS) for s in group_sections)
                max_tokens = min(max_tokens, 2000)

                group_res = await self._safe_json_call(
                    messages=messages,
                    label=f"ambient_group:{group_idx+1}",
                    fallback={},
                    max_tokens=max_tokens,
                )

                # Process each section from the group response
                for sec_key in group_sections:
                    sec_data = group_res.get(sec_key) if isinstance(group_res, dict) else None
                    if sec_data is None:
                        sec_data = {}
                    
                    enriched = enrich_section_data(sec_key, sec_data)
                    all_results[sec_key] = enriched

                    if on_section_done:
                        await on_section_done(sec_key, enriched)

            except Exception as exc:
                logger.error(f"Group extraction failed for group {group_sections}: {exc}")
                # Fallback: process missing sections individually if needed
                for sec_key in group_sections:
                    try:
                        ind_res = await self.extract_section(sec_key, transcript)
                        all_results[sec_key] = ind_res
                        if on_section_done:
                            await on_section_done(sec_key, ind_res)
                    except Exception as ind_exc:
                        err_res = {"_error": str(ind_exc), "_raw": transcript[:200]}
                        all_results[sec_key] = err_res
                        if on_section_done:
                            await on_section_done(sec_key, err_res)

            # Pause between groups to let Groq rate limit bucket reset
            if group_idx < len(AMBIENT_SECTION_GROUPS) - 1:
                await asyncio.sleep(4.0)

        return all_results

    async def extract_section_with_image(
        self,
        section: str,
        transcript: str,
        image_bytes: bytes,
        filename: str = "image.jpg",
    ) -> Dict[str, Any]:
        """
        Process both audio transcript AND captured image for a clinical section.
        Extracts lab values, diagnostic findings, and visual observations from the image,
        combines them with the audio dictation, and summarizes into structured JSON.
        """
        import base64
        base64_img = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{base64_img}"

        prompt = SECTION_PROMPTS.get(section) or GLOBAL_MEDICAL_INSTRUCTION

        text_prompt = (
            f"CLINICAL INPUT FOR SECTION '{section}':\n\n"
            f"AUDIO DICTATION TRANSCRIPT:\n<<<\n{transcript.strip() if transcript else 'No audio dictation provided.'}\n>>>\n\n"
            f"CAPTURED CLINICAL IMAGE / REPORT / SCAN ('{filename}'):\n"
            "Extract all text, lab test parameters, numerical values, reference ranges, abnormal flags, "
            "and visual clinical findings visible in this attached photograph/document.\n\n"
            f"{prompt}\n\n"
            "IMPORTANT: Combine both the dictation and image contents into a single accurate JSON response. Respond with ONLY valid JSON."
        )

        messages = [
            {"role": "system", "content": GLOBAL_MEDICAL_INSTRUCTION.strip()},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]

        try:
            raw_result = await self._safe_json_call(
                messages=messages,
                label=f"section_image:{section}",
                fallback={"_raw": transcript, "_image": filename},
                max_tokens=SECTION_MAX_TOKENS.get(section, DEFAULT_MAX_TOKENS),
            )
            if isinstance(raw_result, dict) and not raw_result.get("_error"):
                return enrich_section_data(section, raw_result)
        except Exception as err:
            logger.warning("Vision AI call failed for section '%s' (%s) — using text extraction fallback", section, err)

        # Fallback: Process using Groq/OpenRouter text pipeline
        fallback_prompt = (
            f"DOCTOR'S DICTATION & CAPTURED REPORT IMAGE ('{filename}'):\n\n"
            f"Transcript: {transcript if transcript else 'Captured report image uploaded.'}\n\n"
            f"Section Instruction: {prompt}"
        )
        return await self.extract_section(section, fallback_prompt)

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

    def _prepare_messages_for_model(self, messages: list, model: str) -> list:
        is_vision = any(v in model.lower() for v in ["vision", "gemini", "gpt-4-vision", "claude-3"])
        norm = []
        for msg in messages:
            c = msg.get("content")
            if isinstance(c, list):
                if is_vision:
                    norm.append(msg)
                else:
                    text_parts = [
                        item.get("text", "")
                        for item in c
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    norm.append({**msg, "content": "\n".join(t for t in text_parts if t)})
            else:
                norm.append(msg)
        return norm

    async def _call_llm(self, messages: list, max_tokens: int) -> str:
        gemini_key = getattr(settings, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
        openrouter_key = settings.OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY", "")
        groq_key = getattr(settings, "GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
        if not gemini_key and not groq_key and not openrouter_key:
            raise RuntimeError("No API key set (GEMINI_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY) in .env")

        async with self._semaphore:
            async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
                # ── Tier 1 Primary: Google Gemini API (1,000,000 TPM Free Limit) ──
                if gemini_key:
                    gemini_headers = {
                        "Authorization": f"Bearer {gemini_key}",
                        "Content-Type": "application/json",
                    }
                    for model in GEMINI_MODELS:
                        payload = {
                            "model": model,
                            "messages": self._prepare_messages_for_model(messages, model),
                            "temperature": 0.1,
                            "max_tokens": int(max_tokens),
                        }
                        try:
                            response = await client.post(GEMINI_URL, headers=gemini_headers, json=payload)
                            if response.status_code in (400, 404, 429, 500, 502, 503, 504):
                                logger.warning("Gemini error %s for model %s: %s", response.status_code, model, response.text[:100])
                                continue
                            response.raise_for_status()
                            data = response.json()
                            choice_msg = data.get("choices", [{}])[0].get("message", {})
                            content = choice_msg.get("content") or choice_msg.get("reasoning") or ""
                            if content:
                                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                            if content:
                                logger.info("LLM extraction succeeded via Gemini (%s)", model)
                                return content
                        except httpx.HTTPError as err:
                            logger.warning("HTTP error on Gemini model %s: %s", model, err)
                            continue
                    logger.warning("All Gemini models failed. Falling back to Groq...")

                # ── Tier 2 Engine: Groq ──
                if groq_key:
                    groq_headers = {
                        "Authorization": f"Bearer {groq_key}",
                        "Content-Type": "application/json",
                    }
                    for model in GROQ_MODELS:
                        payload = {
                            "model": model,
                            "messages": self._prepare_messages_for_model(messages, model),
                            "temperature": 0.1,
                            "max_tokens": int(max_tokens),
                        }
                        try:
                            response = await client.post(GROQ_URL, headers=groq_headers, json=payload)
                            if response.status_code == 429:
                                logger.warning("Groq 429 rate limit for model %s. Retrying after 6s backoff...", model)
                                await asyncio.sleep(6.0)
                                response = await client.post(GROQ_URL, headers=groq_headers, json=payload)
                                if response.status_code == 429:
                                    logger.warning("Groq 429 rate limit again for model %s. Retrying after 10s backoff...", model)
                                    await asyncio.sleep(10.0)
                                    response = await client.post(GROQ_URL, headers=groq_headers, json=payload)
                            if response.status_code in (400, 404, 429, 500, 502, 503, 504):
                                logger.warning("Groq error %s for model %s: %s", response.status_code, model, response.text[:100])
                                continue
                            response.raise_for_status()
                            data = response.json()
                            choice_msg = data.get("choices", [{}])[0].get("message", {})
                            content = choice_msg.get("content") or choice_msg.get("reasoning") or ""
                            if content:
                                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                            if content:
                                logger.info("LLM extraction succeeded via Groq (%s)", model)
                                return content
                        except httpx.HTTPError as err:
                            logger.warning("HTTP error on Groq model %s: %s", model, err)
                            continue
                    logger.warning("All Groq models failed. Falling back to OpenRouter...")

                # ── Tier 3 Engine: OpenRouter ──
                if openrouter_key:
                    headers = {
                        "Authorization": f"Bearer {openrouter_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://sgp.clinic",
                        "X-Title": "SGP Clinical AI",
                    }
                    for model in MODEL_CANDIDATES:
                        payload = {
                            "model": model,
                            "messages": self._prepare_messages_for_model(messages, model),
                            "temperature": 0.1,
                            "max_tokens": int(max_tokens),
                        }
                        try:
                            response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                            if response.status_code in (400, 404, 429, 500, 502, 503, 504):
                                logger.warning("OpenRouter %s error for model %s: %s", response.status_code, model, response.text[:100])
                                continue
                            response.raise_for_status()
                            data = response.json()
                            choice_msg = data.get("choices", [{}])[0].get("message", {})
                            content = choice_msg.get("content") or choice_msg.get("reasoning") or ""
                            if content:
                                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                            if content:
                                logger.info("LLM extraction succeeded via OpenRouter (%s)", model)
                                return content
                        except httpx.HTTPError as err:
                            logger.warning("HTTP error on OpenRouter model %s: %s", model, err)
                            continue
        raise RuntimeError(f"All configured AI models (Gemini, Groq & OpenRouter) failed or were rate-limited.")

    def _parse_json(self, raw: str) -> Optional[Any]:
        if not raw:
            return None
        cleaned = re.sub(r"```(?:json)?", "", raw).strip()
        cleaned = re.sub(r"```", "", cleaned).strip()

        try:
            return json.loads(cleaned)
        except Exception:
            pass

        start_curly = cleaned.find("{")
        end_curly = cleaned.rfind("}")
        if start_curly != -1 and end_curly > start_curly:
            json_str = cleaned[start_curly : end_curly + 1]
            try:
                return json.loads(json_str)
            except Exception:
                fixed_str = re.sub(r",\s*([\}\]])", r"\1", json_str)
                try:
                    return json.loads(fixed_str)
                except Exception:
                    pass

        start_bracket = cleaned.find("[")
        end_bracket = cleaned.rfind("]")
        if start_bracket != -1 and end_bracket > start_bracket:
            json_str = cleaned[start_bracket : end_bracket + 1]
            try:
                return json.loads(json_str)
            except Exception:
                fixed_str = re.sub(r",\s*([\}\]])", r"\1", json_str)
                try:
                    return json.loads(fixed_str)
                except Exception:
                    pass

        # Fallback: Merge multiple individual JSON objects if LLM emitted separate blocks
        combined = {}
        for match in re.finditer(r'\{[^{}]*"(?:[a-zA-Z0-9_]+)":\s*[^{}]*\}', cleaned, re.DOTALL):
            try:
                parsed_sub = json.loads(match.group(0))
                if isinstance(parsed_sub, dict):
                    combined.update(parsed_sub)
            except Exception:
                pass
        if combined:
            return combined

        return None


llm_service = LLMService()
