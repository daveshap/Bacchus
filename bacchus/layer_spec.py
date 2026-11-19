"""Typed scene layers and cue anchors shared by planning and rendering."""

import re


KINDS = (
    "text", "art", "bar-chart", "flow",
    "card", "meter", "counter", "checklist", "callout", "comparison", "figure", "prop",
)
BFG_SUBJECTS = {
    "figure": ("stand", "walk", "think", "point", "speak", "sit", "reach", "wave"),
    "prop": ("book", "clock", "hourglass", "laptop", "brain", "coin-stack"),
}
EFFECTS = ("show", "fade", "rise", "wipe", "pop", "draw")
MAX_LAYERS = 16
MAX_CANVAS_CODE_CHARS = 24_000
IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class LayerError(ValueError):
    pass


def _object(fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_NUMBER = {"type": "number"}
_CUE = {"line": _INTEGER, "word": _INTEGER}
LAYER_SCHEMA = _object({
    "id": _STRING,
    "kind": {"type": "string", "enum": list(KINDS)},
    "box": _object({field: _INTEGER for field in ("x", "y", "w", "h")}),
    "text": _STRING,
    "artId": _STRING,
    "artBrief": _STRING,
    "items": {"type": "array", "items": _object({"label": _STRING, "value": _NUMBER})},
    "unit": _STRING,
    "enter": _object({**_CUE, "effect": {"type": "string", "enum": list(EFFECTS)}, "duration": _NUMBER}),
    "leave": _object(_CUE),
})


def scene_art(scene):
    """Yield every separate planned use, in scene layer order."""
    if isinstance(scene.get("artId"), str) and scene["artId"]:
        yield {"artId": scene["artId"], "artBrief": scene.get("artBrief", ""), "layerId": ""}
    layers = scene.get("layers")
    for layer in layers if isinstance(layers, list) else ():
        if (
            isinstance(layer, dict) and layer.get("kind") == "art"
            and isinstance(layer.get("artId"), str) and layer["artId"]
        ):
            yield {"artId": layer["artId"], "artBrief": layer.get("artBrief", ""), "layerId": layer.get("id", "")}


def _fields(value, schema, name):
    if not isinstance(value, dict) or value.keys() != schema["properties"].keys():
        raise LayerError(f"{name} has missing or unsupported fields.")


def _text(value, label, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise LayerError(f"{label} must be text of at most {maximum} characters.")
    if any(ord(char) < 32 and char not in "\n\t\r" or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise LayerError(f"{label} contains an unsupported character.")
    value = value.strip()
    if required and not value:
        raise LayerError(f"{label} is required.")
    return value


def _identifier(value, name):
    if not isinstance(value, str) or len(value) > 64 or not IDENTIFIER.fullmatch(value):
        raise LayerError(f"{name} must use lowercase letters, digits and single hyphens.")


def _box(box):
    _fields(box, LAYER_SCHEMA["properties"]["box"], "Placement")
    x, y, w, h = (box[key] for key in ("x", "y", "w", "h"))
    if any(type(value) is not int for value in (x, y, w, h)) or not (
        0 <= x < 100 and 0 <= y < 100 and 1 <= w <= 100 - x and 1 <= h <= 100 - y
    ):
        raise LayerError("The box must fit inside the scene content area using whole percentages.")
    return dict(box)


def scene_bounds(source, scene):
    lines = source["lines"]
    start = 0.0 if scene["startLine"] == 1 else lines[scene["startLine"] - 1]["start"]
    end = lines[scene["endLine"]]["start"] if scene["endLine"] < len(lines) else source["duration"]
    return start, end


def _cue(source, scene, cue, bounds, *, leaving=False):
    line, word = cue["line"], cue["word"]
    name = "Leave cue" if leaving else "Entrance cue"
    if type(line) is not int or type(word) is not int or min(line, word) < 0 or line and word:
        raise LayerError(f"{name} must select a scene edge, one narration line or one word ID.")
    if line:
        if not scene["startLine"] <= line <= scene["endLine"]:
            raise LayerError(f"{name} line {line} is outside the scene.")
        return source["lines"][line - 1]["start"]
    if word:
        words = source.get("words") or []
        if word > len(words) or words[word - 1]["id"] != word:
            raise LayerError(f"{name} word {word} is unavailable in the locked narration.")
        start = words[word - 1]["start"]
        if not bounds[0] <= start < bounds[1]:
            raise LayerError(f"{name} word {word} is outside the scene.")
        return start
    return bounds[1] if leaving else bounds[0]


def schedule(source, scene, layer):
    """Resolve the layer's absolute entrance, end of entrance motion and exit times."""
    bounds = scene_bounds(source, scene)
    start = _cue(source, scene, layer["enter"], bounds)
    end = _cue(source, scene, layer["leave"], bounds, leaving=True)
    settled = start + layer["enter"]["duration"]
    if end <= start or settled > end + 1e-9:
        raise LayerError("The layer must hold after its entrance and its motion must fit before it leaves.")
    return start, settled, end


_ITEM_BOUNDS = {
    "bar-chart": (1, 8),
    "flow": (2, 6),
    "meter": (1, 4),
    "counter": (1, 1),
    "checklist": (2, 8),
    "comparison": (2, 8),
    "card": (0, 6),
}


def _items(value, kind):
    if not isinstance(value, list):
        raise LayerError("Items must be a list.")
    bounds = _ITEM_BOUNDS.get(kind, (0, 0))
    if not bounds[0] <= len(value) <= bounds[1]:
        raise LayerError(f"The {kind} layer needs {bounds[0]} to {bounds[1]} items.")
    checked = []
    for raw in value:
        _fields(raw, LAYER_SCHEMA["properties"]["items"]["items"], "Chart or diagram item")
        label = _text(raw["label"], "Item label", 72, True)
        number = raw["value"]
        if type(number) not in (int, float) or not -1e12 <= number <= 1e12:
            raise LayerError("Chart values must be finite numbers within ±1 trillion.")
        if kind in ("flow", "card") and number != 0:
            raise LayerError(f"{kind.capitalize()} items carry labels only and need a neutral value of zero.")
        if kind in ("checklist", "comparison") and number not in (-1, 0, 1):
            raise LayerError(f"{kind.capitalize()} item values must be -1, 0, or 1.")
        checked.append({"label": label, "value": number})
    return checked


def _validate_layer(source, scene, layer):
    _fields(layer, LAYER_SCHEMA, "Layer")
    _identifier(layer["id"], "Layer ID")
    kind = layer["kind"]
    if kind not in KINDS:
        raise LayerError(f"Unsupported layer kind {kind!r}.")
    text_required = kind in ("text", "callout")
    text = _text(layer["text"], "Layer text", 260, text_required)
    if kind in BFG_SUBJECTS and text not in BFG_SUBJECTS[kind]:
        raise LayerError(f"Choose a BFG {kind} from {', '.join(BFG_SUBJECTS[kind])}.")
    art_id = _text(layer["artId"], "Artwork ID", 64)
    brief = _text(layer["artBrief"], "Artwork brief", 380)
    unit = _text(layer["unit"], "Chart unit", 24)
    if kind == "art":
        _identifier(art_id, "Artwork ID")
        if text:
            raise LayerError("Use a separate text layer to label artwork.")
    elif art_id or brief:
        raise LayerError("Only an artwork layer can specify an artwork ID or brief.")
    if unit and kind not in ("bar-chart", "meter", "counter"):
        raise LayerError("Only a bar-chart, meter, or counter can specify a unit.")
    if kind == "card" and not text and not layer["items"]:
        raise LayerError("A card layer needs a heading or bullet items.")
    enter = layer["enter"]
    leave = layer["leave"]
    _fields(enter, LAYER_SCHEMA["properties"]["enter"], "Entrance")
    _fields(leave, LAYER_SCHEMA["properties"]["leave"], "Exit")
    effect, duration = enter["effect"], enter["duration"]
    if effect not in EFFECTS:
        raise LayerError(f"Unsupported layer entrance {effect!r}.")
    if type(duration) not in (int, float) or not (duration == 0 if effect == "show" else 0 < duration <= 3):
        raise LayerError("Show needs zero duration. Animated entrances need more than zero and at most 3 seconds.")
    result = {
        **layer, "box": _box(layer["box"]), "text": text, "artId": art_id, "artBrief": brief,
        "items": _items(layer["items"], kind), "unit": unit, "enter": dict(enter), "leave": dict(leave),
    }
    schedule(source, scene, result)
    return result


def validate_layers(project, scene):
    layers = scene.get("layers", [])
    if not isinstance(layers, list):
        raise LayerError("Scene layers must be a list.")
    if scene["layout"] != "layered":
        if layers:
            raise LayerError("Choose the layered layout to add independent layers.")
        return []
    if scene.get("artId") or scene.get("artBrief"):
        raise LayerError("A layered scene assigns artwork within layers rather than at the scene level.")
    if not 1 <= len(layers) <= MAX_LAYERS:
        raise LayerError(f"A layered scene needs 1 to {MAX_LAYERS} layers.")
    checked = []
    ids = set()
    for index, layer in enumerate(layers, start=1):
        try:
            value = _validate_layer(project["input"], scene, layer)
            if value["id"] in ids:
                raise LayerError(f"Layer ID {value['id']!r} is already used in this scene.")
        except LayerError as error:
            raise LayerError(f"Layer {index}: {error}") from error
        checked.append(value)
        ids.add(value["id"])
    return checked
