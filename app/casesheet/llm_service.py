
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
    AMBIENT_BATCH_PROMPTS,
    AMBIENT_BATCH_GROUPS,
    AMBIENT_SUBBATCH_GROUPS,
    AMBIENT_SUBBATCH_PROMPTS,
    MIDDLEWARE_SEGMENTER_PROMPTS,
    BASE_RULES,
    _SECTION_FOOTER,
)
from app.casesheet.protocols import enrich_section_data
from app.casesheet.clinical_intelligence import (
    postprocess_section,
    normalize_pulse_diagnosis,
    with_variant_examples,
    with_batch_variant_examples,
    apply_nomenclature_to_section,
    with_protocol_taxonomy,
    is_protocol_section,
)
from app.config.settings import settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-2.5-flash",
]

GROQ_MODELS = [
    "qwen/qwen3.8-27b",
    "groq/compound-mini",
    "groq/compound",
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-120b",
]

PRIMARY_MODEL = settings.LLM_MODEL or os.getenv("LLM_MODEL", "openrouter/auto")

_raw_candidates = [
    PRIMARY_MODEL,
    "openrouter/auto",
    "meta-llama/llama-3.3-70b-instruct",
    "qwen/qwen-2.5-72b-instruct",
    "deepseek/deepseek-chat",
]
MODEL_CANDIDATES = []
for m in _raw_candidates:
    if m and m not in MODEL_CANDIDATES:
        MODEL_CANDIDATES.append(m)

LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
DEFAULT_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS_DEFAULT", "1200"))


