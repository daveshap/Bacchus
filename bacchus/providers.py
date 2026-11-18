"""Bounded, audited HTTP transport for optional production providers."""

import base64
import binascii
from http.client import HTTPException, IncompleteRead
import json
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import api_log, settings
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
    request = Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={**headers, "content-type": "application/json"}, method="POST",
    )
    raw, request_id = send_request(request, provider, timeout=timeout, limit=limit)
    try:
        result = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ProviderError(f"{provider} did not return a JSON result.") from error
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
        "size": "1024x1024", "quality": "medium", "n": 1, "output_format": "png",
        "prompt": (
            f"Create one illustration for a narrated video in this visual style: {project_style}. "
            "Use clear silhouettes and an unfussy composition that reads at a small size. "
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
