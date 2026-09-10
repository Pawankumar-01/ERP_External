from __future__ import annotations

"""Permissive Pydantic schemas for native Gemini structured output.

These models are intentionally LOOSE: every clinical field is Optional and
defaults to None/empty. The Gemini SDK suppresses ValidationError and returns
``parsed=None`` silently instead of raising, so putting strict validation here
would convert good-but-imperfect generations into total failures.

Semantic validation (required fields, ranges, canonical vocab) stays in
``clinical_intelligence.sanitize_section`` / ``normalize_pulse_diagnosis`` as a
separate Python step AFTER parsing — see llm_service._validate_semantics.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class OverallVpk(BaseModel):
    dominance: Optional[str] = None
    prakriti: Optional[str] = None
    vikriti: Optional[str] = None
    notes: Optional[str] = None


class PulseSystemRow(BaseModel):
    system: Optional[str] = None
    vata: Optional[str] = None
    pitta: Optional[str] = None
    kapha: Optional[str] = None
    raw_phrase: Optional[str] = None
    needs_doctor_confirmation: List[str] = Field(default_factory=list)


class PulseDiagnosisOutput(BaseModel):
    """Standalone pulse call — never folded into the batch schema."""

    overall_vpk: OverallVpk = Field(default_factory=OverallVpk)
    systems: List[PulseSystemRow] = Field(default_factory=list)
    needs_doctor_confirmation: List[str] = Field(default_factory=list)


class BatchedSectionsOutput(BaseModel):
    """One Gemini call for the 5 light Batch-2 sections.

    Each section payload is a free-form dict (same shape as the legacy
    per-section JSON). Strict shape checks happen post-parse in
    sanitize_section(), not here.
    """

    vitals_anthropometry: Dict[str, Any] = Field(default_factory=dict)
    general_examination: Dict[str, Any] = Field(default_factory=dict)
    systemic_examination: Dict[str, Any] = Field(default_factory=dict)
    investigation_reports: Dict[str, Any] = Field(default_factory=dict)
    ayurvedic_assessment_extended: Dict[str, Any] = Field(default_factory=dict)


BATCHED_SECTIONS_KEYS = (
    "vitals_anthropometry",
    "general_examination",
    "systemic_examination",
    "investigation_reports",
    "ayurvedic_assessment_extended",
)
