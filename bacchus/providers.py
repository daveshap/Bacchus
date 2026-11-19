"""Bounded, audited HTTP transport for optional production providers."""

import base64
import binascii
from http.client import HTTPException, IncompleteRead
import json
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import api_log, budget, settings
from .settings import DEFAULT_IMAGE_MODEL, openai_images_enabled, provider_value


class ProviderError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _start_log(request, provider):
    bearer = request.get_header("Authorization", "").removeprefix("Bearer ")
    anthropic = request.get_header("X-api-key", "")
    try:
        return api_log.start(
            settings.api_log_directory(), provider, request.full_url, request.data,
            request.get_header("Content-type", "application/octet-stream"),
            (*settings.provider_secrets(), bearer, anthropic),
        )
    except api_log.ApiLogError as error:
        raise ProviderError("Choose a writable API log folder in Settings before sending a provider request.") from error


def _finish_log(call, status, body=None, **details):
    try:
        call.finish(status, body, **details)
    except api_log.ApiLogError as error:
        raise ProviderError("The provider may have received the request. Fix the API log folder in Settings before retrying.") from error


def send_request(request, provider, *, timeout, limit):
    if budget.active():
        raise budget.BudgetError("This provider operation has no Auto budget adapter.")
    return _send_request(request, provider, timeout=timeout, limit=limit)


def _send_request(request, provider, *, timeout, limit):
    """Send one request and capture up to the adapter's response-byte limit."""
    call = _start_log(request, provider)
    http_error = None
    try:
        response = build_opener(NoRedirect()).open(request, timeout=timeout)
    except HTTPError as error:
        http_error = response = error
    except (HTTPException, OSError, ValueError) as error:
        _finish_log(call, None, error="network_error")
        raise ProviderError(f"The {provider} request did not return. Provider status is unknown.") from error
    with response:
        status = response.getcode()
        headers = response.headers or {}
        request_id = headers.get("request-id") or headers.get("x-request-id")
        content_type = headers.get("content-type") or headers.get("Content-Type")
        content_length = headers.get("content-length") or headers.get("Content-Length") or ""
        read_error = None
        truncated = False
        try:
            raw = response.read(limit + 1)
        except IncompleteRead as error:
            raw, read_error, truncated = error.partial, error, True
        except (HTTPException, OSError, ValueError) as error:
            raw, read_error = None, error
        oversized = raw is not None and len(raw) > limit
        if raw is not None and not oversized and content_length.isdecimal() and len(content_length) <= 19:
            expected = int(content_length)
            if len(raw) < expected and read_error is None:
                read_error = IncompleteRead(raw, expected - len(raw))
                truncated = True
        error_code = None
        if http_error is not None:
            error_code = "http_error"
        elif read_error is not None:
            error_code = "response_read_error"
        elif oversized:
            error_code = "response_too_large"
        _finish_log(
            call, status, raw[:limit] if raw is not None else None,
            content_type=content_type, request_id=request_id, error=error_code,
            truncated=truncated or oversized,
        )
    if http_error is not None:
        raise ProviderError(f"{provider} returned HTTP {status}. Check the API key, model access, and limits.") from http_error
    if read_error is not None:
        raise ProviderError(f"The {provider} response ended before Bacchus could read it.") from read_error
    if oversized:
        raise ProviderError(f"{provider} returned more data than the configured size limit.")
    return raw, request_id


def post_json(url, payload, headers, provider, timeout=125, limit=512 * 1024):
    return budget.run(provider, url, payload,
                      lambda: _count_inputs(url, payload, headers, provider),
                      lambda: _post_json(url, payload, headers, provider, timeout, limit))


def _post_json(url, payload, headers, provider, timeout, limit):
    request = Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={**headers, "content-type": "application/json"}, method="POST",
    )
    raw, request_id = _send_request(request, provider, timeout=timeout, limit=limit)
    try:
        result = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ProviderError(f"{provider} did not return a JSON result.") from error
    return result, request_id


def _count_inputs(url, payload, headers, provider):
    if url == "https://api.anthropic.com/v1/messages":
        suffix = "/count_tokens"
        fields = ("model", "messages", "system", "output_config", "thinking", "tools")
    elif url == "https://api.openai.com/v1/responses":
        suffix = "/input_tokens"
        fields = ("model", "instructions", "input", "text", "reasoning", "tools")
    else:
        raise budget.BudgetError("No input token counter is available for this endpoint.")
    body = {key: payload[key] for key in fields if key in payload}
    counted, _ = _post_json(url + suffix, body, headers, provider, 125, 32 * 1024)
    return counted.get("input_tokens") if isinstance(counted, dict) else None


def post_anthropic_stream(url, payload, headers, provider="Anthropic", *, timeout=180, limit=4 * 1024 * 1024):
    return budget.run(provider, url, payload,
                      lambda: _count_inputs(url, payload, headers, provider),
                      lambda: _anthropic_stream(url, payload, headers, provider, timeout, limit))


