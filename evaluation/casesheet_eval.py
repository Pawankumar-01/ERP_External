"""Deterministic, provider-free evaluation for the casesheet pipeline.

The evaluator keeps the three major error sources separate:

* ASR: word error rate against a clinician-corrected transcript.
* Extraction: evidence-backed fact precision, recall and F1.
* Mapping: assertions against the final ERP payload.

Gold and prediction files are JSON so outputs from the current pipeline and a
future fact-first prototype can be compared with the same scorer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


class DatasetError(ValueError):
    """Raised when an evaluation file cannot be scored safely."""


_WORD_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?", re.IGNORECASE)
_PATH_PART_RE = re.compile(r"^([^\[\]]+)(?:\[([^\]]+)\])?$")
_MISSING = object()
_FACT_FIELDS = ("section", "concept", "entity", "attribute")


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _evidence_text(value: Any) -> str:
    return " ".join(_WORD_RE.findall(str(value or "").casefold()))


def _normal_value(value: Any) -> Any:
    """Normalize representation without changing clinical meaning."""
    if isinstance(value, str):
        return re.sub(r"[\s_-]+", " ", value.strip().casefold())
    if isinstance(value, list):
        return [_normal_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _normal_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    return value


def fact_key(fact: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(_text(fact.get(field)) for field in _FACT_FIELDS)  # type: ignore[return-value]


def semantic_key(fact: dict[str, Any]) -> tuple[str, str, str]:
    key = fact_key(fact)
    return key[1], key[2], key[3]


def fact_payload(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "value": _normal_value(fact.get("value")),
        "qualifiers": _normal_value(fact.get("qualifiers") or {}),
        "source_kind": _text(fact.get("source_kind") or "explicit"),
    }


def evidence_is_grounded(fact: dict[str, Any], transcript: str) -> bool:
    if _text(fact.get("source_kind") or "explicit") == "derived":
        return True
    evidence = _evidence_text(fact.get("evidence"))
    source = _evidence_text(transcript)
    return bool(evidence) and evidence in source


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DatasetError(message)


def validate_gold_dataset(dataset: dict[str, Any]) -> None:
    _require(isinstance(dataset, dict), "Gold dataset must be a JSON object")
    _require(isinstance(dataset.get("cases"), list), "Gold dataset requires a cases array")
    _require(bool(dataset["cases"]), "Gold dataset cannot be empty")

    seen_cases: set[str] = set()
    for position, case in enumerate(dataset["cases"], start=1):
        prefix = f"Gold case {position}"
        _require(isinstance(case, dict), f"{prefix} must be an object")
        case_id = str(case.get("case_id") or "").strip()
        _require(bool(case_id), f"{prefix} has no case_id")
        _require(case_id not in seen_cases, f"Duplicate gold case_id: {case_id}")
        seen_cases.add(case_id)
        _require(case.get("batch_index") in (1, 2, 3), f"{case_id}: batch_index must be 1, 2 or 3")
        _require(
            case.get("data_classification") in ("synthetic", "de_identified"),
            f"{case_id}: data_classification must be synthetic or de_identified",
        )
        transcript = case.get("gold_transcript")
        _require(isinstance(transcript, str) and bool(transcript.strip()), f"{case_id}: missing gold_transcript")
        facts = case.get("expected_facts")
        _require(isinstance(facts, list), f"{case_id}: expected_facts must be an array")

        seen_facts: set[tuple[str, str, str, str]] = set()
        for fact_pos, fact in enumerate(facts, start=1):
            fp = f"{case_id}: expected fact {fact_pos}"
            _require(isinstance(fact, dict), f"{fp} must be an object")
            _require(all(str(fact.get(field) or "").strip() for field in ("section", "concept", "attribute")),
                     f"{fp} requires section, concept and attribute")
            _require("value" in fact, f"{fp} requires an explicit value (null is allowed)")
            key = fact_key(fact)
            _require(key not in seen_facts, f"{case_id}: duplicate expected fact key {key}")
            seen_facts.add(key)
            if _text(fact.get("source_kind") or "explicit") != "derived":
                _require(evidence_is_grounded(fact, transcript), f"{fp} evidence is not in gold_transcript")

        assertions = case.get("expected_erp_assertions", [])
        _require(isinstance(assertions, list), f"{case_id}: expected_erp_assertions must be an array")
        for assert_pos, assertion in enumerate(assertions, start=1):
            ap = f"{case_id}: ERP assertion {assert_pos}"
            _require(isinstance(assertion, dict), f"{ap} must be an object")
            _require(isinstance(assertion.get("path"), str) and bool(assertion["path"].strip()),
                     f"{ap} requires path")
            _require(
                assertion.get("operator", "equals")
                in {"equals", "contains", "not_contains", "present", "absent"},
                f"{ap} has unsupported operator",
            )
            if assertion.get("operator", "equals") in {"equals", "contains", "not_contains"}:
                _require("value" in assertion, f"{ap} requires value")


def validate_predictions(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _require(isinstance(payload, dict), "Predictions must be a JSON object")
    rows = payload.get("predictions")
    _require(isinstance(rows, list), "Predictions file requires a predictions array")
    indexed: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(rows, start=1):
        _require(isinstance(row, dict), f"Prediction {position} must be an object")
        case_id = str(row.get("case_id") or "").strip()
        _require(bool(case_id), f"Prediction {position} has no case_id")
        _require(case_id not in indexed, f"Duplicate prediction case_id: {case_id}")
        _require(isinstance(row.get("observed_transcript", ""), str),
                 f"{case_id}: observed_transcript must be a string")
        _require(isinstance(row.get("facts", []), list), f"{case_id}: facts must be an array")
        _require(isinstance(row.get("erp_payload", {}), dict), f"{case_id}: erp_payload must be an object")
        for fact_pos, fact in enumerate(row.get("facts", []), start=1):
            _require(isinstance(fact, dict), f"{case_id}: predicted fact {fact_pos} must be an object")
            _require(
                all(str(fact.get(field) or "").strip() for field in ("section", "concept", "attribute")),
                f"{case_id}: predicted fact {fact_pos} requires section, concept and attribute",
            )
            _require("value" in fact, f"{case_id}: predicted fact {fact_pos} requires value")
        indexed[case_id] = row
    return indexed


def word_error_counts(reference: str, hypothesis: str) -> dict[str, int | float]:
    """Return Levenshtein ASR counts and WER without third-party packages."""
    ref = _WORD_RE.findall(reference.casefold())
    hyp = _WORD_RE.findall(hypothesis.casefold())
    # Each cell is (total edits, substitutions, deletions, insertions).
    previous = [(index, 0, 0, index) for index in range(len(hyp) + 1)]
    for ref_index, ref_word in enumerate(ref, start=1):
        current = [(ref_index, 0, ref_index, 0)]
        for hyp_index, hyp_word in enumerate(hyp, start=1):
            if ref_word == hyp_word:
                current.append(previous[hyp_index - 1])
                continue
            substitution = previous[hyp_index - 1]
            deletion = previous[hyp_index]
            insertion = current[hyp_index - 1]
            options = [
                (substitution[0] + 1, substitution[1] + 1, substitution[2], substitution[3]),
                (deletion[0] + 1, deletion[1], deletion[2] + 1, deletion[3]),
                (insertion[0] + 1, insertion[1], insertion[2], insertion[3] + 1),
            ]
            current.append(min(options, key=lambda value: (value[0], value[1], value[2], value[3])))
        previous = current
    edits, substitutions, deletions, insertions = previous[-1]
    return {
        "reference_words": len(ref),
        "hypothesis_words": len(hyp),
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "errors": edits,
        "wer": edits / len(ref) if ref else (0.0 if not hyp else 1.0),
    }


def _metric(tp: int, fp: int, fn: int) -> dict[str, int | float]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def evaluate_facts(
    expected: list[dict[str, Any]],
    predicted: list[dict[str, Any]],
    observed_transcript: str,
) -> dict[str, Any]:
    unused = set(range(len(predicted)))
    unresolved: list[dict[str, Any]] = []
    section_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    details: list[dict[str, Any]] = []
    tp = fp = fn = critical_errors = 0

    def add_section(section: str, tp_add: int = 0, fp_add: int = 0, fn_add: int = 0) -> None:
        counts = section_counts[section or "unknown"]
        counts[0] += tp_add
        counts[1] += fp_add
        counts[2] += fn_add

    # First match exact fact identity, value, qualifiers and grounded evidence.
    for gold in expected:
        match = next(
            (
                index for index in unused
                if fact_key(predicted[index]) == fact_key(gold)
                and fact_payload(predicted[index]) == fact_payload(gold)
                and evidence_is_grounded(predicted[index], observed_transcript)
            ),
            None,
        )
        if match is None:
            unresolved.append(gold)
            continue
        unused.remove(match)
        tp += 1
        add_section(_text(gold.get("section")), tp_add=1)

    # Distinguish a correct fact placed into the wrong section from omission.
    still_unresolved: list[dict[str, Any]] = []
    for gold in unresolved:
        match = next(
            (
                index for index in unused
                if semantic_key(predicted[index]) == semantic_key(gold)
                and fact_payload(predicted[index]) == fact_payload(gold)
                and evidence_is_grounded(predicted[index], observed_transcript)
            ),
            None,
        )
        if match is None:
            still_unresolved.append(gold)
            continue
        unused.remove(match)
        fp += 1
        fn += 1
        if gold.get("critical") is True:
            critical_errors += 1
        add_section(_text(gold.get("section")), fn_add=1)
        add_section(_text(predicted[match].get("section")), fp_add=1)
        details.append({
            "type": "wrong_section",
            "fact": list(fact_key(gold)),
            "predicted_section": predicted[match].get("section"),
            "critical": gold.get("critical") is True,
        })

    # Remaining same-key predictions are wrong values or unsupported evidence.
    for gold in still_unresolved:
        candidates = [index for index in unused if fact_key(predicted[index]) == fact_key(gold)]
        if candidates:
            match = candidates[0]
            unused.remove(match)
            issue_type = (
                "unsupported_evidence"
                if fact_payload(predicted[match]) == fact_payload(gold)
                else "wrong_value"
            )
            details.append({
                "type": issue_type,
                "fact": list(fact_key(gold)),
                "expected": fact_payload(gold),
                "predicted": fact_payload(predicted[match]),
                "critical": gold.get("critical") is True,
            })
            fp += 1
            fn += 1
            add_section(_text(gold.get("section")), fp_add=1, fn_add=1)
        else:
            details.append({
                "type": "missing",
                "fact": list(fact_key(gold)),
                "critical": gold.get("critical") is True,
            })
            fn += 1
            add_section(_text(gold.get("section")), fn_add=1)
        if gold.get("critical") is True:
            critical_errors += 1

    # Everything left is an unsupported or duplicate assertion.
    for index in sorted(unused):
        fact = predicted[index]
        details.append({
            "type": "extra",
            "fact": list(fact_key(fact)),
            "value": fact_payload(fact),
            "evidence_grounded": evidence_is_grounded(fact, observed_transcript),
        })
        fp += 1
        add_section(_text(fact.get("section")), fp_add=1)

    result = _metric(tp, fp, fn)
    result.update({
        "critical_errors": critical_errors,
        "wrong_section": sum(item["type"] == "wrong_section" for item in details),
        "wrong_value": sum(item["type"] == "wrong_value" for item in details),
        "unsupported_evidence": sum(item["type"] == "unsupported_evidence" for item in details),
        "missing": sum(item["type"] == "missing" for item in details),
        "extra": sum(item["type"] == "extra" for item in details),
        "sections": {
            section: _metric(*counts)
            for section, counts in sorted(section_counts.items())
        },
        "details": details,
    })
    return result


def resolve_path(payload: Any, path: str) -> Any:
    """Resolve dotted JSON paths with optional list selectors.

    Examples: ``bp`` and ``sgp_pulse_table[system=CVS].pitta``.
    """
    current = payload
    for raw_part in path.split("."):
        match = _PATH_PART_RE.match(raw_part)
        if not match or not isinstance(current, dict):
            return _MISSING
        name, selector = match.groups()
        if name not in current:
            return _MISSING
        current = current[name]
        if selector is None:
            continue
        if not isinstance(current, list):
            return _MISSING
        if selector.isdigit():
            index = int(selector)
            if index >= len(current):
                return _MISSING
            current = current[index]
            continue
        if "=" not in selector:
            return _MISSING
        field, expected = selector.split("=", 1)
        current = next(
            (
                item for item in current
                if isinstance(item, dict) and _text(item.get(field)) == _text(expected)
            ),
            _MISSING,
        )
        if current is _MISSING:
            return _MISSING
    return current


def _assertion_passes(actual: Any, assertion: dict[str, Any]) -> bool:
    operator = assertion.get("operator", "equals")
    expected = assertion.get("value")
    if operator == "present":
        return actual is not _MISSING and actual is not None and actual != ""
    if operator == "absent":
        return actual is _MISSING or actual is None or actual == ""
    if actual is _MISSING:
        return False
    if operator == "equals":
        return _normal_value(actual) == _normal_value(expected)
    if operator in {"contains", "not_contains"}:
        if isinstance(actual, list):
            found = _normal_value(expected) in _normal_value(actual)
        else:
            found = _text(expected) in _text(actual)
        return found if operator == "contains" else not found
    return False


def evaluate_mapping(assertions: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    critical_errors = 0
    passed = 0
    for assertion in assertions:
        actual = resolve_path(payload, assertion["path"])
        if _assertion_passes(actual, assertion):
            passed += 1
            continue
        critical = assertion.get("critical") is True
        critical_errors += int(critical)
        details.append({
            "path": assertion["path"],
            "operator": assertion.get("operator", "equals"),
            "expected": assertion.get("value"),
            "actual": "<missing>" if actual is _MISSING else actual,
            "critical": critical,
        })
    total = len(assertions)
    return {
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "accuracy": passed / total if total else 1.0,
        "critical_errors": critical_errors,
        "details": details,
    }


def evaluate_dataset(gold: dict[str, Any], predictions: dict[str, Any]) -> dict[str, Any]:
    validate_gold_dataset(gold)
    predicted_by_id = validate_predictions(predictions)
    cases: list[dict[str, Any]] = []
    totals = {"tp": 0, "fp": 0, "fn": 0, "critical": 0, "map_pass": 0, "map_total": 0,
              "ref_words": 0, "asr_errors": 0}

    for gold_case in gold["cases"]:
        case_id = gold_case["case_id"]
        predicted = predicted_by_id.get(case_id, {"case_id": case_id, "facts": [], "erp_payload": {}})
        observed = str(predicted.get("observed_transcript") or "")
        asr = word_error_counts(gold_case["gold_transcript"], observed)
        facts = evaluate_facts(gold_case["expected_facts"], predicted.get("facts", []), observed)
        mapping = evaluate_mapping(gold_case.get("expected_erp_assertions", []), predicted.get("erp_payload", {}))
        cases.append({"case_id": case_id, "batch_index": gold_case["batch_index"], "asr": asr,
                      "facts": facts, "mapping": mapping})
        totals["tp"] += int(facts["tp"])
        totals["fp"] += int(facts["fp"])
        totals["fn"] += int(facts["fn"])
        totals["critical"] += int(facts["critical_errors"]) + int(mapping["critical_errors"])
        totals["map_pass"] += mapping["passed"]
        totals["map_total"] += mapping["total"]
        totals["ref_words"] += int(asr["reference_words"])
        totals["asr_errors"] += int(asr["errors"])

    facts_total = _metric(totals["tp"], totals["fp"], totals["fn"])
    return {
        "dataset_version": gold.get("dataset_version"),
        "case_count": len(cases),
        "overall": {
            "asr_wer": totals["asr_errors"] / totals["ref_words"] if totals["ref_words"] else 0.0,
            "asr_errors": totals["asr_errors"],
            "reference_words": totals["ref_words"],
            "facts": facts_total,
            "mapping_accuracy": totals["map_pass"] / totals["map_total"] if totals["map_total"] else 1.0,
            "mapping_passed": totals["map_pass"],
            "mapping_total": totals["map_total"],
            "critical_errors": totals["critical"],
        },
        "cases": cases,
        "unmatched_prediction_cases": sorted(set(predicted_by_id) - {case["case_id"] for case in gold["cases"]}),
    }


def _load(path: str) -> dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"Cannot read {path}: {exc}") from exc
    _require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


def _print_summary(report: dict[str, Any]) -> None:
    overall = report["overall"]
    facts = overall["facts"]
    print(f"Cases: {report['case_count']}")
    print(f"ASR WER: {overall['asr_wer']:.3f} ({overall['asr_errors']}/{overall['reference_words']})")
    print(
        "Facts: "
        f"precision={facts['precision']:.3f} recall={facts['recall']:.3f} f1={facts['f1']:.3f} "
        f"(TP={facts['tp']} FP={facts['fp']} FN={facts['fn']})"
    )
    print(
        f"ERP mapping: {overall['mapping_accuracy']:.3f} "
        f"({overall['mapping_passed']}/{overall['mapping_total']})"
    )
    print(f"Critical errors: {overall['critical_errors']}")
    for case in report["cases"]:
        print(
            f"  {case['case_id']}: WER={case['asr']['wer']:.3f} "
            f"fact-F1={case['facts']['f1']:.3f} mapping={case['mapping']['accuracy']:.3f}"
        )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate casesheet ASR, facts and ERP mapping offline")
    parser.add_argument("--gold", required=True, help="Gold dataset JSON")
    parser.add_argument("--predictions", required=True, help="Prediction JSON")
    parser.add_argument("--json", action="store_true", help="Print the complete JSON report")
    parser.add_argument("--max-asr-wer", type=float)
    parser.add_argument("--fail-under-precision", type=float)
    parser.add_argument("--fail-under-recall", type=float)
    parser.add_argument("--fail-under-f1", type=float)
    parser.add_argument("--fail-under-mapping", type=float)
    parser.add_argument("--max-critical-errors", type=int)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = evaluate_dataset(_load(args.gold), _load(args.predictions))
    except DatasetError as exc:
        print(f"Evaluation input error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_summary(report)

    overall = report["overall"]
    failed = (
        (args.max_asr_wer is not None and overall["asr_wer"] > args.max_asr_wer)
        or (args.fail_under_precision is not None and overall["facts"]["precision"] < args.fail_under_precision)
        or (args.fail_under_recall is not None and overall["facts"]["recall"] < args.fail_under_recall)
        or (args.fail_under_f1 is not None and overall["facts"]["f1"] < args.fail_under_f1)
        or (args.fail_under_mapping is not None and overall["mapping_accuracy"] < args.fail_under_mapping)
        or (args.max_critical_errors is not None and overall["critical_errors"] > args.max_critical_errors)
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
