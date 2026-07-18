from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import Any

from .audiobook_store import normalize_for_similarity
from .text_normalization import normalization_rule_settings


TAIL_REVIEW_METRICS_VERSION = 1


def analyze_audio_tail(
    source_text: str,
    word_timestamps: list[object],
    audio_duration_seconds: float,
    *,
    safety_margin_seconds: float = 0.40,
    warning_threshold_seconds: float = 0.50,
    failure_threshold_seconds: float = 1.00,
) -> dict[str, object]:
    """Measure audio left after the last Whisper word aligned to the source.

    Thresholds apply to the unexplained tail *after* the safety margin.  Exact
    source/Whisper token matches are deliberately used as anchors so a trailing
    hallucination such as "uh" is not mistaken for the final expected word.
    """
    duration = max(0.0, _finite_float(audio_duration_seconds))
    safety = max(0.0, _finite_float(safety_margin_seconds))
    warning = max(0.01, _finite_float(warning_threshold_seconds, 0.50))
    failure = max(warning + 0.01, _finite_float(failure_threshold_seconds, 1.00))
    source_tokens = normalize_for_similarity(source_text).split()
    recognized = _recognized_tokens(word_timestamps, duration)
    recognized_tokens = [token[0] for token in recognized]
    last_recognized_index = _last_aligned_recognized_index(
        source_tokens,
        recognized_tokens,
    )

    base: dict[str, object] = {
        "enabled": True,
        "metrics_version": TAIL_REVIEW_METRICS_VERSION,
        "audio_duration_seconds": round(duration, 3),
        "safety_margin_seconds": round(safety, 3),
        "warning_threshold_seconds": round(warning, 3),
        "failure_threshold_seconds": round(failure, 3),
    }
    if last_recognized_index is None:
        return {
            **base,
            "status": "unavailable",
            "risk_percent": 100.0,
            "last_valid_word": "",
            "last_valid_word_end_seconds": None,
            "raw_tail_seconds": None,
            "excess_tail_seconds": None,
        }

    last_word, last_end = recognized[last_recognized_index]
    last_end = min(duration, max(0.0, last_end))
    raw_tail = round(max(0.0, duration - last_end), 3)
    excess_tail = round(max(0.0, raw_tail - safety), 3)
    if excess_tail <= warning:
        status = "safe"
    elif excess_tail <= failure:
        status = "review"
    else:
        status = "retry_needed"

    return {
        **base,
        "status": status,
        "risk_percent": round(
            tail_risk_percent(excess_tail, warning, failure),
            1,
        ),
        "last_valid_word": last_word,
        "last_valid_word_end_seconds": round(last_end, 3),
        "raw_tail_seconds": round(raw_tail, 3),
        "excess_tail_seconds": round(excess_tail, 3),
    }


def tail_risk_percent(
    excess_tail_seconds: float,
    warning_threshold_seconds: float,
    failure_threshold_seconds: float,
) -> float:
    """Return a transparent severity heuristic, not a statistical probability."""
    excess = max(0.0, _finite_float(excess_tail_seconds))
    warning = max(0.01, _finite_float(warning_threshold_seconds, 0.50))
    failure = max(warning + 0.01, _finite_float(failure_threshold_seconds, 1.00))
    if excess <= warning:
        return 30.0 * (excess / warning)
    if excess <= failure:
        return 30.0 + 40.0 * ((excess - warning) / (failure - warning))
    # Reach 100% after one additional failure-threshold interval.
    return min(100.0, 70.0 + 30.0 * ((excess - failure) / failure))


def combined_review_status(transcript_status: str, tail_status: str) -> str:
    transcript = str(transcript_status or "not_verified")
    tail = str(tail_status or "disabled")
    if transcript == "retry_needed" or tail == "retry_needed":
        return "retry_needed"
    if transcript != "approved" or tail in {"review", "unavailable"}:
        return "review"
    return "approved"


