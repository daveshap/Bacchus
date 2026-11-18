import hashlib
import json
from pathlib import Path

from .providers import ProviderError, openai_json, post_json
from .settings import DEFAULT_OPENAI_MODEL, provider_value


PACK_FOLDER = Path(__file__).resolve().parent.parent / "packs" / "editorial"
PACK_BYTES = (PACK_FOLDER / "pack.json").read_bytes()
PACK = json.loads(PACK_BYTES)
PROMPT_BYTES = (PACK_FOLDER / "plan.md").read_bytes()
LAYOUTS = PACK["layouts"]
MOTIONS = PACK["motions"]
MAX_LINES = 240
MAX_SCENES = 100
MAX_PLANNER_CALLS = 8
OUTPUT_TOKENS = 16000

SCENE_FIELDS = {
    "startLine": {"type": "integer"},
    "endLine": {"type": "integer"},
    "layout": {"type": "string", "enum": LAYOUTS},
    "motion": {"type": "string", "enum": MOTIONS},
    "title": {"type": "string"},
    "caption": {"type": "string"},
    "artId": {"type": "string"},
    "artBrief": {"type": "string"},
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": SCENE_FIELDS,
                "required": list(SCENE_FIELDS),
                "additionalProperties": False,
            },
        },
    },
    "required": ["scenes"],
    "additionalProperties": False,
}
PACK_HASH = hashlib.sha256(
    PACK_BYTES + b"\0" + PROMPT_BYTES + b"\0" + json.dumps(OUTPUT_SCHEMA, sort_keys=True).encode("utf-8")
).hexdigest()


class PlannerError(Exception):
    pass


def planner_provider():
    preferred = provider_value("BACCHUS_PLANNER_PROVIDER", "auto").strip().lower()
    anthropic = bool(provider_value("ANTHROPIC_API_KEY", "").strip()
                     and provider_value("BACCHUS_CLAUDE_MODEL", "").strip())
    openai = bool(provider_value("OPENAI_API_KEY", "").strip())
    if openai and preferred in ("openai", "auto", ""):
        return "openai"
    if anthropic and preferred in ("anthropic", "auto", ""):
        return "anthropic"
    return None


def planner_available():
    return planner_provider() is not None


def starter_plan(lines):
    scenes = []
    stride = (len(lines) + MAX_SCENES - 1) // MAX_SCENES
    for index in range(0, len(lines), stride):
        title = f"Scene {len(scenes) + 1}"
        caption = lines[index]["text"]
        if len(caption) > 170:
            caption = caption[:169].rstrip() + "…"
        scenes.append({
            "startLine": index + 1,
            "endLine": min(index + stride, len(lines)),
            "layout": "editorial",
            "motion": "fade",
            "title": title,
            "caption": caption,
            "artId": "",
            "artBrief": "",
        })
    return scenes


def plan_request(project, reusable=()):
    source = project["input"]
    context = {
        "intention": source["intention"],
        "visualTreatment": project.get("style", "warm-editorial"),
        "narration": source["lines"],
        "availableAssets": [
            {"id": asset["id"], "name": asset["name"], "description": asset.get("brief", "")}
            for asset in project["assets"] if asset["approved"]
        ] + [
            {"id": asset["id"], "name": asset["alias"], "description": asset["brief"]}
            for asset in reusable
        ],
        "maxScenes": MAX_SCENES,
        "maximumDistinctNewArtwork": 24,
    }
    system = (
        PROMPT_BYTES.decode("utf-8") + "\nHonor the supplied visual treatment. "
        "Keep the number of new illustrations under the specified limit and reuse recurring visuals when helpful. "
        "Use the existing layout and motion choices to design meaningful visual beats."
    )
    content = json.dumps(context, ensure_ascii=False)
    if planner_provider() == "openai":
        return {
            "model": provider_value("BACCHUS_OPENAI_MODEL", "").strip() or DEFAULT_OPENAI_MODEL,
            "max_output_tokens": OUTPUT_TOKENS, "store": False,
            "instructions": system, "input": content,
            "text": {"format": {"type": "json_schema", "name": "scene_plan", "strict": True, "schema": OUTPUT_SCHEMA}},
        }
    return {
        "model": provider_value("BACCHUS_CLAUDE_MODEL", "").strip(),
        "max_tokens": OUTPUT_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    }


def request_plan(payload):
    try:
        if "text" in payload:
            result, request_id = openai_json("responses", payload)
            if result.get("status") != "completed":
                raise PlannerError("OpenAI did not complete the plan.")
            content = "".join(
                part["text"] for item in result["output"] if item["type"] == "message"
                for part in item["content"] if part["type"] == "output_text"
            )
        else:
            key = provider_value("ANTHROPIC_API_KEY", "").strip()
            if not key or not payload["model"]:
                raise PlannerError("Add an Anthropic API key and Claude model in Settings.")
            result, request_id = post_json(
                "https://api.anthropic.com/v1/messages", payload,
                {"x-api-key": key, "anthropic-version": "2023-06-01"}, "Anthropic",
            )
            if result["stop_reason"] != "end_turn":
                raise PlannerError(f"Claude did not complete the plan ({result['stop_reason']}).")
            content = "".join(part["text"] for part in result["content"] if part["type"] == "text")
        plan = json.loads(content)
        if set(plan) != {"scenes"}:
            raise ValueError("Unexpected output fields")
        return plan["scenes"], {
            "requestId": request_id,
            "responseId": result["id"],
            "model": result["model"],
            "usage": result.get("usage"),
        }
    except ProviderError as error:
        raise PlannerError(str(error)) from error
    except (KeyError, TypeError, ValueError) as error:
        raise PlannerError("The model did not return a complete scene plan.") from error
