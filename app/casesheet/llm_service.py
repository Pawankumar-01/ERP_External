
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

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
# The batch workflow deliberately has a *short*, deterministic provider path.
# Retrying five Groq models and then an unfunded OpenRouter account was what
# starved the Pulse request in the recorded Batch-2 incident.
GEMINI_PRIMARY_MODEL = os.getenv("GEMINI_PRIMARY_MODEL", "gemini-3.5-flash")
# Gemini reported in production that 2.5 Flash is retired for new users.
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash")
# Lone non-Gemini last resort (proven: 3 successes in the Batch-2 incident log).
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "qwen/qwen3.8-27b")
GEMINI_MODELS = list(dict.fromkeys((GEMINI_PRIMARY_MODEL, GEMINI_FALLBACK_MODEL)))
GROQ_MODELS = [GROQ_FALLBACK_MODEL]

# Explicit per-call output budgets. Pulse gets its own larger budget because it
# can contain every organ/system row in one response.
GEMINI_BATCH_MAX_TOKENS = int(os.getenv("GEMINI_BATCH_MAX_TOKENS", "6000"))
GEMINI_PULSE_MAX_TOKENS = int(os.getenv("GEMINI_PULSE_MAX_TOKENS", "5000"))

LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
DEFAULT_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS_DEFAULT", "1200"))
LLM_CONCURRENCY = max(1, int(os.getenv("CASE_LLM_CONCURRENCY", "1")))
LLM_TRANSIENT_RETRIES = max(0, int(os.getenv("CASE_LLM_TRANSIENT_RETRIES", "1")))
# Diagnostic switch. The response contains clinical data, so production may
# disable it after mapping investigation with CASE_LOG_LLM_OUTPUTS=false.
LOG_LLM_OUTPUTS = os.getenv("CASE_LOG_LLM_OUTPUTS", "true").strip().lower() in {"1", "true", "yes", "on"}
LLM_OUTPUT_LOG_LIMIT = max(1_000, int(os.getenv("CASE_LLM_OUTPUT_LOG_LIMIT", "40000")))


