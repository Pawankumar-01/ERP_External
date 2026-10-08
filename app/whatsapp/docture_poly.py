"""Device-only knowledge context and offline answers for Docture-Poly."""

from __future__ import annotations

import re

from app.whatsapp.knowledge import ARTICLES, KnowledgeMatch, format_knowledge_context


DEVICE_ARTICLES = tuple(a for a in ARTICLES if a.category == "docture_poly")
_ARTICLES_BY_ID = {a.article_id: a for a in DEVICE_ARTICLES}

_PRODUCT_NAME = re.compile(
    r"\b(?:docture|doctor)[\s-]*poly\b|\bvpk\s*42\b", re.IGNORECASE
)
_QUESTION_TYPOS = {
    "turst": "trust",
    "helpfull": "helpful",
    "daigonce": "diagnosis",
    "expailn": "explain",
}

# These cues are used only when the answer provider is unavailable. Normal
# device questions are interpreted by the LLM with the complete device corpus.
_FALLBACK_TOPICS = (
    ("docture_poly_accuracy", {"trust", "accurate", "accuracy", "reliable", "reliability", "evidence", "proof", "proven", "research", "validation", "validated"}),
    ("docture_poly_limits", {"diagnosis", "diagnose", "diagnoses", "diagnostic", "detect", "disease", "cure", "cures", "prevent", "replace", "replacement"}),
    ("docture_poly_availability", {"price", "pricing", "cost", "costs", "buy", "purchase", "demo", "available", "availability", "duration", "minutes", "long"}),
    ("docture_poly_regulatory_privacy", {"approval", "approved", "fda", "ce", "certified", "certification", "regulatory", "privacy", "stored", "storage", "policy"}),
    ("docture_poly_comfort", {"pain", "painful", "hurt", "hurts", "safe", "safety", "invasive", "needle", "precautions", "contraindications", "pregnancy", "children"}),
    ("docture_poly_monitoring", {"wearable", "ring", "continuous", "continuously", "monitor", "monitoring", "oxygen", "bp"}),
    ("docture_poly_omics", {"omics", "hba1c", "glucose", "biochemical", "chemistry"}),
    ("docture_poly_readiness", {"regeneration", "readiness", "repair", "recovery"}),
    ("docture_poly_vpk42", {"variability", "processing", "kinetics", "vpk", "42"}),
    ("docture_poly_recommendations", {"diet", "nutrition", "yoga", "breathing", "exercise", "supplement", "supplements", "prescribe"}),
    ("docture_poly_outputs", {"help", "helps", "helpful", "benefit", "benefits", "useful", "purpose", "advantage", "advantages", "report", "reports", "result", "results", "output", "outputs", "show", "provide"}),
    ("docture_poly_scan", {"work", "works", "working", "scan", "scanning", "ppg", "sensor", "signals"}),
)


def device_knowledge_context() -> str:
    """Include every device article so word overlap cannot exclude an answer."""
    return format_knowledge_context(KnowledgeMatch(a, 0) for a in DEVICE_ARTICLES)


def device_fallback_article_ids(query: str) -> list[str]:
    # Product names establish the topic, not the answer to a particular query.
    normalized = _PRODUCT_NAME.sub(" ", query.lower())
    words = re.findall(r"[a-z0-9]+", normalized)
    words = [_QUESTION_TYPOS.get(word, word) for word in words]
    terms = set(words)
    selected = [article_id for article_id, cues in _FALLBACK_TOPICS if terms & cues]

    introduction = bool(re.search(
        r"\b(?:tell me about|explain(?: about)?|describe|introduce)\b",
        " ".join(words),
    ))
    subject_words = terms - {
        "what", "is", "a", "an", "the", "device", "machine", "this", "it",
        "do", "you", "mean", "tell", "me", "about", "explain", "describe",
        "introduce", "please",
    }
    # Include an introduction when explicitly requested along with another
    # supported topic, or when the question is just asking what the device is.
    # "What is the battery capacity?" must not receive an unrelated overview.
    if re.search(r"\bvpk\s*42\b", query, re.IGNORECASE) and not selected:
        selected = ["docture_poly_vpk42"]
    elif introduction and (not subject_words or selected):
        selected.insert(0, "docture_poly_overview")
    elif not subject_words:
        selected = ["docture_poly_overview"]

    # Retain every requested topic, including overview + price in one message.
    return list(dict.fromkeys(selected))


def device_fallback_answer(query: str) -> str:
    article_ids = device_fallback_article_ids(query)
    if not article_ids:
        return (
            "I don't have verified information about that Docture-Poly detail. "
            "Please contact the product team on 7331109988 or at info@sgprs.com "
            "for confirmation, or visit docture-poly.com."
        )

    paragraphs = [_ARTICLES_BY_ID[article_id].answer for article_id in article_ids]
    # A WhatsApp text message is limited in length. Preserve whole paragraphs
    # and explain any omissions instead of sending a cut-off medical statement.
    answer = ""
    for paragraph in paragraphs:
        candidate = "\n\n".join(part for part in (answer, paragraph) if part)
        if len(candidate) > 3200:
            answer += (
                "\n\nYour question covers additional topics. Please ask those "
                "separately or contact the product team on 7331109988."
            )
            break
        answer = candidate
    return answer


def device_answer_is_generic_overview(query: str, answer: str) -> bool:
    """Reject the old stock paragraph when it omits a requested device topic."""
    if device_fallback_article_ids(query) == ["docture_poly_overview"]:
        return False
    normalized_answer = re.sub(r"[^a-z0-9]+", "", answer.lower())
    normalized_overview = re.sub(
        r"[^a-z0-9]+", "", _ARTICLES_BY_ID["docture_poly_overview"].answer.lower()
    )
    return normalized_answer == normalized_overview
