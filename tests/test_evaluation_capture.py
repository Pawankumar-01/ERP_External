import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

import app.casesheet.evaluation_capture as capture_module
from app.casesheet.evaluation_capture import (
    EvaluationCapture,
    begin_llm_trace,
    end_llm_trace,
)
from app.casesheet.llm_service import LLMService
from evaluation.export_capture import export_session


class EvaluationCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_capture_does_not_create_a_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "capture"
            capture = EvaluationCapture(False, root)
            written = await capture.capture_batch_audio("session-1", 1, "job-1", b"RIFFdata")
            self.assertFalse(written)
            self.assertFalse(root.exists())

    async def test_capture_preserves_audio_artifacts_corrections_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "capture"
            capture = EvaluationCapture(True, root)
            audio = b"RIFF" + b"synthetic-wave-data"

            self.assertTrue(
                await capture.capture_batch_audio(
                    "session-1",
                    2,
                    "job-1",
                    audio,
                    {"batch_revision": 1, "mode": "monologue"},
                )
            )
            self.assertTrue(
                await capture.write_job_artifact(
                    "session-1", 2, "job-1", "asr_transcript.json", {"transcript": "CVS mild P"}
                )
            )
            self.assertTrue(
                await capture.append_correction(
                    "session-1",
                    {
                        "kind": "draft_field_edit",
                        "field_path": "pulse_diagnosis.systems.0.pitta",
                        "previous_value": "moderate",
                        "resulting_value": "mild",
                    },
                )
            )

            job_dir = root / "session-1" / "batch_2" / "job-1"
            self.assertEqual((job_dir / "original.wav").read_bytes(), audio)
            metadata = json.loads((job_dir / "audio_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["size_bytes"], len(audio))
            self.assertEqual(len(metadata["sha256"]), 64)
            transcript = json.loads((job_dir / "asr_transcript.json").read_text(encoding="utf-8"))
            self.assertEqual(transcript["transcript"], "CVS mild P")

            correction_lines = (
                root / "session-1" / "correction_history.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(correction_lines), 1)
            self.assertEqual(json.loads(correction_lines[0])["resulting_value"], "mild")
            self.assertEqual(stat.S_IMODE((job_dir / "original.wav").stat().st_mode), 0o600)

    async def test_audio_limit_records_skip_without_writing_wave(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "capture"
            capture = EvaluationCapture(True, root, max_audio_mb=1)
            await capture.capture_batch_audio("session-1", 1, "job-large", b"x" * (1024 * 1024 + 1))

            self.assertFalse((root / "session-1" / "batch_1" / "job-large" / "original.wav").exists())
            events = (root / "session-1" / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("audio_capture_skipped", events)

    async def test_export_contains_all_artifacts_and_checksum_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "capture"
            capture = EvaluationCapture(True, root)
            await capture.capture_batch_audio("session-1", 3, "job-1", b"RIFFsynthetic")
            await capture.write_finalization_artifact(
                "session-1", "attempt-1", "erp_request_payload.json", {"patient": "TEST"}
            )

            output = Path(temp) / "session-1.zip"
            exported = export_session(root, "session-1", output)
            self.assertEqual(exported, output.resolve())
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn("session-1/batch_3/job-1/original.wav", names)
                self.assertIn("session-1/finalization/attempt-1/erp_request_payload.json", names)
                manifest = json.loads(archive.read("session-1/bundle_manifest.json"))
            self.assertTrue(manifest["contains_clinical_data"])
            self.assertTrue(any(row["path"] == "batch_3/job-1/original.wav" for row in manifest["files"]))

    def test_llm_trace_is_buffered_only_inside_enabled_capture_context(self):
        with tempfile.TemporaryDirectory() as temp:
            replacement = EvaluationCapture(True, Path(temp) / "capture")
            original = capture_module.evaluation_capture
            capture_module.evaluation_capture = replacement
            try:
                token = begin_llm_trace("session-1", 2, "job-1")
                LLMService._log_llm_output(
                    "section:pulse_diagnosis",
                    "provider_response",
                    '{"systems": [{"system": "CVS", "pitta": "mild"}]}',
                )
                entries = end_llm_trace(token)
            finally:
                capture_module.evaluation_capture = original

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["stage"], "provider_response")
            self.assertIn("CVS", entries[0]["payload"])


if __name__ == "__main__":
    unittest.main()