def _anthropic_stream(url, payload, headers, provider, timeout, limit):
    """Send a streaming Anthropic Messages request, reassembling text/usage while auditing bytes."""
    stream_payload = {**payload, "stream": True}
    request = Request(
        url, data=json.dumps(stream_payload, ensure_ascii=False).encode("utf-8"),
        headers={**headers, "content-type": "application/json", "accept": "text/event-stream"},
        method="POST",
    )
    call = _start_log(request, provider)
    http_error = None
    try:
        response = build_opener(NoRedirect()).open(request, timeout=timeout)
    except HTTPError as error:
        http_error = response = error
    except (HTTPException, OSError, ValueError) as error:
        _finish_log(call, None, error="network_error")
        raise ProviderError(f"The {provider} request did not return. Provider status is unknown.") from error

    with response:
        status = response.getcode()
        res_headers = response.headers or {}
        request_id = res_headers.get("request-id") or res_headers.get("x-request-id")
        content_type = res_headers.get("content-type") or res_headers.get("Content-Type")
        if http_error is not None:
            raw = response.read(limit + 1)
            _finish_log(call, status, raw[:limit], content_type=content_type, request_id=request_id,
                        error="http_error", truncated=len(raw) > limit)
            raise ProviderError(f"{provider} returned HTTP {status}. Check the API key, model access, and limits.") from http_error

        buf = bytearray()
        oversized = False
        read_error = None
        text_parts = []
        message_id = None
        model_id = payload.get("model", "")
        stop_reason = None
        usage = {}
        final_usage = False
        complete = False
        stream_error = False
        try:
            while True:
                line = response.readline(256 * 1024)
                if not line:
                    break
                if len(buf) + len(line) > limit:
                    oversized = True
                    break
                buf.extend(line)
                decoded = line.decode("utf-8", "replace").strip()
                if not decoded.startswith("data:"):
                    continue
                data_str = decoded[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    event = json.loads(data_str)
                except ValueError:
                    stream_error = True
                    break
                if not isinstance(event, dict):
                    stream_error = True
                    break
                etype = event.get("type")
                if etype == "error":
                    stream_error = True
                    break
                elif etype == "message_stop":
                    complete = True
                elif etype == "message_start":
                    msg = event.get("message") or {}
                    message_id = msg.get("id") or message_id
                    model_id = msg.get("model") or model_id
                    if isinstance(msg.get("usage"), dict):
                        usage.update(msg["usage"])
                elif etype == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                        text_parts.append(delta["text"])
                elif etype == "message_delta":
                    delta = event.get("delta") or {}
                    if delta.get("stop_reason"):
                        stop_reason = delta["stop_reason"]
                    if isinstance(event.get("usage"), dict):
                        usage.update(event["usage"])
                        final_usage = event["usage"].get("output_tokens") is not None
        except (HTTPException, IncompleteRead, OSError, ValueError) as error:
            read_error = error
        error_code = "response_read_error" if (read_error or stream_error or not complete) else None
        if oversized:
            error_code = "response_too_large"
        _finish_log(
            call, status, bytes(buf[:limit]),
            content_type=content_type, request_id=request_id, error=error_code,
            truncated=oversized or read_error is not None,
        )
    if read_error is not None:
        raise ProviderError(f"The {provider} stream ended before Bacchus could finish reading it.") from read_error
    if oversized:
        raise ProviderError(f"{provider} returned more data than the configured size limit.")
    if stream_error or not complete or not message_id:
        raise ProviderError(f"The {provider} stream did not complete. Check the API log before retrying.")
    if not final_usage:
        # message_start reports a partial output count. Leave the reservation in place.
        usage.pop("output_tokens", None)
        usage.pop("total_tokens", None)
    result = {
        "id": message_id or request_id or "streamed",
        "model": model_id,
        "stop_reason": stop_reason,
        "content": [{"type": "text", "text": "".join(text_parts)}],
        "usage": usage or None,
    }
    return result, request_id


def openai_json(endpoint, payload, **options):
    key = provider_value("OPENAI_API_KEY", "").strip()
    if not key:
        raise ProviderError("Add an OpenAI API key in Settings.")
    return post_json(
        f"https://api.openai.com/v1/{endpoint}", payload, {"Authorization": f"Bearer {key}"},
        "OpenAI", **options,
    )


def image_available():
    return openai_images_enabled() and bool(provider_value("OPENAI_API_KEY", "").strip())


def image_request(project_style, asset_id, brief, *, guide="", visual_notes="", palette=None, references=()):
    payload = {
        "model": provider_value("BACCHUS_IMAGE_MODEL", "").strip() or DEFAULT_IMAGE_MODEL,
        "size": "1024x1024", "quality": "high", "n": 1, "output_format": "png",
        "prompt": (
            f"Create one illustration for a narrated video in this visual style: {project_style}. "
            "Use clear silhouettes and an unfussy composition that reads at a small size. "
            "Use a transparent background for an isolated subject and retain backgrounds for complete scenes. "
            "Use the following brief as visual reference. Do not add lettering, logos, or captions. "
            f"Artwork ID: {asset_id}. Visual brief: {brief}"
        ),
    }
    if guide or visual_notes or palette:
        payload["prompt"] += (
            f"\n\nStyle information for this illustration. Apply it to the requested artwork.\n"
            f"{visual_notes[:5000]}\nPalette: {json.dumps(palette or {})}\n{guide}"
        )
    if references:
        payload["prompt"] += " Create the requested subject using the input images as visual style examples."
        payload["images"] = [
            {"image_url": "data:image/png;base64," + base64.b64encode(reference).decode("ascii")}
            for reference in references
        ]
    return payload


def request_image(payload):
    if not openai_images_enabled():
        raise ProviderError("Enable OpenAI artwork generation in Settings to generate images.")
    endpoint = "images/edits" if payload.get("images") else "images/generations"
    result, request_id = openai_json(endpoint, payload, timeout=160, limit=16 * 1024 * 1024)
    try:
        image = base64.b64decode(result["data"][0]["b64_json"], validate=True)
    except (TypeError, KeyError, IndexError, binascii.Error, ValueError) as error:
        raise ProviderError("The image provider did not return an image.") from error
    return image, {"requestId": request_id, "usage": result.get("usage")}
