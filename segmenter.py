"""Sentence grouping for word-level English transcription timestamps."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class Segment:
    id: str
    startMs: int
    endMs: int
    text: str
    confidence: float
    needsReview: bool


def words_to_segments(words: list[dict[str, Any]], duration_ms: int) -> list[Segment]:
    """Use punctuation, pauses, and 2–15 second bounds to form practice clips."""
    result: list[Segment] = []
    bucket: list[dict[str, Any]] = []

    def emit() -> None:
        if not bucket:
            return
        start = max(0, round(float(bucket[0]["start"]) * 1000))
        end = min(duration_ms, round(float(bucket[-1]["end"]) * 1000))
        text = re.sub(r"\s+([,.!?;:])", r"\1", " ".join(w["word"].strip() for w in bucket)).strip()
        probabilities = [float(w.get("probability", 0.65)) for w in bucket]
        confidence = round(sum(probabilities) / len(probabilities), 2)
        seconds = (end - start) / 1000
        result.append(Segment(str(uuid.uuid4()), start, max(end, start + 1), text, confidence,
                              confidence < 0.72 or not re.search(r"[.!?]$", text) or seconds < 2 or seconds > 15))
        bucket.clear()

    for word in words:
        if not word.get("word"):
            continue
        if bucket:
            pause = float(word["start"]) - float(bucket[-1]["end"])
            projected_seconds = float(word["end"]) - float(bucket[0]["start"])
            bucket_seconds = float(bucket[-1]["end"]) - float(bucket[0]["start"])
            previous_ends_sentence = bool(re.search(r"[.!?]$", bucket[-1]["word"].strip()))
            if projected_seconds > 15 or (pause >= 0.7 and bucket_seconds >= 2) or (previous_ends_sentence and bucket_seconds >= 2):
                emit()
        bucket.append(word)
        if re.search(r"[.!?]$", word["word"].strip()) and float(bucket[-1]["end"]) - float(bucket[0]["start"]) >= 2:
            emit()
    emit()

    merged: list[Segment] = []
    for segment in result:
        if merged and segment.endMs - segment.startMs < 2000 and segment.endMs - merged[-1].startMs <= 15000:
            prior = merged[-1]
            prior.endMs = segment.endMs
            prior.text = f"{prior.text} {segment.text}".strip()
            prior.confidence = round((prior.confidence + segment.confidence) / 2, 2)
            prior.needsReview = prior.needsReview or segment.needsReview
        else:
            merged.append(segment)
    return merged
