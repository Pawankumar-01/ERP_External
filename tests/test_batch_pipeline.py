import asyncio
import unittest

from app.casesheet.clinical_intelligence import (
    merge_section_data,
    normalize_pulse_diagnosis,
)
from app.casesheet.llm_service import LLMService
from fastapi import HTTPException

from app.casesheet.router import (
    _batch_finalize_blockers,
    _map_draft_to_encounter,
    process_full_consultation_audio,
    upload_audio,
)


RAW_PULSE_SAMPLE = """Overall PPK dominance was severe Pitta, CVS mild to moderate P,
GIT mild to moderate K, RT mild to moderate B, LB, moderate to severe Pitta,
severe Vata, LIV, moderate to severe Pitta, severe Vata, IS, moderate to severe
Kapha, mild Pitta, PAN, moderate to severe Kapha, mild Pitta, KUB. mild to
moderate pitta moderate kafa GB mild to moderate pitta moderate kafa SS mild PB
moderate K LI SI mild PB moderate K ISCS mild to moderate K mild V OBG mild to
moderate K mild V PR O mild to moderate B mild K RB mild to moderate B"""


class StubBatchLLM(LLMService):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def _preprocess_and_segment_batch_transcript(self, batch_index, transcript):
        return {
            "vitals_anthropometry": "BP 120 over 80.",
            "pulse_diagnosis": "CVS mild P, LI mild K.",
            "general_examination": "",
            "systemic_examination": "",
            "investigation_reports": "",
            "ayurvedic_assessment_extended": "",
        }

    async def extract_section(self, section, transcript):
        self.calls.append((section, transcript))
        return {"source_section": section, "source_transcript": transcript}


class InvalidPulseFallbackLLM(LLMService):
    """Simulates the exact incident: a valid segment, invalid LLM JSON."""

    async def _preprocess_and_segment_batch_transcript(self, batch_index, transcript):
        return {
            "vitals_anthropometry": "",
            "general_examination": "",
            "systemic_examination": "",
            "investigation_reports": "",
            "pulse_diagnosis": "CVS mild P, LI mild K.",
            "ayurvedic_assessment_extended": "",
        }

    async def _safe_json_call(self, *, fallback, **_kwargs):
        # extract_section must retain this error after the deterministic Pulse
        # parser rebuilt rows from the raw source segment.
        return {**fallback, "_error": "invalid_json"}


class RepairingPulseLLM(LLMService):
    """First Pulse candidate conflicts; focused repair resolves it."""

    def __init__(self):
        super().__init__()
        self.responses = [
            {
                "overall_vpk": {},
                "systems": [
                    {
                        "system": "SS",
                        "vata": None,
                        "pitta": "mild_moderate",
                        "kapha": "mild_moderate",
                        "raw_phrase": "SS mild to moderate pitta and kapha",
                    },
                    {
                        "system": "SS",
                        "vata": "mild",
                        "pitta": "mild",
                        "kapha": None,
                        "raw_phrase": "SS mild pitta and vata",
                    },
                ],
            },
            {
                "overall_vpk": {},
                "systems": [
                    {
                        "system": "SS",
                        "vata": "mild",
                        "pitta": "mild_moderate",
                        "kapha": "mild_moderate",
                        "raw_phrase": "SS mild to moderate pitta and kapha, mild vata",
                    },
                ],
                "needs_doctor_confirmation": [],
            },
        ]

    async def _safe_json_call(self, **_kwargs):
        return self.responses.pop(0)


