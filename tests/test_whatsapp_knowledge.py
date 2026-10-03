import os


os.environ.setdefault("WHATSAPP_TOKEN", "test-token")
os.environ.setdefault("WHATSAPP_PHONE_ID", "123456789")

from app.whatsapp.knowledge import (
    format_knowledge_context,
    is_emergency_query,
    search_knowledge,
)
from app.whatsapp.llm_service import WhatsAppLLMService


def test_oil_question_retrieves_universal_oil_guidance():
    matches = search_knowledge("Why do you use oil applications in treatment?")

    assert matches
    assert matches[0].article.article_id == "oil_purpose"
    assert "supportive" in matches[0].article.answer.lower()
    assert "particular oil" in matches[0].article.answer.lower()


def test_missed_dose_question_retrieves_safe_answer():
    matches = search_knowledge("I forgot one dose. Should I double the next dose?")

    assert matches
    assert matches[0].article.article_id == "missed_dose"
    assert "do not automatically double" in matches[0].article.answer.lower()


def test_clear_emergency_language_is_detected():
    assert is_emergency_query("I have chest pain and cannot breathe") is True


def test_negated_emergency_language_does_not_trigger():
    assert is_emergency_query("I do not have chest pain") is False


def test_plain_text_response_is_preserved():
    assert WhatsAppLLMService.clean_response("Use the oil only as instructed.") == (
        "Use the oil only as instructed."
    )


def test_json_response_wrapper_is_removed():
    raw = '{"response":"Use the oil only as instructed."}'

    assert WhatsAppLLMService.clean_response(raw) == "Use the oil only as instructed."


def test_fenced_json_response_wrapper_is_removed():
    raw = '```json\n{"answer":"General guidance only."}\n```'

    assert WhatsAppLLMService.clean_response(raw) == "General guidance only."


def test_docture_poly_typo_retrieves_device_overview():
    matches = search_knowledge("What is doctor poly and what does this device do?")

    assert matches
    assert matches[0].article.article_id == "docture_poly_overview"
    assert "non-invasive" in matches[0].article.answer.lower()
    assert "not an independent diagnostic" in matches[0].article.answer.lower()


def test_docture_poly_diagnosis_question_retrieves_explicit_limit():
    matches = search_knowledge("Can Docture Poly diagnose disease and replace my blood test?")

    assert matches
    assert matches[0].article.article_id == "docture_poly_limits"
    assert "does not independently diagnose" in matches[0].article.answer.lower()
    assert "laboratory testing" in matches[0].article.answer.lower()


def test_docture_poly_omics_is_not_presented_as_a_lab_result():
    matches = search_knowledge("Does the OMICS module give an exact HbA1c blood value?")

    assert matches
    assert matches[0].article.article_id == "docture_poly_omics"
    assert "not exact" in matches[0].article.answer.lower()
    assert "standard laboratory testing" in matches[0].article.answer.lower()


def test_docture_poly_price_routes_to_verified_contact():
    matches = search_knowledge("What is the device price and where can I book a demo?")

    assert matches
    assert matches[0].article.article_id == "docture_poly_availability"
    assert "does not contain a verified current price" in matches[0].article.answer.lower()
    assert "7331109988" in matches[0].article.answer


def test_device_context_contains_official_source_provenance():
    matches = search_knowledge("How does the VPK42 scan work?")
    context = format_knowledge_context(matches)

    assert "Official sources:" in context
    assert "https://docture-poly.com/" in context


def test_generic_cure_question_does_not_select_device_limits():
    matches = search_knowledge("Does this treatment cure cancer?")

    assert matches
    assert matches[0].article.article_id != "docture_poly_limits"


def test_qualified_device_safety_question_selects_device_guidance():
    matches = search_knowledge("Is the Docture Poly device safe and non-invasive?")

    assert matches
    assert matches[0].article.article_id == "docture_poly_comfort"


def test_device_topic_anchors_short_diagnosis_follow_up():
    matches = search_knowledge(
        "Can it diagnose disease?",
        category="docture_poly",
    )

    assert matches
    assert matches[0].article.article_id == "docture_poly_limits"


def test_device_topic_anchors_short_price_follow_up():
    matches = search_knowledge(
        "How much does it cost?",
        category="docture_poly",
    )

    assert matches
    assert matches[0].article.article_id == "docture_poly_availability"
