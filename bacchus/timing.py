"""Import source-bound word timings as transcript cues."""

import json
import math
import re

from .audio import MAX_CUES, MAX_TRANSCRIPT_LENGTH


MAX_CUE_LENGTH = 500
DURATION_TOLERANCE = 0.25
END_TOLERANCE = 0.05
SENTENCE_END = re.compile(r"""[.!?。！？]["'”’»)]*$""")
SHA256 = re.compile(r"[0-9a-fA-F]{64}")


class TimingError(ValueError):
    pass


def _finite_number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _word_spans(words, duration):
    spans = []
    previous_start = 0
    script_length = 0
    for index, word in enumerate(words, 1):
        if not isinstance(word, dict):
            raise TimingError(f"Word {index} must be an object.")
        text = word.get("text")
        if not isinstance(text, str) or not text.strip() or any(
            (ord(char) < 32 and not char.isspace()) or 0xD800 <= ord(char) <= 0xDFFF
            for char in text
        ):
            raise TimingError(f"Word {index} must contain usable text.")
        text = " ".join(text.split())
        start, end = word.get("start"), word.get("end")
        if not (
            _finite_number(start) and _finite_number(end)
            and previous_start <= start < duration
            and start < end <= duration + END_TOLERANCE
        ):
            raise TimingError(f"Word {index} has unordered times or extends past the recording.")
        previous_start = start
        end = min(end, duration)
        script_length += len(text) + int(index > 1)
        if script_length > MAX_TRANSCRIPT_LENGTH:
            raise TimingError("The word timings contain too much text for this project.")
        if spans and start < spans[-1][2]:
            start_text, start_time, end_time = spans.pop()
            text, start, end = f"{start_text} {text}", start_time, max(end, end_time)
        if len(text) > MAX_CUE_LENGTH:
            raise TimingError(f"Word {index} or its overlapping words are too long for one cue.")
        spans.append((text, start, end))
    return spans


def _group(spans, target, natural):
    lines = []
    text = ""
    start = end = 0
    for word, word_start, word_end in spans:
        can_break = natural and len(text) >= 75 and (
            SENTENCE_END.search(text) or word_start - end >= 0.55
        )
        if text and (can_break or len(text) + len(word) + 1 > target):
            lines.append({"id": len(lines) + 1, "text": text, "start": start, "end": end})
            text = ""
        if text:
            text += f" {word}"
        else:
            text, start = word, word_start
        end = word_end
    lines.append({"id": len(lines) + 1, "text": text, "start": start, "end": end})
    return lines


def import_cues(raw: bytes, source_hash: str, duration: float) -> dict:
    """Validate portable word evidence before forming ordered display cues."""
    try:
        data = json.loads(raw)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise TimingError("Timing evidence must be a readable JSON document.") from error
    if not isinstance(data, dict) or not isinstance(data.get("audio"), dict):
        raise TimingError("Timing evidence must contain audio.sha256, duration, and words.")
    bound_hash = data["audio"].get("sha256")
    if not isinstance(bound_hash, str) or not SHA256.fullmatch(bound_hash):
        raise TimingError("Timing evidence needs the recording's audio.sha256.")
    if bound_hash.lower() != source_hash.lower():
        raise TimingError("The word timings belong to a different recording. Check the audio file.")
    declared_duration = data.get("duration")
    if not (
        _finite_number(duration) and duration > 0
        and _finite_number(declared_duration) and declared_duration > 0
        and duration - DURATION_TOLERANCE <= declared_duration <= duration + DURATION_TOLERANCE
    ):
        raise TimingError("The duration in the word timings does not match the recording.")
    words = data.get("words")
    if not isinstance(words, list) or not words:
        raise TimingError("Timing evidence must contain a nonempty words array.")
    spans = _word_spans(words, duration)
    lines = _group(spans, 165, True)
    if len(lines) > MAX_CUES:
        lines = _group(spans, MAX_CUE_LENGTH, False)
    if len(lines) > MAX_CUES:
        raise TimingError(f"The word timings need more than {MAX_CUES} cues.")
    return {
        "script": "\n".join(line["text"] for line in lines),
        "timing": "source-cues",
        "lines": lines,
        "model": "imported word timings",
    }
