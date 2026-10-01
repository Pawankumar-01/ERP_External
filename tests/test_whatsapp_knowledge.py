import os


os.environ.setdefault("WHATSAPP_TOKEN", "test-token")
os.environ.setdefault("WHATSAPP_PHONE_ID", "123456789")

from app.whatsapp.knowledge import is_emergency_query, search_knowledge
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