class LLMService:

    def __init__(self):
        # One request at a time is intentional for clinical batch extraction:
        # it prevents the six Batch-2 extractors from exhausting a shared
        # account's rate limit before the Pulse call gets its turn.
        self._semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    @staticmethod
    def _log_llm_output(label: str, stage: str, payload: Any) -> None:
        """Log an inspectable model response without ever logging credentials."""
        if not LOG_LLM_OUTPUTS:
            return
        try:
            text = payload if isinstance(payload, str) else json.dumps(
                payload, ensure_ascii=False, default=str, separators=(",", ":")
            )
        except Exception:
            text = repr(payload)
        if len(text) > LLM_OUTPUT_LOG_LIMIT:
            text = f"{text[:LLM_OUTPUT_LOG_LIMIT]}… [truncated for log]"
        logger.info("LLM_OUTPUT label=%s stage=%s payload=%s", label, stage, text)

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

        max_tokens = (
            GEMINI_PULSE_MAX_TOKENS
            if section == "pulse_diagnosis"
            else SECTION_MAX_TOKENS.get(section, DEFAULT_MAX_TOKENS)
        )
        raw_result = await self._safe_json_call(
            messages=messages,
            label=f"section:{section}",
            fallback={"_raw": transcript},
            max_tokens=max_tokens,
        )
        self._log_llm_output(f"section:{section}", "parsed_candidate", raw_result)
        # Provenance: _safe_json_call failure codes must survive the
        # deterministic fallback below. normalize/sanitize strip every "_*"
        # key by design, so capture the pre-normalize error here and re-attach
        # it after post-processing — otherwise a total provider failure would
        # be recorded as a clean "mapped" (Batch-2 incident).
        pre_error = raw_result.get("_error") if isinstance(raw_result, dict) else None
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
        if pre_error and isinstance(result, dict):
            # sanitize/normalize stripped the internal keys — restore the
            # failure provenance so fallback data is never mistaken for a
            # clean LLM extraction downstream.
            result["_error"] = pre_error
            if raw_result.get("_llm_output") and not result.get("_llm_output"):
                result["_llm_output"] = raw_result.get("_llm_output")
        if settings.CASE_APPLY_NOMENCLATURE:
            result = apply_nomenclature_to_section(result)
        if section == "pulse_diagnosis":
            self._log_llm_output(f"section:{section}", "reconciled_mapping", result)
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
            max_tokens=GEMINI_BATCH_MAX_TOKENS,
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
                # Only Pulse has a deterministic, source-grounded fallback.
                # A generic section fallback is merely raw text and must never
                # be represented as extracted clinical data.
                if section == "pulse_diagnosis" and self._has_source_backed_pulse(data):
                    return section, data, {"status": "mapped_needs_review", "error": str(data.get("_error"))}
                return section, {}, {"status": "failed", "error": str(data.get("_error"))}
            if not data or (isinstance(data, dict) and data.get("_reprompt")):
                return section, {}, {"status": "needs_review"}
            return section, data, {"status": "mapped"}

        tasks = [asyncio.create_task(_extract_one(section, snippet)) for section, snippet in snippets.items()]
        for task in asyncio.as_completed(tasks):
            section, data, status = await task
            section_statuses[section] = status
            if status["status"] in ("mapped", "mapped_needs_review"):
                results[section] = data
            if on_section_done:
                await on_section_done(
                    section,
                    data if status["status"] in ("mapped", "mapped_needs_review") else {},
                )

        # Progress must include visible no-content/review states too; otherwise
        # a completed job looks permanently stuck when a doctor omitted a field.
        for section in batch_sections:
            if section not in snippets and on_section_done:
                await on_section_done(section, {})

        results["_raw_section_transcripts"] = raw_section_transcripts
        results["_section_statuses"] = section_statuses
        return results

    @staticmethod
    def _has_source_backed_pulse(data: Any) -> bool:
        """True only when the deterministic raw Pulse parser recovered data."""
        if not isinstance(data, dict):
            return False
        systems = data.get("systems")
        if isinstance(systems, list):
            for row in systems:
                if not isinstance(row, dict) or not row.get("raw_phrase"):
                    continue
                if any(row.get(dosha) is not None for dosha in ("vata", "pitta", "kapha")):
                    return True
        overall = data.get("overall_vpk")
        return isinstance(overall, dict) and bool(overall.get("dominance"))

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

    def _validate_section_semantics(self, section: str, data: Dict[str, Any]) -> None:
        """Post-parse semantic validation (NEVER inside Pydantic schemas).

        The Gemini SDK suppresses ValidationError and returns ``parsed=None``
        silently instead of raising, so strict field/model validators would
        convert good-but-imperfect generations into total failures.  Schemas
        in structured_outputs.py are therefore intentionally permissive; the
        real checks live here and in sanitize_section()/normalize_pulse_
        diagnosis(), which run AFTER parsing.  Findings are logged only —
        this method never raises and never mutates.
        """
        try:
            if section == "pulse_diagnosis":
                systems = data.get("systems")
                if not isinstance(systems, list) or not systems:
                    logger.warning("Semantic check: section:pulse_diagnosis has no systems rows")
                    return
                valid_codes = {
                    "LI", "SI", "LISI", "CVS", "GIT", "IS", "PAN", "KUB",
                    "PRO", "RT", "LB", "GB", "LIV", "SS", "LSCS", "RB", "OBG",
                }
                valid_sev = {
                    "very_mild", "mild", "mild_moderate", "moderate",
                    "moderate_severe", "severe", None,
                }
                for row in systems:
                    if not isinstance(row, dict):
                        continue
                    if row.get("system") not in valid_codes:
                        logger.warning(
                            "Semantic check: pulse row has unknown system code %r",
                            row.get("system"),
                        )
                    for dosha in ("vata", "pitta", "kapha"):
                        if row.get(dosha) not in valid_sev:
                            logger.warning(
                                "Semantic check: pulse %s has out-of-vocab %s=%r",
                                row.get("system"), dosha, row.get(dosha),
                            )
            elif section == "vitals_anthropometry":
                for key in ("height_cm", "weight_kg", "bmi"):
                    val = data.get(key)
                    if val is None:
                        continue
                    try:
                        num = float(val)
                    except (TypeError, ValueError):
                        logger.warning("Semantic check: vitals %s=%r is not numeric", key, val)
                        continue
                    ranges = {"height_cm": (20, 300), "weight_kg": (1, 500), "bmi": (5, 100)}
                    lo, hi = ranges[key]
                    if not (lo <= num <= hi):
                        logger.warning("Semantic check: vitals %s=%r out of range", key, val)
        except Exception:
            logger.debug("Semantic validation skipped for %s", section, exc_info=True)

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
            raw = await self._call_llm(
                messages=messages,
                max_tokens=max_tokens,
                require_json=True,
                trace_label=label,
            )
            parsed = self._parse_json(raw)
            if parsed is None:
                finish = self._classify_empty_result(raw)
                if finish == "output_truncated":
                    logger.warning("LLM output truncated for %s; retrying once with ~2x tokens", label)
                    try:
                        raw_retry = await self._call_llm(
                            messages=messages,
                            max_tokens=int(max_tokens) * 2,
                            require_json=True,
                            trace_label=f"{label}:larger_budget_retry",
                        )
                    except RuntimeError:
                        raw_retry = None
                    if raw_retry:
                        retried = self._parse_json(raw_retry)
                        if retried is not None:
                            if isinstance(retried, dict) and label.startswith("section:"):
                                self._validate_section_semantics(label.split(":", 1)[1], retried)
                            logger.info("LLM JSON success for %s (after truncation retry)", label)
                            return retried
                        if self._classify_empty_result(raw_retry) != "output_truncated":
                            logger.warning("LLM returned non-JSON for %s", label)
                            return {**fallback, "_llm_output": raw_retry, "_error": "invalid_json"}
                    logger.warning("LLM output truncated for %s (retry exhausted)", label)
                    return {**fallback, "_llm_output": raw or "", "_error": "output_truncated"}
                if finish == "empty_response":
                    logger.warning("LLM returned empty response for %s", label)
                    return {**fallback, "_llm_output": raw or "", "_error": "empty_response"}
                logger.warning("LLM returned non-JSON for %s", label)
                return {**fallback, "_llm_output": raw, "_error": "invalid_json"}
            if isinstance(parsed, dict) and label.startswith("section:"):
                self._validate_section_semantics(label.split(":", 1)[1], parsed)
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

    @staticmethod
    def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
        """Use Retry-After when supplied; otherwise use a short capped backoff."""
        try:
            return min(15.0, max(1.0, float(response.headers.get("retry-after", ""))))
        except (TypeError, ValueError):
            return min(15.0, 1.5 * (2 ** attempt))

    async def _post_with_backoff(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        provider: str,
        model: str,
    ) -> httpx.Response:
        """Retry transient provider overload without fanning out requests."""
        response: Optional[httpx.Response] = None
        for attempt in range(LLM_TRANSIENT_RETRIES + 1):
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code not in (429, 503) or attempt == LLM_TRANSIENT_RETRIES:
                return response
            delay = self._retry_delay_seconds(response, attempt)
            logger.warning(
                "%s %s for model %s; retrying the same request in %.1fs (%s/%s)",
                provider,
                response.status_code,
                model,
                delay,
                attempt + 1,
                LLM_TRANSIENT_RETRIES,
            )
            await asyncio.sleep(delay)
        assert response is not None
        return response

    async def _call_llm(
        self,
        messages: list,
        max_tokens: int,
        require_json: bool = False,
        trace_label: Optional[str] = None,
    ) -> str:
        gemini_key = getattr(settings, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
        groq_key = getattr(settings, "GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
        if not gemini_key and not groq_key:
            raise RuntimeError("No API key set (GEMINI_API_KEY or GROQ_API_KEY) in .env")

        providers = []
        if gemini_key:
            providers.append(("Gemini", GEMINI_URL, {"Authorization": f"Bearer {gemini_key}", "Content-Type": "application/json"}, GEMINI_MODELS))
        if groq_key:
            providers.append(("Groq", GROQ_URL, {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}, GROQ_MODELS))

        async with self._semaphore:
            async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
                for provider, url, headers, models in providers:
                    for model in models:
                        payload = {
                            "model": model,
                            "messages": self._prepare_messages_for_model(messages, model),
                            "temperature": 0.1,
                            "max_tokens": int(max_tokens),
                            "response_format": {"type": "json_object"},
                        }
                        try:
                            response = await self._post_with_backoff(
                                client, url, headers, payload, provider, model
                            )
                            if response.status_code in (400, 404, 429, 500, 502, 503, 504):
                                logger.warning(
                                    "%s error %s for model %s: %s",
                                    provider,
                                    response.status_code,
                                    model,
                                    response.text[:300],
                                )
                                continue
                            response.raise_for_status()
                            data = response.json()
                            choice_msg = data.get("choices", [{}])[0].get("message", {})
                            content = choice_msg.get("content") or choice_msg.get("reasoning") or ""
                            if content:
                                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                            if not content:
                                logger.warning("%s returned an empty completion for model %s", provider, model)
                                continue
                            if trace_label:
                                self._log_llm_output(
                                    f"{trace_label} provider={provider} model={model}",
                                    "provider_response",
                                    content,
                                )
                            if require_json and self._parse_json(content) is None:
                                # A complete prose reply such as the Gemini
                                # 200 response in the Batch-2 incident must
                                # not terminate the provider chain. Preserve
                                # genuinely ragged JSON for _safe_json_call so
                                # it can perform the larger-budget retry.
                                if self._classify_empty_result(content) == "output_truncated":
                                    return content
                                logger.warning(
                                    "%s returned non-JSON for model %s; trying the next configured fallback",
                                    provider,
                                    model,
                                )
                                continue
                            usage = data.get("usage") or {}
                            logger.info(
                                "LLM extraction succeeded via %s (%s); usage prompt=%s completion=%s total=%s",
                                provider,
                                model,
                                usage.get("prompt_tokens"),
                                usage.get("completion_tokens"),
                                usage.get("total_tokens"),
                            )
                            return content
                        except (httpx.HTTPError, ValueError, IndexError, TypeError) as err:
                            logger.warning("%s request failed for model %s: %s", provider, model, err)
                            continue
        raise RuntimeError("Gemini primary/fallback and Groq fallback all failed or were rate-limited.")

    def _classify_empty_result(self, raw: Optional[str]) -> str:
        """Classify an unparseable LLM result.

        Returns one of: "output_truncated" | "empty_response" | "invalid_json".
        Truncation heuristics (unbalanced trailing JSON) are checked first so a
        MAX_TOKENS cutoff is never misreported as a chatty prose wrapper. The
        native Gemini path overrides this via finish_reason==MAX_TOKENS.
        """
        if not raw or not raw.strip():
            return "empty_response"
        text = raw.strip()
        # Balanced-or-prose → invalid_json; ragged trailing JSON → truncated.
        opens_c = text.count("{")
        closes_c = text.count("}")
        opens_s = text.count("[")
        closes_s = text.count("]")
        trailing_open = opens_c > closes_c or opens_s > closes_s
        ends_mid_value = bool(re.search(r'[:,\\[{,\\s]$', text))
        last_close = max(text.rfind("}"), text.rfind("]"))
        trailing_garbage = last_close != -1 and len(text) - last_close > 60
        if (trailing_open and not trailing_garbage) or (
            ends_mid_value and not text.rstrip().endswith(("}", "]", '"""'))
        ):
            return "output_truncated"
        return "invalid_json"

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
