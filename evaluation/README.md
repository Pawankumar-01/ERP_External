# Casesheet accuracy evaluation

This directory provides an offline, deterministic benchmark for the three-batch
casesheet workflow. It does not import the FastAPI application, connect to the
database, load Whisper, or call an LLM provider.

The included files are deliberately synthetic. They demonstrate the evaluation
contract; their perfect result is **not** evidence that the production pipeline
is accurate.

## What is measured

The report keeps three stages separate:

1. **ASR WER** compares the observed transcript with a clinician-corrected gold
   transcript.
2. **Clinical facts** use micro precision, recall and F1. A fact identity is
   `(section, concept, entity, attribute)`. Its value, qualifiers and source kind
   must match, and an explicit fact must quote evidence from the observed
   transcript.
3. **ERP mapping** evaluates exact assertions against the final payload,
   including Frappe child-table selectors such as
   `sgp_pulse_table[system=CVS].pitta`.

Fact failures are classified as missing, extra, wrong value, wrong section or
unsupported evidence. Critical errors are counted at every failed stage, so the
same underlying clinical problem can correctly appear as both an extraction
failure and an ERP mapping failure.

## Run the synthetic contract check

```bash
PYTHONDONTWRITEBYTECODE=1 python -m evaluation.casesheet_eval \
  --gold evaluation/fixtures/synthetic_cases.json \
  --predictions evaluation/fixtures/synthetic_predictions.json \
  --fail-under-precision 0.90 \
  --fail-under-recall 0.90 \
  --fail-under-f1 0.90 \
  --fail-under-mapping 0.90 \
  --max-critical-errors 0
```

Use `--json` to produce a machine-readable report. Exit status is `1` when an
optional quality gate fails and `2` when an input file is invalid.
`--max-asr-wer` can independently gate transcription quality; it should be set
from an organization-approved benchmark rather than inferred from the synthetic
example.

## Prediction contract

Each prediction contains:

```json
{
  "case_id": "stable_case_id",
  "observed_transcript": "the final ASR transcript",
  "facts": [
    {
      "section": "pulse_diagnosis",
      "concept": "system_dosha",
      "entity": "CVS",
      "attribute": "pitta",
      "value": "mild",
      "qualifiers": {},
      "source_kind": "explicit",
      "evidence": "CVS mild Pitta"
    }
  ],
  "erp_payload": {}
}
```

The production pipeline should export this artifact in shadow mode rather than
writing to ERP during evaluation. Gold facts must be approved independently;
never create gold labels by copying the model's own output.

## Building a real benchmark

- Keep evaluation audio and labels outside Git and under organization-approved
  access controls.
- Use only appropriately authorized and de-identified material.
- Retain doctor, speaking-style and difficulty metadata under non-identifying
  IDs so holdouts can be split by doctor rather than by random sentence.
- Include terse Pulse dictation, negation, current versus historical medicines,
  completed versus planned procedures, unknown items and overlapping terms.
- Record both the original ASR transcript and the corrected transcript.
- Split development and held-out test cases before prompt or model tuning.
- Report each clinical section separately; do not hide a weak section behind a
  high overall average.

The initial synthetic set covers all three batches but is intentionally small.
The next data milestone should be clinician-approved cases representing actual
organizational speaking patterns, followed by a locked held-out set.
