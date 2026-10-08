"""Offline regression checks for real Docture-Poly enquiries and LLM routing."""

import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault("WHATSAPP_TOKEN", "test-token")
os.environ.setdefault("WHATSAPP_PHONE_ID", "123456789")

from app.config.settings import settings
from app.whatsapp.bot_engine import WhatsAppBotEngine
from app.whatsapp.docture_poly import (
    DEVICE_ARTICLES,
    device_fallback_answer,
    device_fallback_article_ids,
    device_knowledge_context,
)
from app.whatsapp.knowledge import ARTICLES, EMERGENCY_MESSAGE
from app.whatsapp.llm_service import WhatsAppLLMService, whatsapp_llm_service
from app.whatsapp.service import whatsapp_service
from app.whatsapp.state_store import UserSession, state_store


SCREENSHOT_QUERIES = (
    "how docture-poly helpfull us",
    "how can i turst docture-poly device",
    "what daigonce docture-poly provide",
    "expailn about docture-poly device and price details",
)


def text_payload(query):
    return {"entry": [{"changes": [{"value": {"messages": [{
        "from": "offline-test",
        "type": "text",
        "text": {"body": query},
    }]}}]}]}


class DeviceKnowledgeTests(unittest.TestCase):
    def test_fallbacks_address_the_screenshot_intents(self):
        answers = [device_fallback_answer(q) for q in SCREENSHOT_QUERIES]
        self.assertEqual(len(set(answers)), 4)
        self.assertIn("nutrition guidance", answers[0])
        self.assertIn("validation", answers[1])
        self.assertIn("does not independently diagnose", answers[2])
        self.assertIn("not confirmed medical diagnoses", answers[2])
        self.assertIn("physiological-signal platform", answers[3])
        self.assertIn("verified current price", answers[3])
        self.assertIn("7331109988", answers[3])

    def test_spelling_and_product_name_variants_preserve_the_intent(self):
        cases = (
            ("Can I trust Docture Poly?", "docture_poly_accuracy"),
            ("how can i turst docture-poly device", "docture_poly_accuracy"),
            ("What diagnosis does doctor poly provide?", "docture_poly_limits"),
            ("How helpful is VPK42?", "docture_poly_outputs"),
        )
        for query, article_id in cases:
            with self.subTest(query=query):
                self.assertIn(article_id, device_fallback_article_ids(query))

    def test_combined_explanation_and_price_preserves_both(self):
        article_ids = device_fallback_article_ids(SCREENSHOT_QUERIES[3])
        self.assertIn("docture_poly_overview", article_ids)
        self.assertIn("docture_poly_availability", article_ids)

    def test_short_specific_follow_up_does_not_get_an_introduction(self):
        self.assertEqual(
            device_fallback_article_ids("What is the price?"),
            ["docture_poly_availability"],
        )
        self.assertEqual(
            device_fallback_article_ids("What is VPK42?"),
            ["docture_poly_vpk42"],
        )

    def test_unknown_detail_is_not_replaced_with_the_overview(self):
        query = "What is the Docture-Poly battery capacity?"
        self.assertEqual(device_fallback_article_ids(query), [])
        answer = device_fallback_answer(query)
        self.assertIn("don't have verified information", answer)
        self.assertNotIn("physiological-signal platform", answer)

    def test_context_contains_all_device_sources_and_no_other_articles(self):
        context = device_knowledge_context()
        for article in DEVICE_ARTICLES:
            self.assertIn(f"[{article.article_id}]", context)
        self.assertIn("https://docture-poly.com/research/", context)
        self.assertNotIn("[medicine_timing]", context)
        self.assertNotIn("[oil_purpose]", context)


class DeviceBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = WhatsAppBotEngine()
        self.session = UserSession("offline-test")
        self.sender = AsyncMock(return_value=True)
        self.model = AsyncMock()
        self.patches = (
            patch.object(state_store, "get_session", return_value=self.session),
            patch.object(whatsapp_service, "send_text_message", self.sender),
            patch.object(whatsapp_llm_service, "generate_answer", self.model),
        )
        for context in self.patches:
            context.start()
            self.addCleanup(context.stop)

    async def test_screenshot_questions_reach_model_with_complete_knowledge(self):
        responses = (
            "It supports nutrition and wellness insights for physician review.",
            "Consider its validation evidence and professional interpretation.",
            "It does not independently diagnose disease.",
            "It supplies wellness insights. A verified current price is unavailable.",
        )
        self.model.side_effect = responses
        for query in SCREENSHOT_QUERIES:
            await self.engine.handle_webhook_payload(text_payload(query))
        self.assertEqual(self.model.await_count, 4)
        self.assertEqual(self.sender.await_count, 4)
        for call in self.model.await_args_list:
            self.assertEqual(call.kwargs["topic"], "docture_poly")
            for article in DEVICE_ARTICLES:
                self.assertIn(f"[{article.article_id}]", call.kwargs["knowledge_context"])
        for response, call in zip(responses, self.sender.await_args_list):
            self.assertIn(response, call.args[1])

    async def test_model_outage_sends_distinct_relevant_answers(self):
        self.model.side_effect = RuntimeError("offline provider outage")
        for query in SCREENSHOT_QUERIES:
            await self.engine.handle_webhook_payload(text_payload(query))
        replies = [call.args[1] for call in self.sender.await_args_list]
        self.assertEqual(len(set(replies)), 4)
        self.assertIn("validation", replies[1])
        self.assertIn("does not independently diagnose", replies[2])
        self.assertIn("verified current price", replies[3])
        self.assertEqual(self.model.await_count, 4)

    async def test_model_stock_overview_cannot_hide_price_or_trust_request(self):
        self.model.return_value = next(
            article.answer for article in DEVICE_ARTICLES
            if article.article_id == "docture_poly_overview"
        )
        for query in (SCREENSHOT_QUERIES[1], SCREENSHOT_QUERIES[3]):
            await self.engine.handle_webhook_payload(text_payload(query))
        replies = [call.args[1] for call in self.sender.await_args_list]
        self.assertIn("validation", replies[0])
        self.assertIn("verified current price", replies[1])
        self.assertEqual(self.model.await_count, 2)

    async def test_model_overview_is_allowed_for_actual_introduction_question(self):
        overview = next(
            article.answer for article in DEVICE_ARTICLES
            if article.article_id == "docture_poly_overview"
        )
        self.model.return_value = overview
        await self.engine.handle_webhook_payload(text_payload("What is Docture-Poly?"))
        self.assertIn(overview, self.sender.await_args.args[1])
        self.model.assert_awaited_once()

    async def test_device_mention_inside_general_ai_conversation_uses_device_path(self):
        self.session.update_state("ASKING_AI_QUESTION")
        self.model.return_value = "Its proprietary estimates require validation."
        await self.engine.handle_webhook_payload(text_payload(SCREENSHOT_QUERIES[1]))
        self.assertEqual(self.model.await_args.kwargs["topic"], "docture_poly")
        self.assertEqual(self.session.data["knowledge_topic"], "docture_poly")

    async def test_device_follow_up_receives_bounded_conversation_history(self):
        self.model.return_value = "Please ask the product team for this detail."
        queries = ("What is Docture-Poly?", "Is it painful?", "What about the price?", "Where is the data stored?")
        for query in queries:
            await self.engine.handle_webhook_payload(text_payload(query))
        second_history = self.model.await_args_list[1].kwargs["conversation_history"]
        self.assertEqual(second_history[0]["content"], queries[0])
        last_history = self.model.await_args.kwargs["conversation_history"]
        self.assertEqual(len(last_history), 4)
        self.assertEqual(last_history[-2]["content"], queries[2])
        self.assertEqual(len(self.session.data["docture_poly_history"]), 4)

    async def test_general_oil_guidance_keeps_existing_direct_answer(self):
        self.session.update_state("ASKING_AI_QUESTION")
        await self.engine.handle_webhook_payload(text_payload(
            "Why do you use oil applications in treatment?"
        ))
        self.model.assert_not_awaited()
        article = next(a for a in ARTICLES if a.article_id == "oil_purpose")
        self.assertIn(article.answer, self.sender.await_args.args[1])
        self.assertNotIn("docture_poly_history", self.session.data)

    async def test_emergency_message_keeps_existing_route(self):
        self.session.update_state("ASKING_AI_QUESTION", knowledge_topic="docture_poly")
        await self.engine.handle_webhook_payload(text_payload(
            "I have chest pain and cannot breathe. Can Docture-Poly help?"
        ))
        self.model.assert_not_awaited()
        self.assertEqual(self.sender.await_args.args[1], EMERGENCY_MESSAGE)

    async def test_unrelated_general_question_keeps_existing_model_arguments(self):
        self.session.update_state("ASKING_AI_QUESTION")
        self.model.return_value = "Please clarify the service you mean."
        await self.engine.handle_webhook_payload(text_payload("What are your membership options?"))
        self.assertEqual(set(self.model.await_args.kwargs), {"query", "knowledge_context"})


class DeviceModelRequestTests(unittest.IsolatedAsyncioTestCase):
    async def request_with_mock_provider(self, content, *, finish_reason="stop", topic="docture_poly", history=()):
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{
                "finish_reason": finish_reason,
                "message": {"content": content},
            }]})

        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(handle)

        def client_factory(**kwargs):
            return client_type(transport=transport, **kwargs)

        with patch.object(settings, "OPENROUTER_API_KEY", "offline-test-key"), patch(
            "app.whatsapp.llm_service.httpx.AsyncClient", side_effect=client_factory
        ):
            answer = await WhatsAppLLMService().generate_answer(
                query="How can I trust Docture-Poly?",
                knowledge_context=device_knowledge_context(),
                topic=topic,
                conversation_history=history,
            )
        return answer, requests

    async def test_actual_provider_payload_has_device_instructions_and_history(self):
        answer, requests = await self.request_with_mock_provider(
            "Its proprietary estimates require appropriate validation.",
            history=({"role": "user", "content": "What is Docture-Poly?"},),
        )
        self.assertEqual(len(requests), 1)
        messages = requests[0]["messages"]
        self.assertIn("Address EVERY requested", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "What is Docture-Poly?")
        self.assertEqual(messages[-1]["content"], "How can I trust Docture-Poly?")
        self.assertIn("validation", answer)

    async def test_truncated_device_answer_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            await self.request_with_mock_provider("The price is", finish_reason="length")

    async def test_partial_json_device_answer_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "invalid text"):
            await self.request_with_mock_provider('{"response": "The device')

    async def test_complete_json_wrapper_is_still_cleaned(self):
        answer, _ = await self.request_with_mock_provider(
            '{"response": "A verified current price is unavailable."}'
        )
        self.assertEqual(answer, "A verified current price is unavailable.")

    async def test_general_model_prompt_and_history_behavior_are_unchanged(self):
        _, requests = await self.request_with_mock_provider(
            "General guidance.", topic=None,
            history=({"role": "user", "content": "Earlier question"},),
        )
        messages = requests[0]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertNotIn("DOCTURE-POLY CONVERSATION:", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
