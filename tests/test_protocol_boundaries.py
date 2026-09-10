import unittest

from app.casesheet.clinical_intelligence import (
    CAT_DETOX,
    CAT_PROCEDURES,
    apply_protocol_boundaries,
    classify_protocol_item,
    enrich_protocol_item,
    normalize_pulse_diagnosis,
)


class ProtocolBoundaryTests(unittest.TestCase):
    def test_virechana_is_in_clinic_and_nvk_is_home_detox(self):
        self.assertEqual(classify_protocol_item("Virechana")[0], CAT_PROCEDURES)
        self.assertEqual(classify_protocol_item("NVK")[0], CAT_DETOX)

    def test_greeva_asr_alias_is_canonicalized(self):
        item = enrich_protocol_item({"procedure": "Griever Vasthi"})
        self.assertEqual(item["procedure"], "Greeva Vasti")
        self.assertEqual(item["category"], CAT_PROCEDURES)

    def test_boundary_move_and_duplicate_virechana_collapse(self):
        draft = {
            "panchakarma": {
                "sessions": [
                    {"procedure": "Abhyanga", "session_count": 5},
                    {"procedure": "Virechana", "status": "prescribed"},
                ]
            },
            "detox_procedures": {
                "detox_items": [
                    {"item_name": "Virechana", "remarks": "not done"},
                    {"item_name": "Virechana", "remarks": "not done"},
                    {"item_name": "Fennel Water"},
                ]
            },
            "exercises_yoga": {"exercises": []},
        }

        apply_protocol_boundaries(draft)

        sessions = draft["panchakarma"]["sessions"]
        self.assertEqual([row["name"] for row in sessions], ["Abhyanga", "Virechana"])
        self.assertEqual(sessions[0]["session_count"], 5)
        self.assertEqual(draft["detox_procedures"]["detox_items"][0]["name"], "Fennel Water")

    def test_pulse_parser_corrects_b_and_keeps_li_si_lisi_distinct(self):
        pulse = normalize_pulse_diagnosis(
            {"systems": [{"system": "CVS", "pitta": "mild"}, {"system": "CVS", "vata": "severe"}]},
            "Overall PPK dominance was severe Pitta. CVS mild to moderate B, "
            "LI mild P, SI moderate K, LISI severe V.",
        )
        rows = {row["system"]: row for row in pulse["systems"]}
        self.assertEqual(pulse["overall_vpk"]["dominance"], "P")
        self.assertEqual(rows["CVS"]["pitta"], "mild_moderate")
        self.assertEqual(rows["LI"]["pitta"], "mild")
        self.assertEqual(rows["SI"]["kapha"], "moderate")
        self.assertEqual(rows["LISI"]["vata"], "severe")
        self.assertEqual(len([row for row in pulse["systems"] if row["system"] == "CVS"]), 1)


if __name__ == "__main__":
    unittest.main()
