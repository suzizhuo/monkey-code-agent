"""LLM detection, model pick chain, and JSON HTTP helper."""

import http.client
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

from cage import CageError

# Isolis leftover names the Task 3 pick_model tests still skip with an empty
# blocked set (deepseek-chat -> gw-alpha). CAGE_BLOCKED_MODELS stays extra.
_SKIP_MODELS = {
    "auto",
    "step-5-preview",
    "deepseek-chat",
    "deepseek-flash",
}


def parse_blocked(raw):
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


def _is_blocked(model_id, blocked):
    name = (model_id or "").strip()
    if not name:
        return True
    tail = name.rsplit("/", 1)[-1]
    if name in blocked or tail in blocked:
        return True
    if name in _SKIP_MODELS or tail in _SKIP_MODELS:
        return True
    return False


def pick_model(injected, body, blocked, env):
    blocked = set(blocked or ())
    candidate = (injected or "").strip()
    if candidate and not _is_blocked(candidate, blocked):
        return candidate
    env_model = ""
    default = ""
    if env is not None:
        env_model = (env.get("MCAI_LLM_MODEL") or "").strip()
        default = (env.get("CAGE_DEFAULT_MODEL") or "").strip()
    if env_model and not _is_blocked(env_model, blocked):
        return env_model
    items = body.get("data") if isinstance(body, dict) else None
    catalog = []
    if isinstance(items, list):
        for item in items:
            model_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(model_id, str) and not _is_blocked(model_id, blocked):
                catalog.append(model_id)
    if catalog:
        return catalog[0]
    if default and not _is_blocked(default, blocked):
        return default
    raise CageError("no usable model was injected")


def models_url(base):
    return (base or "").rstrip("/") + "/models"


def http_json(method, url, payload=None, timeout=15, token=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read()
        except Exception:
            raw = b""
    except (urllib.error.URLError, http.client.HTTPException, OSError, ssl.SSLError) as exc:
        raise CageError("request failed: %s (%s)" % (url, exc))
    try:
        body = json.loads(raw.decode("utf-8", "replace")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        body = {}
    return status, body


def detect_llm():
    base = os.environ.get("MCAI_LLM_BASE_URL", "").strip()
    key = os.environ.get("MCAI_LLM_API_KEY", "").strip()
    if not base or not key:
        raise CageError(
            "MonkeyCode LLM was not injected: MCAI_LLM_BASE_URL and "
            "MCAI_LLM_API_KEY are required"
        )
    status, body = http_json("GET", models_url(base), timeout=20, token=key)
    if status in (401, 403):
        raise CageError(
            "MonkeyCode LLM rejected the injected key (HTTP %d); "
            "do not switch gateways" % status
        )
    if status not in (200, 404, 405):
        raise CageError(
            "MonkeyCode LLM at %s is unavailable (HTTP %d)" % (base, status)
        )
    model = pick_model(
        os.environ.get("CAGE_MODEL", "").strip(),
        body,
        parse_blocked(os.environ.get("CAGE_BLOCKED_MODELS", "")),
        os.environ,
    )
    host = urllib.parse.urlsplit(base).netloc
    return {
        "base_url": base,
        "api_key": key,
        "model": model,
        "host": host,
        "key_len": len(key),
        "key_prefix": key[:3] + "..." if len(key) > 3 else "***",
    }
