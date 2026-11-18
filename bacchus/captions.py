"""Build readable SRT captions from accepted English word timings."""

from dataclasses import dataclass
import math
import re


METHOD = "timed-words-v1"
LINE_WIDTH = 42
MIN_DWELL_MS = 1_000
TARGET_DWELL_MS = 6_000
MAX_DWELL_MS = 7_000
MAX_TAIL_MS = 500
TARGET_CPS = 17
FLAG_CPS = 20

_SENTENCE = re.compile(r"""[.!?]["'”’»\])]*$""")
_CLAUSE = re.compile(r"""[,;:–—]["'”’»\])]*$""")
_INITIAL = re.compile(r"[A-Z]\.")
_NUMBER = re.compile(r"[+−-]?\d[\d,.]*")
_TITLES = {"mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "jr.", "sr.", "rev.", "capt.", "mt."}
_CONNECTORS = set(
    "a an the and or but because although if than that whether of to for from in on "
    "with by at per into under over between my your his her its our their "
    "he she they it we you i will would can could should has have had".split()
)
_UNITS = set(
    "kg g mg lb lbs oz km m cm mm mi ft in yd h hr hrs s sec ms min "
    "kilograms grams pounds ounces kilometers meters centimetres centimeters millimeters "
    "miles feet inches hours minutes seconds milliseconds percent percentage "
    "degrees hz khz mhz ghz volts watts dollars cents euros".split()
)


class CaptionError(ValueError):
    """The source is outside the timing format accepted by intake."""


@dataclass(frozen=True)
class _WordGroup:
    start: int
    end: int
    text: str
    precision_limited: bool


@dataclass(frozen=True)
class _Cue:
    start: int
    end: int
    lines: tuple[str, ...]
    messages: tuple[str, ...]


def _finite(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _millisecond_groups(words, duration, duration_ms):
    groups = []
    previous_start = -1
    for word in words:
        if not isinstance(word, dict) or not isinstance(word.get("text"), str):
            raise CaptionError("Each caption word needs approved text and timings.")
        text = " ".join(word["text"].split())
        start, end = word.get("start"), word.get("end")
        if (not text or not _finite(start) or not _finite(end)
                or not 0 <= start < end <= duration or start < previous_start):
            raise CaptionError("Caption words must follow the recording in order.")
        previous_start = start
        start_ms, end_ms = round(start * 1000), round(end * 1000)
        fused = end_ms <= start_ms or start_ms >= duration_ms
        start_ms = min(start_ms, duration_ms - 1)
        end_ms = min(duration_ms, max(start_ms + 1, end_ms))
        if groups and groups[-1].start == start_ms:
            current = groups[-1]
            groups[-1] = _WordGroup(start_ms, max(current.end, end_ms), f"{current.text} {text}", True)
        else:
            groups.append(_WordGroup(start_ms, end_ms, text, fused))
    return groups


def _break_cost(left, right):
    """Score a textual boundary, preferring punctuation and whole phrases."""
    last, first = left.strip("\"'“‘(["), right.strip("\"'“‘([")
    last_word = last.rstrip(".,;:!?)]\"'”’").casefold()
    first_word = first.rstrip(".,;:!?)]\"'”’").casefold()
    if last.casefold() in _TITLES or _INITIAL.fullmatch(last):
        return 22
    if _SENTENCE.search(left):
        return 0
    if _CLAUSE.search(left):
        return 3
    if (last_word in _CONNECTORS or (_NUMBER.fullmatch(last) and first_word in _UNITS)
            or (last[:1].isupper() and first[:1].isupper())):
        return 22
    return 8


def _wrap(text):
    if len(text) <= LINE_WIDTH or " " not in text:
        return (text,)
    tokens = text.split(" ")
    splits = 0
    choices = []
    for left, right in zip(tokens, tokens[1:]):
        splits += len(left) + int(splits > 0)
        other = len(text) - splits - 1
        overflow = max(0, splits - LINE_WIDTH) + max(0, other - LINE_WIDTH)
        choices.append(((overflow, _break_cost(left, right), abs(splits - other)), splits))
    _, boundary = min(choices)
    return text[:boundary], text[boundary + 1:]


def _make_cue(start, spoken_end, next_start, text, lines, precision_limited):
    hard_end = min(next_start, start + MAX_DWELL_MS)
    target = min(TARGET_DWELL_MS, max(MIN_DWELL_MS, (len(text) * 1000 + TARGET_CPS - 1) // TARGET_CPS))
    tail_end = min(hard_end, spoken_end + MAX_TAIL_MS, start + target)
    end = max(min(spoken_end, hard_end), tail_end)
    dwell = end - start
    cps = len(text) * 1000 / dwell
    messages = []
    if any(len(line) > LINE_WIDTH for line in lines):
        messages.append("A line exceeds 42 characters because the timed text could not be split further.")
    if dwell < MIN_DWELL_MS:
        messages.append("Displays for less than one second.")
    if cps > FLAG_CPS:
        messages.append(f"Reading speed is {cps:.1f} CPS, above the {FLAG_CPS} CPS review threshold.")
    if spoken_end > next_start:
        messages.append("Overlapping word timings required this caption to end at the next caption's onset.")
    if spoken_end - start > MAX_DWELL_MS:
        messages.append("The measured span exceeds seven seconds. The caption display is capped.")
    if precision_limited:
        messages.append("Millisecond precision limits required joining or rounding some word timings.")
    # Penalize departures from the reading and dwell goals while preserving usable phrase breaks.
    weight = 10 + max(0, cps - TARGET_CPS) + max(0, dwell - TARGET_DWELL_MS) / 100
    return _Cue(start, end, lines, tuple(messages)), weight


def _cues(groups, duration_ms):
    """Choose breaks over a bounded lookahead, so dense speech does not create flash captions."""
    count = len(groups)
    costs = [(0, 0.0)] * (count + 1)
    chosen = [None] * count
    for head in range(count - 1, -1, -1):
        start = groups[head].start
        spoken_end = 0
        text = ""
        precision_limited = False
        pause_cost = 0.0
        best = (math.inf, math.inf)
        for tail in range(head, count):
            word = groups[tail]
            if text and (len(text) + 1 + len(word.text) > 2 * LINE_WIDTH + 1
                         or max(spoken_end, word.end) - start > MAX_DWELL_MS):
                break
            if text:
                pause_cost += max(0, word.start - spoken_end - 550) / 25
            text += (" " if text else "") + word.text
            spoken_end = max(spoken_end, word.end)
            precision_limited |= word.precision_limited
            lines = _wrap(text)
            if tail > head and any(len(line) > LINE_WIDTH for line in lines):
                continue
            following = groups[tail + 1] if tail + 1 < count else None
            next_start = following.start if following else duration_ms
            cue, weight = _make_cue(start, spoken_end, next_start, text, lines, precision_limited)
            if following and next_start - spoken_end < 550:
                weight += _break_cost(word.text.rsplit(" ", 1)[-1], following.text.split(" ", 1)[0])
            remaining_flags, remaining_weight = costs[tail + 1]
            cost = len(cue.messages) + remaining_flags, weight + pause_cost + remaining_weight
            if cost <= best:
                best = cost
                chosen[head] = cue, tail + 1
        costs[head] = best

    head = 0
    while head < count:
        cue, head = chosen[head]
        yield cue


def _stamp(milliseconds):
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def generate(source):
    """Return an SRT and its timing/readability findings without changing approved input."""
    if not isinstance(source, dict):
        raise CaptionError("Captions need approved word timings.")
    words, duration = source.get("words"), source.get("duration")
    if not isinstance(words, (list, tuple)) or not words or not _finite(duration) or duration < 0.001:
        raise CaptionError("Captions need approved words and a positive recording duration.")
    duration_ms = math.floor(duration * 1000)
    srt, issues = [], []
    for index, cue in enumerate(_cues(_millisecond_groups(words, duration, duration_ms), duration_ms), 1):
        text = "\n".join(cue.lines)
        srt.append(f"{index}\n{_stamp(cue.start)} --> {_stamp(cue.end)}\n{text}\n")
        if cue.messages:
            issues.append({
                "cue": index, "start": cue.start / 1000, "end": cue.end / 1000,
                "text": text, "messages": list(cue.messages),
            })
    return "\n".join(srt), {
        "method": METHOD, "cueCount": len(srt), "flaggedCount": len(issues), "issues": issues,
    }
