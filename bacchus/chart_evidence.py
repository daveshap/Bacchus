"""Check chart readings against the locked narration and project brief."""

from decimal import Decimal
import re


_TOKEN = re.compile(r"\d+(?:[,.]\d+)*|[^\W\d_]+|[%$€£−+-]", re.UNICODE)
_CLAUSE = re.compile(r"[\n;!?]+|\.(?=\s|$)")
_WORDS = {
    word: value for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen".split()
    )
}
_WORDS.update(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
_SCALE = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000, "trillion": 1_000_000_000_000}
_UNITS = {
    "percent": "%", "percentage": "%", "$": "usd", "dollar": "usd", "dollars": "usd",
    "€": "eur", "euro": "eur", "euros": "eur", "£": "gbp", "pounds": "gbp",
    **{unit + "s": unit for unit in _SCALE},
}
_CURRENCIES = {"usd", "eur", "gbp"}
_QUALIFIERS = set(_SCALE) | _CURRENCIES | {"%"}
_UNCERTAIN = {
    "no", "not", "never", "would", "could", "might", "below", "above", "under", "over",
    "roughly", "nearly", "approximately", "about", "less", "more", "least", "most",
}


def _tokens(text):
    text = re.sub(r"\bper\s+cent\b", "percent", text.casefold())
    return tuple(_UNITS.get(word, word) for word in _TOKEN.findall(text))


def _label_positions(words, label):
    return [start for start in range(len(words) - len(label) + 1)
            if words[start:start + len(label)] == label]


def _quantity(words, start):
    if start >= len(words):
        return None
    pos = start
    prefix = words[pos] if words[pos] in _CURRENCIES else None
    pos += int(prefix is not None)
    if pos >= len(words):
        return None
    negative = words[pos] in ("-", "−", "minus", "negative")
    pos += int(negative or words[pos] in ("+", "plus"))
    if pos >= len(words):
        return None
    word = words[pos]
    if word[0].isdigit():
        if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", word):
            return None
        value = Decimal(word.replace(",", ""))
        pos += 1
    elif word in _WORDS:
        value = _WORDS[word]
        pos += 1
        if 20 <= value <= 90 and pos + 1 < len(words) and words[pos] == "-" and words[pos + 1] in _WORDS:
            pos += 1
        if pos < len(words) and 20 <= value <= 90 and 0 < _WORDS.get(words[pos], 10) < 10:
            value += _WORDS[words[pos]]
            pos += 1
        if pos < len(words) and value < 10 and words[pos] == "hundred":
            value *= 100
            pos += 1
            if pos + 1 < len(words) and words[pos] == "and" and words[pos + 1] in _WORDS:
                pos += 1
            if pos < len(words) and words[pos] in _WORDS:
                last = _WORDS[words[pos]]
                value += last
                pos += 1
                if pos < len(words) and 20 <= last <= 90 and 0 < _WORDS.get(words[pos], 10) < 10:
                    value += _WORDS[words[pos]]
                    pos += 1
        if pos + 1 < len(words) and words[pos] == "point":
            end = pos + 1
            while end < len(words) and 0 <= _WORDS.get(words[end], 10) < 10:
                end += 1
            if end > pos + 1:
                digits = "".join(str(_WORDS[word]) for word in words[pos + 1:end])
                value = Decimal(f"{value}.{digits}")
                pos = end
    else:
        return None
    return (-value if negative else value), pos, prefix


def _matches(words, start, item, unit, *, stop=None):
    quantity = _quantity(words, start)
    if quantity is None:
        return False
    value, end, currency = quantity
    if value != Decimal(str(item["value"])) or end > (stop if stop is not None else len(words)):
        return False
    remaining = words[end:stop]
    if currency and currency in unit:
        unit = tuple(word for word in unit if word != currency)
        if not unit and remaining and remaining[0] in _SCALE:
            return False
    elif currency:
        return False
    if unit:
        return remaining[:len(unit)] == unit
    return not remaining or remaining[0] not in _QUALIFIERS


def _supported(clauses, item, labels, unit):
    label = _tokens(item["label"])
    if not label:
        return False
    for words in clauses:
        if "if" in words or "unless" in words:
            continue
        other_positions = [
            (position, position + len(other))
            for other in labels if other != label for position in _label_positions(words, other)
        ]
        for label_start in _label_positions(words, label):
            label_end = label_start + len(label)
            if any(start <= label_start < end and end > label_end for start, end in other_positions):
                continue
            stop = min((start for start, _ in other_positions if start >= label_end), default=len(words))
            for start in range(label_end, min(stop, label_end + 9)):
                if _quantity(words, start) is not None:
                    if _UNCERTAIN.isdisjoint(words[label_end:start]) and _matches(words, start, item, unit, stop=stop):
                        return True
                    break
    return False


def chart_findings(source, scenes):
    """List each chart item whose value and unit lack matching labeled source evidence."""
    brief = _CLAUSE.split(source.get("intention", ""))
    findings = []
    for index, scene in enumerate(scenes, 1):
        charts = [layer for layer in scene.get("layers", []) if layer["kind"] in ("bar-chart", "meter", "counter")]
        if not charts:
            continue
        narration = " ".join(line["text"] for line in source["lines"][scene["startLine"] - 1:scene["endLine"]])
        clauses = [_tokens(text) for text in [*_CLAUSE.split(narration), *brief] if text.strip()]
        for chart in charts:
            labels = [_tokens(item["label"]) for item in chart["items"]]
            unit = _tokens(chart["unit"])
            for item in chart["items"]:
                if not _supported(clauses, item, labels, unit):
                    display = f"{item['value']:g}" + (f" {chart['unit']}" if chart["unit"] else "")
                    findings.append(
                        f"Scene {index}, chart {chart['id']!r}: could not match {item['label']!r} to {display} "
                        "in the scene narration or project brief. Correct it or include the exact labeled data in the brief."
                    )
    return findings
