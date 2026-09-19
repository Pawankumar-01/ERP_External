import copy
import json
import unittest
from pathlib import Path

from evaluation.casesheet_eval import (
    DatasetError,
    evaluate_dataset,
    evaluate_facts,
    evaluate_mapping,
    resolve_path,
    validate_gold_dataset,
    word_error_counts,
)


ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "evaluation" / "fixtures" / "synthetic_cases.json"
PREDICTION_PATH = ROOT / "evaluation" / "fixtures" / "synthetic_predictions.json"


def load_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class CasesheetEvaluationTests(unittest.TestCase):
    def test_reference_fixture_separates_asr_from_correct_facts(self):
        report = evaluate_dataset(load_json(GOLD_PATH), load_json(PREDICTION_PATH))

        self.assertEqual(report["case_count"], 3)
        self.assertEqual(report["overall"]["asr_errors"], 1)
        self.assertGreater(report["overall"]["asr_wer"], 0)
        self.assertEqual(report["overall"]["facts"]["f1"], 1.0)
        self.assertEqual(report["overall"]["mapping_accuracy"], 1.0)
        self.assertEqual(report["overall"]["critical_errors"], 0)

    def test_word_error_rate_labels_substitution_insertion_and_deletion(self):
        substitution_and_insertion = word_error_counts("a b c", "a x c extra")
        self.assertEqual(substitution_and_insertion["substitutions"], 1)
        self.assertEqual(substitution_and_insertion["insertions"], 1)
        self.assertEqual(substitution_and_insertion["deletions"], 0)

        deletion = word_error_counts("a b c", "a c")
        self.assertEqual(deletion["deletions"], 1)
        self.assertEqual(deletion["insertions"], 0)

    def test_fact_errors_are_classified_by_failure_type(self):
        expected = {
            "section": "pulse_diagnosis",
            "concept": "system_dosha",
            "entity": "CVS",
            "attribute": "pitta",
            "value": "mild",
            "qualifiers": {},
            "source_kind": "explicit",
            "evidence": "CVS mild Pitta",
            "critical": True,
        }

        wrong_section = copy.deepcopy(expected)
        wrong_section["section"] = "systemic_examination"
        section_report = evaluate_facts([expected], [wrong_section], "CVS mild Pitta")
        self.assertEqual(section_report["wrong_section"], 1)
        self.assertEqual(section_report["critical_errors"], 1)

        wrong_value = copy.deepcopy(expected)
        wrong_value["value"] = "severe"
        value_report = evaluate_facts([expected], [wrong_value], "CVS mild Pitta")
        self.assertEqual(value_report["wrong_value"], 1)

        unsupported = copy.deepcopy(expected)
        unsupported["evidence"] = "CVS severe Pitta"
        evidence_report = evaluate_facts([expected], [unsupported], "CVS mild Pitta")
        self.assertEqual(evidence_report["unsupported_evidence"], 1)

        extra_report = evaluate_facts([], [expected], "CVS mild Pitta")
        self.assertEqual(extra_report["extra"], 1)
        self.assertEqual(extra_report["fp"], 1)

    def test_erp_paths_support_child_table_selectors(self):
        payload = {
            "sgp_pulse_table": [
                {"system": "CVS", "pitta": "mild"},
                {"system": "LI", "pitta": "moderate"},
            ]
        }
        self.assertEqual(resolve_path(payload, "sgp_pulse_table[system=CVS].pitta"), "mild")
        report = evaluate_mapping(
            [
                {
                    "path": "sgp_pulse_table[system=CVS].pitta",
                    "operator": "equals",
                    "value": "severe",
                    "critical": True,
                }
            ],
            payload,
        )
        self.assertEqual(report["accuracy"], 0.0)
        self.assertEqual(report["critical_errors"], 1)

    def test_gold_evidence_must_be_present_in_corrected_transcript(self):
        gold = load_json(GOLD_PATH)
        gold["cases"][0]["expected_facts"][0]["evidence"] = "unsupported phrase"
        with self.assertRaises(DatasetError):
            validate_gold_dataset(gold)

    def test_missing_prediction_is_not_silent_success(self):
        report = evaluate_dataset(load_json(GOLD_PATH), {"predictions": []})
        self.assertEqual(report["overall"]["facts"]["tp"], 0)
        self.assertEqual(report["overall"]["facts"]["fn"], 21)
        self.assertEqual(report["overall"]["mapping_accuracy"], 0.0)
        self.assertGreater(report["overall"]["critical_errors"], 0)


if __name__ == "__main__":
    unittest.main()
