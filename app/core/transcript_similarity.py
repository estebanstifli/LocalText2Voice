from __future__ import annotations

from app.core.audiobook_store import normalize_for_similarity


def similarity_metrics(source_text: str, transcript_text: str) -> dict[str, float]:
    source = normalize_for_similarity(source_text)
    transcript = normalize_for_similarity(transcript_text)
    source_words = source.split()
    transcript_words = transcript.split()
    wer = _error_rate(source_words, transcript_words)
    cer = _error_rate(list(source), list(transcript))
    word_similarity = max(0.0, 1.0 - wer)
    char_similarity = max(0.0, 1.0 - cer)
    if len(source_words) <= 4:
        score = (word_similarity * 0.35) + (char_similarity * 0.65)
    else:
        score = (word_similarity * 0.65) + (char_similarity * 0.35)
    return {
        "similarity_score": round(max(0.0, min(100.0, score * 100)), 2),
        "wer": round(wer, 4),
        "cer": round(cer, 4),
    }


def verification_status(score: float, approve_threshold: float = 92.0) -> str:
    if score >= approve_threshold:
        return "approved"
    if score >= max(0.0, approve_threshold - 7.0):
        return "review"
    return "retry_needed"


def _error_rate(source: list[str], candidate: list[str]) -> float:
    if not source and not candidate:
        return 0.0
    if not source:
        return 1.0
    distance = _levenshtein(source, candidate)
    return distance / max(1, len(source))


def _levenshtein(left: list[str], right: list[str]) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_item in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_item in enumerate(right, start=1):
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            substitution = previous[right_index - 1] + (
                0 if left_item == right_item else 1
            )
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]
