"""Durable, per-project reservations for provider calls made by the Auto worker."""

from contextlib import closing, contextmanager
from contextvars import ContextVar
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
import re
import sqlite3

from .storage_paths import writable_path


class BudgetError(ValueError):
    pass


_ACTIVE = ContextVar("bacchus_auto_budget", default=None)
_RATE_FIELDS = {"inputUsdPerMillion", "outputUsdPerMillion", "imageMaxUsd", "imageMaxTokens"}
_MODEL = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_MICROS = Decimal(1_000_000)
_TEXT_URLS = {
    "https://api.anthropic.com/v1/messages",
    "https://api.openai.com/v1/responses",
}
_IMAGE_URLS = {
    "https://api.openai.com/v1/images/generations",
    "https://api.openai.com/v1/images/edits",
}


def _number(value, field, *, whole=False, positive=False):
    if type(value) not in (int, float) or (whole and type(value) is not int):
        raise BudgetError(f"{field} must be a {'whole ' if whole else ''}number.")
    n = Decimal(str(value))
    if not n.is_finite() or n < 0 or (positive and n == 0) or n > 1_000_000_000 or n.as_tuple().exponent < -6:
        raise BudgetError(f"Enter a valid {field} with at most six decimal places.")
    return value


def validate_limits(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or value.keys() - {"maxTokens", "maxUsd", "rates"}:
        raise BudgetError("Auto limits support maxTokens, maxUsd and model rates.")
    rates = value.get("rates", {})
    if not isinstance(rates, dict):
        raise BudgetError("Model rates must be an object keyed by model ID.")
    accepted = {}
    for model, rate in rates.items():
        if not isinstance(model, str) or not _MODEL.fullmatch(model) or model.lower().startswith("sk-"):
            raise BudgetError("Use a model ID for each rate.")
        if not isinstance(rate, dict) or not rate or rate.keys() - _RATE_FIELDS:
            raise BudgetError(f"Check the rate fields for {model}.")
        accepted[model] = {k: _number(v, k, whole=k == "imageMaxTokens", positive=True) for k, v in rate.items()}
    tokens, usd = value.get("maxTokens"), value.get("maxUsd")
    if tokens is not None:
        _number(tokens, "maxTokens", whole=True)
    if usd is not None:
        _number(usd, "maxUsd")
    return {"maxTokens": tokens, "maxUsd": usd, "rates": accepted}


@contextmanager
def scope(folder, limits=None):
    marker = _ACTIVE.set((Path(folder), validate_limits(limits)))
    try:
        yield
    finally:
        _ACTIVE.reset(marker)


def active():
    return _ACTIVE.get() is not None


def _path(folder):
    return Path(folder) / "records" / "auto-usage.sqlite3"


@contextmanager
def _ledger(folder):
    path = writable_path(_path(folder))
    for suffix in ("-journal", "-wal", "-shm"):
        writable_path(path.with_name(path.name + suffix))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY, started TEXT DEFAULT CURRENT_TIMESTAMP,
                provider TEXT, model TEXT, kind TEXT, state TEXT, rates TEXT,
                input_tokens INTEGER, output_tokens INTEGER, tokens INTEGER, micro_usd INTEGER,
                request_id TEXT)""")
            yield db
    except (sqlite3.Error, OSError) as error:
        raise BudgetError("Bacchus could not read or write the project Auto usage ledger.") from error


def _cost(kind, rate, input_tokens, output_tokens, total_tokens=None):
    if kind == "image":
        return int(Decimal(str(rate["imageMaxUsd"])) * _MICROS) if "imageMaxUsd" in rate else None
    if not {"inputUsdPerMillion", "outputUsdPerMillion"} <= rate.keys():
        return None
    input_rate = Decimal(str(rate["inputUsdPerMillion"]))
    output_rate = Decimal(str(rate["outputUsdPerMillion"]))
    if input_tokens is None or output_tokens is None:
        if total_tokens is None:
            return None
        cost = total_tokens * max(input_rate, output_rate)
    else:
        cost = input_tokens * input_rate + output_tokens * output_rate
        unclassified = max(0, (total_tokens or 0) - input_tokens - output_tokens)
        cost += unclassified * max(input_rate, output_rate)
    return int(cost.to_integral_value(rounding=ROUND_CEILING))


def _totals(db, rates):
    tokens = micro_usd = unknown_tokens = unknown_usd = calls = held = 0
    for row in db.execute("SELECT model,kind,state,input_tokens,output_tokens,tokens,micro_usd FROM calls"):
        calls += 1
        held += row["state"] == "held"
        count = row["tokens"]
        rate = rates.get(row["model"], {})
        if count is None and row["kind"] == "image":
            count = rate.get("imageMaxTokens")
        cost = row["micro_usd"]
        if cost is None:
            cost = _cost(row["kind"], rate, row["input_tokens"], row["output_tokens"], count)
        unknown_tokens += count is None
        unknown_usd += cost is None
        tokens += count or 0
        micro_usd += cost or 0
    return {"tokens": tokens, "microUsd": micro_usd, "unknownTokenCalls": unknown_tokens,
            "unknownUsdCalls": unknown_usd, "calls": calls, "heldCalls": held}


def summary(folder, limits=None):
    if _path(folder).exists():
        with _ledger(folder) as db:
            totals = _totals(db, validate_limits(limits)["rates"])
    else:
        totals = {"tokens": 0, "microUsd": 0, "unknownTokenCalls": 0,
                  "unknownUsdCalls": 0, "calls": 0, "heldCalls": 0}
    return {**totals, "usd": float(Decimal(totals["microUsd"]) / _MICROS)}


def _check(limits, totals, tokens, cost):
    if limits["maxTokens"] is not None:
        if totals["unknownTokenCalls"]:
            raise BudgetError("An earlier Auto call has unknown token usage. The project token limit cannot be verified.")
        if tokens is None:
            raise BudgetError("Auto cannot bound all tokens. Set the image model's maximum tokens per request.")
        if totals["tokens"] + tokens > limits["maxTokens"]:
            raise BudgetError("This provider request would exceed the project's Auto token limit.")
    if limits["maxUsd"] is not None:
        if totals["unknownUsdCalls"]:
            raise BudgetError("An earlier Auto call has unknown cost. Check recorded usage and configure rates for earlier models.")
        if cost is None:
            raise BudgetError("Set the image model's maximum USD per request before using a spending limit.")
        if totals["microUsd"] + cost > Decimal(str(limits["maxUsd"])) * _MICROS:
            raise BudgetError("This provider request would exceed the project's Auto spending limit.")


def _counts(result):
    usage = result.get("usage") if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        return None, None, None
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "total_tokens")
    if any(type(usage[k]) is not int or not 0 <= usage[k] <= 1_000_000_000 for k in keys if k in usage):
        return None, None, None
    if "input_tokens" in usage and "output_tokens" in usage:
        incoming = usage["input_tokens"] + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
        outgoing = usage["output_tokens"]
        return incoming, outgoing, max(incoming + outgoing, usage.get("total_tokens", 0))
    return None, None, usage.get("total_tokens")


def run(provider, url, payload, count_inputs, send):
    """Reserve before sending. On transport failure or process exit the full hold survives."""
    context = _ACTIVE.get()
    if context is None:
        return send()
    folder, limits = context
    model = payload.get("model")
    if url not in _TEXT_URLS | _IMAGE_URLS or not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise BudgetError("Auto cannot meter this provider request.")
    if limits["maxTokens"] == 0 or limits["maxUsd"] == 0:
        raise BudgetError("The project has a zero Auto budget. No provider request was sent.")
    rate = limits["rates"].get(model, {})
    kind = "image" if url in _IMAGE_URLS else "text"
    incoming = outgoing = None
    if kind == "image":
        if payload.get("n", 1) != 1:
            raise BudgetError("Auto meters one generated image per request.")
        tokens = rate.get("imageMaxTokens")
    else:
        outgoing = payload.get("max_tokens", payload.get("max_output_tokens"))
        if type(outgoing) is not int or not 0 < outgoing <= 1_000_000_000 or payload.get("tools"):
            raise BudgetError("Auto requires a bounded text request without separately billed tools.")
        if limits["maxUsd"] is not None and not {"inputUsdPerMillion", "outputUsdPerMillion"} <= rate.keys():
            raise BudgetError(f"Set maximum input and output USD rates per million tokens for {model}.")
        if limits["maxTokens"] is not None or limits["maxUsd"] is not None:
            # Providers describe their input count as an estimate. Include template headroom,
            # plus the whole requested output (including reasoning) before authorizing inference.
            count = count_inputs()
            if type(count) is not int or not 0 <= count <= 1_000_000_000:
                raise BudgetError("The provider did not return a usable input token count.")
            incoming = count + max(256, (count + 9) // 10)
        # A failed uncapped vision call cannot be priced from compressed image bytes.
        # Keep its input usage unknown until the provider supplies a complete report.
        tokens = incoming + outgoing if incoming is not None else None
    cost = _cost(kind, rate, incoming, outgoing)
    with _ledger(folder) as db:
        _check(limits, _totals(db, limits["rates"]), tokens, cost)
        call = db.execute("""INSERT INTO calls (provider, model, kind, state, rates, input_tokens,
            output_tokens, tokens, micro_usd) VALUES (?, ?, ?, 'held', ?, ?, ?, ?, ?)""",
            (provider, model, kind, json.dumps(rate), incoming, outgoing, tokens, cost)).lastrowid
    result, request_id = send()
    used_in, used_out, used_tokens = _counts(result)
    actual_cost = _cost(kind, rate, used_in, used_out, used_tokens)
    with _ledger(folder) as db:
        state = "reported" if used_tokens is not None else "held"
        db.execute("""UPDATE calls SET state=?, input_tokens=COALESCE(?,input_tokens),
            output_tokens=COALESCE(?,output_tokens), tokens=COALESCE(?,tokens),
            micro_usd=COALESCE(?,micro_usd), request_id=? WHERE id=?""",
            (state, used_in, used_out, used_tokens, actual_cost, request_id, call))
    if ((limits["maxTokens"] is not None and used_tokens is not None and used_tokens > tokens)
            or (limits["maxUsd"] is not None and actual_cost is not None and actual_cost > cost)):
        raise BudgetError("Reported usage exceeded the reserved ceiling. Auto stopped. Check the project limits and model rates.")
    return result, request_id