class LLMService:

    def __init__(self):
        self._semaphore = asyncio.Semaphore(2)

    async def extract_section(self, section: str, transcript: str) -> Dict[str, Any]:
        if not transcript.strip():
            return {"_raw": "", "_error": "empty transcript"}

        prompt = SECTION_PROMPTS.get(section)
        if not prompt:
            logger.warning("No prompt defined for section: %s", section)
            return {"_raw": transcript, "_error": f"unknown section: {section}"}

        if settings.CASE_EMBED_VARIANT_EXAMPLES:
            prompt = with_variant_examples(section, prompt)
        if is_protocol_section(section):
            prompt = with_protocol_taxonomy(prompt)

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
        if (
            settings.CASE_SANITIZE
            and isinstance(raw_result, dict)
            and not raw_result.get("_error")
            and not raw_result.get("_reprompt")
        ):
            raw_result = postprocess_section(section, raw_result)
        result = enrich_section_data(section, raw_result)
        if section == "pulse_diagnosis":
            result = normalize_pulse_diagnosis(result, transcript)
        if settings.CASE_APPLY_NOMENCLATURE:
            result = apply_nomenclature_to_section(result)
        return result

    async def _preprocess_and_segment_batch_transcript(
        self,
        batch_index: int,
        transcript: str,
    ) -> Dict[str, Any]:
        if not transcript.strip():
            return {}

        prompt = MIDDLEWARE_SEGMENTER_PROMPTS.get(batch_index)
        if not prompt:
            return {}

        messages = [
            {"role": "system", "content": "You are a clinical speech cleaner, medical spell corrector, and section segmenter."},
            {"role": "user", "content": f"{prompt}\n\nRAW DOCTOR MONOLOGUE TRANSCRIPT:\n<<<\n{transcript}\n>>>"},
        ]

        res = await self._safe_json_call(
            messages=messages,
            label=f"middleware_segmenter_batch:{batch_index}",
            fallback={},
            # Batch 1 can contain twelve snippets.  The old 4k budget also
            # carried a duplicate full transcript and routinely truncated the
            # trailing sections; this result contains section evidence only.
            max_tokens=6000,
        )

        if isinstance(res, dict) and not res.get("_error"):
            logger.info(
                f"Stage 1 Middleware Segmenter success for Batch {batch_index}: "
                f"segmented keys={[k for k, v in res.items() if v]}"
            )
            return res
        return {}

    async def extract_batch_transcript(
        self,
        batch_index: int,
        transcript: str,
        on_section_done: Any = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Run the batch-only pipeline using each section's own evidence.

        The segmenter is a router, not an extractor.  Sending its complete
        cleaned batch back to every extractor was the root cause of fields
        appearing under a transcript that had never been used to create them.
        """
        if not transcript.strip():
            return {"_batch_error": "empty transcript"}

        batch_sections = AMBIENT_BATCH_GROUPS.get(batch_index, [])
        if not batch_sections:
            logger.warning("Invalid batch_index: %s", batch_index)
            return {"_batch_error": f"invalid batch index: {batch_index}"}

        segmented_map = await self._preprocess_and_segment_batch_transcript(batch_index, transcript)
        if not isinstance(segmented_map, dict) or segmented_map.get("_error"):
            return {"_batch_error": "segmenter returned no valid JSON"}

        results: Dict[str, Any] = {}
        raw_section_transcripts: Dict[str, str] = {}
        section_statuses: Dict[str, Dict[str, str]] = {}

        snippets: Dict[str, str] = {}
        seen_snippets: set[str] = set()
        for section in batch_sections:
            snippet = segmented_map.get(section)
            snippet = snippet.strip() if isinstance(snippet, str) else ""
            raw_section_transcripts[section] = snippet
            if not snippet:
                section_statuses[section] = {"status": "not_dictated"}
                continue
            fingerprint = re.sub(r"\W+", "", snippet).lower()
            if fingerprint and fingerprint in seen_snippets:
                # Identical evidence cannot safely belong to multiple clinical
                # sections.  Leave it visible for review instead of guessing.
                section_statuses[section] = {"status": "needs_review", "error": "duplicate segment evidence"}
                continue
            seen_snippets.add(fingerprint)
            if not self._is_grounded_segment(snippet, transcript):
                section_statuses[section] = {"status": "needs_review", "error": "segment contains unsupported text"}
                continue
            snippets[section] = snippet

        async def _extract_one(section: str, snippet: str) -> tuple[str, Any, Dict[str, str]]:
            data = await self.extract_section(section, snippet)
            if isinstance(data, dict) and data.get("_error"):
                return section, {}, {"status": "failed", "error": str(data.get("_error"))}
            return section, data, {"status": "mapped"}

        tasks = [asyncio.create_task(_extract_one(section, snippet)) for section, snippet in snippets.items()]
        for task in asyncio.as_completed(tasks):
            section, data, status = await task
            section_statuses[section] = status
            if status["status"] == "mapped":
                results[section] = data
            if on_section_done:
                await on_section_done(section, data if status["status"] == "mapped" else {})

        # Progress must include visible no-content/review states too; otherwise
        # a completed job looks permanently stuck when a doctor omitted a field.
        for section in batch_sections:
            if section not in snippets and on_section_done:
                await on_section_done(section, {})

        results["_raw_section_transcripts"] = raw_section_transcripts
        results["_section_statuses"] = section_statuses
        return results

    @staticmethod
    def _is_grounded_segment(snippet: str, raw_transcript: str) -> bool:
        """Reject segmenter inventions before they reach a clinical extractor."""
        raw_tokens = re.findall(r"[a-z0-9]+", raw_transcript.lower())
        snippet_tokens = re.findall(r"[a-z0-9]+", snippet.lower())
        if not snippet_tokens:
            return False
        raw_set = set(raw_tokens)
        meaningful = [token for token in snippet_tokens if len(token) > 2 or token.isdigit()]
        if not meaningful:
            return True
        overlap = sum(token in raw_set for token in meaningful) / len(meaningful)
        # Numbers/doses are high-risk: a corrected or invented number must not
        # be silently accepted merely because surrounding words match.
        numbers_are_grounded = all(token in raw_set for token in meaningful if token.isdigit())
        return overlap >= 0.85 and numbers_are_grounded

    async def extract_sections_from_full_transcript(
        self,
        transcript: str,
        on_section_done: Any = None,
    ) -> Dict[str, Dict[str, Any]]:
        if not transcript.strip():
            return {}

        logger.info("Starting parallel 3-domain batch monologue extraction...")

        tasks = [
            self.extract_batch_transcript(1, transcript, on_section_done),
            self.extract_batch_transcript(2, transcript, on_section_done),
            self.extract_batch_transcript(3, transcript, on_section_done),
        ]

        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        combined_results: Dict[str, Dict[str, Any]] = {}
        for idx, res in enumerate(batch_results):
            if isinstance(res, dict):
                combined_results.update(res)
            else:
                logger.error("Batch %d extraction failed: %s", idx + 1, res)

        return combined_results

    async def extract_section_with_image(
        self,
        section: str,
        transcript: str,
        image_bytes: bytes,
        filename: str = "image.jpg",
    ) -> Dict[str, Any]:
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
                result = enrich_section_data(section, raw_result)
                if settings.CASE_APPLY_NOMENCLATURE:
                    result = apply_nomenclature_to_section(result)
                return result
        except Exception as err:
            logger.warning("Vision AI call failed for section '%s' (%s) — using text extraction fallback", section, err)

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

    async def generate_text(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 500,
    ) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            return await self._call_llm(messages=messages, max_tokens=max_tokens)
        except Exception as exc:
            logger.error("generate_text failed: %s", exc)
            raise

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
                            "response_format": {"type": "json_object"},
                        }
                        try:
                            response = await client.post(GROQ_URL, headers=groq_headers, json=payload)
                            if response.status_code == 429:
                                logger.warning("Groq 429 rate limit for model %s. Trying next Groq model...", model)
                                continue
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
                    logger.warning("All OpenRouter models failed. Falling back to Gemini...")

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
                            "response_format": {"type": "json_object"},
                        }
                        try:
                            gemini_endpoint = f"{GEMINI_URL}?key={gemini_key}"
                            response = await client.post(gemini_endpoint, headers=gemini_headers, json=payload)
                            if response.status_code in (429, 503):
                                logger.warning("Gemini %s temporary overload for model %s. Retrying in 1s...", response.status_code, model)
                                await asyncio.sleep(1.0)
                                response = await client.post(gemini_endpoint, headers=gemini_headers, json=payload)
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
                        except httpx.HTTPError as err:
                            logger.warning("HTTP error on OpenRouter model %s: %s", model, err)
                            continue
        raise RuntimeError(f"All configured AI models (Gemini, Groq & OpenRouter) failed or were rate-limited.")

    def _parse_json(self, raw: str) -> Optional[Any]:
        """Accept one complete JSON document only; never salvage partial facts."""
        if not raw:
            return None
        cleaned = re.sub(r"```(?:json)?", "", raw).strip()
        cleaned = re.sub(r"```", "", cleaned).strip()
        try:
            return json.loads(cleaned)
        except Exception:
            # Partial JSON commonly means a truncated model response.  Saving
            # the fragments produces plausible but unsafe clinical data, so
            # callers must surface a failed/reviewable section instead.
            return None


llm_service = LLMService()
