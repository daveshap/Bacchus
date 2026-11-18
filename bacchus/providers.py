"""Small JSON-only HTTP transport for optional production providers."""

import base64
import binascii
import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .settings import DEFAULT_IMAGE_MODEL, provider_value


class ProviderError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_json(url, payload, headers, provider, timeout=125, limit=512 * 1024):
    request = Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={**headers, "content-type": "application/json"}, method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(limit + 1)
            request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
    except HTTPError as error:
        raise ProviderError(f"{provider} returned HTTP {error.code}. Check the API key, model access, and limits.") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ProviderError(f"The {provider} request did not return. No automatic retry was sent.") from error
    if len(raw) > limit:
        raise ProviderError(f"{provider} returned more data than the configured size limit.")
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
    return bool(provider_value("OPENAI_API_KEY", "").strip())


def image_request(project_style, asset_id, brief):
    return {
        "model": provider_value("BACCHUS_IMAGE_MODEL", "").strip() or DEFAULT_IMAGE_MODEL,
        "size": "1024x1024", "quality": "medium", "n": 1, "output_format": "png",
        "prompt": (
            f"Create one illustration for a narrated video in this visual style: {project_style}. "
            "Use clear silhouettes and an unfussy composition that reads at a small size. "
            "Use the following brief as visual reference. Do not add lettering, logos, or captions. "
            f"Artwork ID: {asset_id}. Visual brief: {brief}"
        ),
    }


def request_image(payload):
    result, request_id = openai_json("images/generations", payload, timeout=160, limit=16 * 1024 * 1024)
    try:
        image = base64.b64decode(result["data"][0]["b64_json"], validate=True)
    except (TypeError, KeyError, IndexError, binascii.Error, ValueError) as error:
        raise ProviderError("The image provider did not return an image.") from error
    return image, {"requestId": request_id, "usage": result.get("usage")}
