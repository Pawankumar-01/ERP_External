"""Dedicated plain-text LLM client for WhatsApp patient support.

This service is intentionally isolated from the casesheet extraction service.
It never requests JSON mode and never uses the casesheet semaphore, model
circuit breakers, or provider fallback chain.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config.settings import settings


logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


class WhatsAppLLMService:
    @staticmethod
    def clean_response(raw: str) -> str:
        """Convert accidental JSON/prose wrappers into WhatsApp-ready text."""
        value = (raw or "").strip()
        value = re.sub(r"<think>.*?</think>", "", value, flags=re.DOTALL).strip()

        fenced = re.fullmatch(r"```(?:json|text)?\s*(.*?)\s*```", value, re.DOTALL | re.IGNORECASE)
        if fenced:
            value = fenced.group(1).strip()

        try:
            decoded: Any = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = None

        if isinstance(decoded, dict):
            for key in ("response", "answer", "message", "content", "text"):
                candidate = decoded.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    value = candidate.strip()
                    break
        elif isinstance(decoded, str):
            value = decoded.strip()

        value = value.strip().strip("`").strip()
        # WhatsApp text messages support more, but keeping a margin prevents a
        # verbose free model from producing an invalid or unreadable response.
        if len(value) > 3500:
            value = value[:3497].rstrip() + "..."
        return value

    async def generate_answer(self, *, query: str, knowledge_context: str) -> str:
        api_key = (settings.OPENROUTER_API_KEY or "").strip()
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        model = (settings.WHATSAPP_LLM_MODEL or "openrouter/free").strip()
        system_prompt = """You are the Novadigm Health WhatsApp information assistant.

Answer the user's question clearly and concisely in plain human-readable text.
Use the approved guidance supplied below whenever it is relevant. Do not invent
Novadigm services, medicine instructions, treatment mechanisms, prices,
doctors, outcomes or organizational facts.

For Docture-Poly, never claim that the platform independently diagnoses,
treats, cures, mitigates or prevents disease, and never describe an estimated
OMICS trend as an exact laboratory result. Do not claim a specific accuracy,
regulatory approval, certification, price, scan duration, data-privacy practice
or hardware capability unless it is explicitly present in the approved
guidance. Do not imply that it replaces physicians, laboratory testing, ECG,
imaging, specialist consultation, emergency care or prescribed treatment.

Your answers are universal educational guidance, not patient-specific advice.
Never claim to have read the user's prescription or medical record. Do not
diagnose, prescribe, recommend starting a treatment, or tell someone to stop,
increase, reduce or replace a prescribed medicine or procedure. Do not promise
a cure or treatment timeline. When an individualized clinical decision is
required, clearly ask the user to contact the clinical team. For urgent or
potentially life-threatening symptoms, advise immediate emergency care.
If the approved guidance does not contain the answer to an organizational or
product question, say that the information is not verified and refer the user
to the official team instead of answering from general model knowledge.

Return only the final WhatsApp-ready message. Never return JSON, XML, code
fences, metadata or internal reasoning. Keep a normal answer to roughly 3–6
short sentences.

APPROVED KNOWLEDGE:
""" + (knowledge_context or "No matching Novadigm knowledge article was found.")

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            "temperature": 0.1,
            "max_tokens": int(settings.WHATSAPP_LLM_MAX_TOKENS),
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "Novadigm Patient Support",
        }

        timeout = httpx.Timeout(float(settings.WHATSAPP_LLM_TIMEOUT_SECONDS))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                OPENROUTER_CHAT_URL,
                headers=headers,
                json=payload,
            )

        if response.status_code != 200:
            logger.warning(
                "WhatsApp LLM failed model=%s status=%s body=%s",
                model,
                response.status_code,
                response.text[:300],
            )
            raise RuntimeError(f"WhatsApp model returned HTTP {response.status_code}")

        data = response.json()
        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content") or ""
        )
        cleaned = self.clean_response(content)
        if not cleaned:
            raise RuntimeError("WhatsApp model returned an empty response")
        return cleaned


whatsapp_llm_service = WhatsAppLLMService()