class BatchPipelineTests(unittest.TestCase):
    def test_pulse_sample_recovers_all_terse_rows_without_false_is(self):
        parsed = normalize_pulse_diagnosis({}, RAW_PULSE_SAMPLE)
        rows = {row["system"]: row for row in parsed["systems"]}

        self.assertEqual(parsed["overall_vpk"]["dominance"], "P")
        self.assertEqual(rows["RT"]["pitta"], "mild_moderate")  # B -> P only in Pulse
        self.assertEqual(rows["LISI"]["pitta"], "mild")
        self.assertEqual(rows["LISI"]["kapha"], "moderate")
        self.assertEqual(rows["LSCS"]["vata"], "mild")
        self.assertEqual(rows["LSCS"]["kapha"], "mild_moderate")
        self.assertEqual(rows["PRO"]["pitta"], "mild_moderate")
        self.assertEqual(rows["RB"]["pitta"], "mild_moderate")

        no_false_is = normalize_pulse_diagnosis(
            {}, "Overall PPK dominance is severe Pitta, CVS mild P."
        )
        self.assertNotIn("IS", {row["system"] for row in no_false_is["systems"]})

    def test_separate_li_si_and_combined_lisi_remain_distinct(self):
        parsed = normalize_pulse_diagnosis(
            {}, "LI mild P, SI moderate K, LISI severe V."
        )
        rows = {row["system"]: row for row in parsed["systems"]}
        self.assertEqual(set(rows), {"LI", "SI", "LISI"})
        self.assertEqual(rows["LI"]["pitta"], "mild")
        self.assertEqual(rows["SI"]["kapha"], "moderate")
        self.assertEqual(rows["LISI"]["vata"], "severe")

    def test_llm_pulse_values_survive_noisy_asr_reconciliation(self):
        # "TIN" is an unrecognised STT system label. Its later values must
        # not be attached to IS and overwrite Gemini's correct IS mapping.
        parsed = normalize_pulse_diagnosis(
            {
                "systems": [
                    {
                        "system": "IS",
                        "vata": "severe",
                        "pitta": "mild_moderate",
                        "kapha": None,
                    }
                ]
            },
            "IS severe vata mild to moderate pitta TIN moderate vata mild kafa.",
        )
        self.assertEqual([row["system"] for row in parsed["systems"]], ["IS"])
        self.assertEqual(parsed["systems"][0]["vata"], "severe")
        self.assertEqual(parsed["systems"][0]["pitta"], "mild_moderate")
        self.assertIsNone(parsed["systems"][0]["kapha"])

    def test_raw_pulse_parser_fills_missing_values_but_never_overwrites_llm(self):
        transcript = "CVS mild to moderate B."
        preserved = normalize_pulse_diagnosis(
            {"systems": [{"system": "CVS", "pitta": "mild"}]}, transcript
        )
        filled = normalize_pulse_diagnosis(
            {"systems": [{"system": "CVS", "pitta": None}]}, transcript
        )
        self.assertEqual(preserved["systems"][0]["pitta"], "mild")
        self.assertEqual(filled["systems"][0]["pitta"], "mild_moderate")

    def test_pulse_conflict_is_repaired_by_llm_before_reconciliation(self):
        result = asyncio.run(
            RepairingPulseLLM().extract_section(
                "pulse_diagnosis",
                "SS mild to moderate pitta and kapha, mild vata.",
            )
        )
        self.assertNotIn("_quality_issues", result)
        self.assertEqual(len(result["systems"]), 1)
        self.assertEqual(result["systems"][0]["system"], "SS")
        self.assertEqual(result["systems"][0]["pitta"], "mild_moderate")

    def test_pulse_list_merge_has_one_row_per_system(self):
        merged = merge_section_data(
            {"systems": [{"system": "CVS", "pitta": "mild"}]},
            {"systems": [{"system": "CVS", "vata": "severe"}]},
        )
        self.assertEqual(len(merged["systems"]), 1)
        self.assertEqual(merged["systems"][0]["pitta"], "mild")
        self.assertEqual(merged["systems"][0]["vata"], "severe")

    def test_batch_extraction_uses_only_its_segmented_evidence(self):
        service = StubBatchLLM()
        raw = "BP 120 over 80. CVS mild P, LI mild K."
        result = asyncio.run(service.extract_batch_transcript(2, raw))

        self.assertEqual(
            service.calls,
            [
                ("vitals_anthropometry", "BP 120 over 80."),
                ("pulse_diagnosis", "CVS mild P, LI mild K."),
            ],
        )
        self.assertEqual(result["_raw_section_transcripts"]["pulse_diagnosis"], "CVS mild P, LI mild K.")
        self.assertEqual(result["_section_statuses"]["general_examination"]["status"], "not_dictated")

    def test_segment_must_be_a_contiguous_source_excerpt(self):
        raw = "CVS mild P, RB moderate VK."
        self.assertTrue(LLMService._is_grounded_segment("CVS mild P", raw))
        self.assertFalse(LLMService._is_grounded_segment("RB moderate VK CVS mild P", raw))

    def test_legacy_recording_routes_are_retired(self):
        for endpoint in (upload_audio, process_full_consultation_audio):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(endpoint("session-id"))
            self.assertEqual(raised.exception.status_code, 410)

    def test_invalid_llm_json_is_not_silently_salvaged(self):
        service = LLMService()
        self.assertIsNone(service._parse_json('prefix {"pitta": "mild"} trailing'))
        self.assertEqual(service._parse_json('```json\n{"pitta": "mild"}\n```'), {"pitta": "mild"})

    def test_invalid_pulse_json_saves_source_rows_and_requires_review(self):
        result = asyncio.run(
            InvalidPulseFallbackLLM().extract_batch_transcript(2, "CVS mild P, LI mild K.")
        )
        self.assertEqual(result["_section_statuses"]["pulse_diagnosis"]["status"], "mapped_needs_review")
        rows = {row["system"]: row for row in result["pulse_diagnosis"]["systems"]}
        self.assertEqual(rows["CVS"]["pitta"], "mild")
        self.assertEqual(rows["LI"]["kapha"], "mild")
        blockers = _batch_finalize_blockers(
            {"batch_2": {"status": "completed", "section_statuses": result["_section_statuses"]}},
            {},
        )
        self.assertTrue(any("pulse_diagnosis" in item for item in blockers))

    def test_erp_payload_preserves_batch_three_and_pulse_values(self):
        draft = {
            "pulse_diagnosis": {
                "overall_vpk": {"dominance": "VP"},
                "systems": [
                    {"system": "LI", "pitta": "mild"},
                    {"system": "SI", "kapha": "moderate"},
                    {"system": "LISI", "vata": "severe"},
                ],
            },
            "_raw_transcripts": {"pulse_diagnosis": "LI mild P, SI moderate K, LISI severe V."},
            "ayurvedic_supplements": [{"name": "APD", "weeks": []}],
            "panchakarma": {"sessions": [{"procedure": "Virechana", "status": "prescribed"}]},
            "detox_procedures": {"detox_items": [{"name": "Fennel Water", "frequency": "daily"}]},
            "assessment_and_plan": {
                "plan": {"diet_plan_weeks": [{"week_range": "1-6", "diet_type": "PPD"}]}
            },
        }
        payload = _map_draft_to_encounter("P-1", "D-1", None, draft)

        self.assertEqual([row["system"] for row in payload["sgp_pulse_table"]], ["LI", "SI", "LISI"])
        self.assertEqual(payload["sgp_diet_weeks"][0]["diet_type"], "PPD")
        self.assertIn("Fennel Water", payload["detox_procedures"])
        self.assertIn("Virechana", payload["panchakarma"])
        self.assertEqual(payload["sgp_supplements_table"][0]["quantity_mg"], "")
        self.assertEqual(payload["sgp_supplements_table"][0]["frequency"], "")

    def test_erp_export_preserves_doctor_reviewed_pulse_correction(self):
        draft = {
            "pulse_diagnosis": {
                "systems": [{"system": "CVS", "pitta": "severe"}],
            },
            "_raw_transcripts": {"pulse_diagnosis": "CVS mild P."},
            "_section_meta": {"pulse_diagnosis": {"source": "manual"}},
        }
        payload = _map_draft_to_encounter("P-1", "D-1", None, draft)
        self.assertEqual(payload["sgp_pulse_table"][0]["pitta"], "severe")

    def test_finalize_blocks_active_or_unreviewed_batch(self):
        progress = {
            "batch_2": {
                "status": "completed",
                "section_statuses": {"pulse_diagnosis": {"status": "needs_review"}},
            },
            "batch_3": {"status": "extracting"},
        }
        blockers = _batch_finalize_blockers(progress, {})
        self.assertTrue(any("pulse_diagnosis" in item for item in blockers))
        self.assertTrue(any("batch_3" in item for item in blockers))


if __name__ == "__main__":
    unittest.main()
