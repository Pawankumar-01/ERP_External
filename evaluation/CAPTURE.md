# Evaluation capture mode

Evaluation capture preserves the evidence needed to measure the live three-batch
pipeline. It is disabled by default and does not alter prompts, extraction or
ERP mapping.

## Enable for a controlled test

Configure the backend process and restart it:

```env
CASE_EVAL_CAPTURE_ENABLED=true
CASE_EVAL_CAPTURE_ROOT=var/casesheet_evaluation
CASE_EVAL_CAPTURE_MAX_AUDIO_MB=100
```

The root must be on a private filesystem with enough capacity. It must not be
served by a web server or synchronized into Git. The application creates
directories with mode `0700` and files with mode `0600` where the operating
system supports POSIX permissions.

## Captured artifacts

Every recording attempt has its own `batch_N/<job_id>/` directory containing:

- `original.wav` and `audio_metadata.json` with a SHA-256 digest;
- `asr_transcript.json`;
- `segmented_transcripts.json`;
- `llm_trace.json`, including exact captured provider responses and parsed
  candidates for that job;
- `extracted_sections.json`;
- `timings.json`;
- `committed_draft.json`, or `failure.json` when processing fails.

At session level, `correction_history.jsonl` stores before/requested/resulting
values for draft edits and before/after transcripts for transcript correction.
`revisions/` contains immutable post-edit draft snapshots. `finalization/`
contains the exact reviewed draft, ERP request and ERP response for every
finalization attempt.

The normal server log does not need to contain complete transcripts.

## Export one session

Run this on the backend server:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m evaluation.export_capture \
  --session-id SESSION_UUID \
  --capture-root var/casesheet_evaluation \
  --output /tmp/SESSION_UUID-evaluation.zip
```

The exporter refuses to overwrite an existing archive and adds a manifest with
the size and SHA-256 digest of every source file. Export does not delete the
captured session.

These bundles contain clinical data. Transfer, retention and deletion must be
handled through an organization-approved process. No download API is exposed by
this implementation.