def candidate_selection_score(
    similarity_score: float,
    tail_analysis: dict[str, object] | None,
) -> float:
    score = _finite_float(similarity_score)
    if not tail_analysis or not bool(tail_analysis.get("enabled", False)):
        return score
    risk = max(0.0, min(100.0, _finite_float(tail_analysis.get("risk_percent"))))
    return score - (risk * 0.35)


def candidate_is_better(
    candidate: dict[str, object],
    current: dict[str, object],
) -> bool:
    status_rank = {"retry_needed": 0, "review": 1, "approved": 2}
    candidate_key = (
        status_rank.get(str(candidate.get("status", "retry_needed")), 0),
        _finite_float(candidate.get("selection_score")),
        _finite_float(candidate.get("score")),
    )
    current_key = (
        status_rank.get(str(current.get("status", "retry_needed")), 0),
        _finite_float(current.get("selection_score")),
        _finite_float(current.get("score")),
    )
    return candidate_key > current_key


def parse_review_metrics(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def tail_analysis_is_current(
    review_metrics: object,
    *,
    enabled: bool,
    safety_margin_seconds: float,
    warning_threshold_seconds: float,
    failure_threshold_seconds: float,
) -> bool:
    if not enabled:
        return True
    metrics = parse_review_metrics(review_metrics)
    tail = metrics.get("tail_analysis")
    if not isinstance(tail, dict) or not bool(tail.get("enabled", False)):
        return False
    if str(tail.get("status", "")) not in {
        "safe",
        "review",
        "retry_needed",
        "unavailable",
    }:
        return False
    expected = (
        safety_margin_seconds,
        warning_threshold_seconds,
        failure_threshold_seconds,
    )
    actual = (
        tail.get("safety_margin_seconds"),
        tail.get("warning_threshold_seconds"),
        tail.get("failure_threshold_seconds"),
    )
    return all(
        abs(_finite_float(current, -1000.0) - _finite_float(wanted)) < 0.001
        for current, wanted in zip(actual, expected)
    )


def comparison_normalization_is_current(
    review_metrics: object,
    *,
    enabled: bool,
    language: str = "",
    rules: object = None,
) -> bool:
    metrics = parse_review_metrics(review_metrics)
    value = metrics.get("comparison_normalization")
    if not enabled:
        return not isinstance(value, dict) or not bool(value.get("enabled", False))
    if not isinstance(value, dict) or not bool(value.get("enabled", False)):
        return False
    if str(value.get("language", "")) != str(language):
        return False
    if rules is not None:
        stored_rules = value.get("rules")
        if not isinstance(stored_rules, dict):
            return False
        return normalization_rule_settings(stored_rules) == normalization_rule_settings(
            rules
        )
    return True


def _recognized_tokens(
    word_timestamps: list[object],
    audio_duration_seconds: float,
) -> list[tuple[str, float]]:
    tokens: list[tuple[str, float]] = []
    for item in word_timestamps:
        if not isinstance(item, dict):
            continue
        normalized = normalize_for_similarity(str(item.get("word", "")))
        if not normalized:
            continue
        end = _finite_float(item.get("end"), -1.0)
        if end < 0.0:
            continue
        end = min(audio_duration_seconds, end) if audio_duration_seconds else end
        for token in normalized.split():
            tokens.append((token, end))
    return tokens


def _last_aligned_recognized_index(
    source_tokens: list[str],
    recognized_tokens: list[str],
) -> int | None:
    if not source_tokens or not recognized_tokens:
        return None
    matcher = SequenceMatcher(
        None,
        source_tokens,
        recognized_tokens,
        autojunk=False,
    )
    last_index: int | None = None
    for _source_start, recognized_start, size in matcher.get_matching_blocks():
        if size:
            last_index = recognized_start + size - 1
    return last_index


def _finite_float(value: object, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if result != result or result in {float("inf"), float("-inf")}:
        return fallback
    return result
