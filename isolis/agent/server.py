#!/usr/bin/env python3
"""Isolis in-container / process agent runtime.

Stdlib HTTP server + provider-neutral chat with a bounded tool set. The wire
protocol is negotiated once per process (spec 5.4): a one-token, six-second
probe of {base}/chat/completions; HTTP 404/405 selects the Anthropic-style
client, anything else the OpenAI-style client. LangChain is used when it is
installed; otherwise a stdlib implementation of both wire paths answers, so
/chat works on plain python3 too. /health and /info answer even when the
framework or the platform LLM is missing.

Environment (host forwards these into the container/process):
  MCAI_LLM_BASE_URL / MCAI_LLM_API_KEY / MCAI_LLM_MODEL  platform injection
  ISOLIS_AGENT_NAME    agent name (also selects this runtime when present)
  ISOLIS_PROFILE       strict|tooled (default strict)
  ISOLIS_INSTALL       1 enables install_packages, 0 refuses it
  ISOLIS_SOURCE        what agent/ was built from (reported by /info)
  ISOLIS_WORKSPACE     writable dir (default /work)
  ISOLIS_SYSTEM_PROMPT override the built-in system prompt
  ISOLIS_MODEL         model override, wins over MCAI_LLM_MODEL
  ISOLIS_DEFAULT_MODEL fallback when nothing is injected
  ISOLIS_MAX_TURNS          live turn pairs before eviction (default 400)
  ISOLIS_MAX_MESSAGE_CHARS per-message cap (default 900000)
  ISOLIS_CONTEXT_TOKENS    token budget before compression (default 900000)
  ISOLIS_COMPRESS_BATCH    messages folded per compression pass (default 40)
  ISOLIS_ARCHIVE_MAX       archive entries kept, never sent to the model (4000)
  ISOLIS_BIND              listen address (default 0.0.0.0)
  ISOLIS_PORT              listen port (default 8080)
  ISOLIS_IMPORT_MAX_BYTES  import size cap (default 536870912)
"""

import io
import json
import os
import re
import socket
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import urllib.error
import urllib.request

INSTALLER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install-android-sdk.sh")
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_openai import ChatOpenAI

    HAS_LANGCHAIN = True
except Exception:  # ImportError or a broken install: /health must still work
    HAS_LANGCHAIN = False
    ChatOpenAI = None

    class _Msg(object):
        def __init__(self, content="", tool_calls=None, tool_call_id="", **_kw):
            self.content = content
            self.tool_calls = list(tool_calls or [])
            self.tool_call_id = tool_call_id

    class SystemMessage(_Msg):
        role = "system"

    class HumanMessage(_Msg):
        role = "user"

    class AIMessage(_Msg):
        role = "assistant"

    class ToolMessage(_Msg):
        role = "tool"

AGENT_NAME = os.environ.get("ISOLIS_AGENT_NAME", "").strip() or "default"
AGENT_SOURCE = os.environ.get("ISOLIS_SOURCE", "").strip() or "local"
AGENT_PROFILE = os.environ.get("ISOLIS_PROFILE", "").strip() or "strict"
INSTALL_ENABLED = os.environ.get("ISOLIS_INSTALL", "").strip().lower() in ("1", "true", "yes", "on")
WORKSPACE = os.environ.get("ISOLIS_WORKSPACE", "").strip() or "/work"

MAX_TOOL_STEPS = 8
SHELL_TIMEOUT = 120
INSTALL_TIMEOUT = 1800
MAX_OUTPUT = 20000
SUMMARY_MAX_CHARS = 8000
SUMMARY_INPUT_CHARS = 60000
IMPORT_MAX_BYTES = 512 * 1024 * 1024
EXPORT_MAX_BYTES = 512 * 1024 * 1024
SUMMARY_MARKER = "[compressed history]"
STARTED_AT = time.time()

BASE_URL = os.environ.get("MCAI_LLM_BASE_URL", "").strip()
API_KEY = os.environ.get("MCAI_LLM_API_KEY", "").strip()
MODEL = (
    os.environ.get("ISOLIS_MODEL", "").strip()
    or os.environ.get("MCAI_LLM_MODEL", "").strip()
    or os.environ.get("ISOLIS_DEFAULT_MODEL", "").strip()
    or "isolis-chat-default"
)
INJECTED = bool(BASE_URL and API_KEY)
LLM_HOST = urlparse(BASE_URL).netloc if BASE_URL else ""


def _int_env(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


MAX_MESSAGE_CHARS = _int_env("ISOLIS_MAX_MESSAGE_CHARS", 900000)
MAX_TURNS = max(1, _int_env("ISOLIS_MAX_TURNS", 400))
COMPRESS_BATCH = max(1, _int_env("ISOLIS_COMPRESS_BATCH", 40))
ARCHIVE_MAX = max(0, _int_env("ISOLIS_ARCHIVE_MAX", 4000))
CONTEXT_TOKENS = _int_env("ISOLIS_CONTEXT_TOKENS", 900000)
CONTEXT_HARD_LIMIT = 1000000
if CONTEXT_TOKENS > CONTEXT_HARD_LIMIT:
    sys.stderr.write(
        "isolis-agent: ISOLIS_CONTEXT_TOKENS %d exceeds the upstream limit %d; clamping\n"
        % (CONTEXT_TOKENS, CONTEXT_HARD_LIMIT)
    )
    CONTEXT_TOKENS = CONTEXT_HARD_LIMIT


# ---------------------------------------------------------------- text utils


def safe_text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    try:
        value.encode("utf-8")
        return value
    except UnicodeEncodeError:
        try:
            raw = value.encode("utf-8", "surrogateescape")
        except Exception:
            return value.encode("utf-8", "replace").decode("utf-8")
        for encoding in ("utf-8", "gb18030"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")


def json_sanitize(value):
    if isinstance(value, str):
        return safe_text(value)
    if isinstance(value, dict):
        return {safe_text(str(key)): json_sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_sanitize(item) for item in value]
    return value


def _encoder():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


ENCODER = _encoder()


def count_tokens(text):
    if not text:
        return 0
    text = safe_text(text)
    if ENCODER is not None:
        try:
            return len(ENCODER.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, len(text) // 4)


def truncate_message(text, cap=None):
    if cap is None:
        cap = MAX_MESSAGE_CHARS
    text = safe_text(text)
    if cap <= 0 or len(text) <= cap:
        return text, False
    keep = max(2, cap - 120)
    head = keep // 2
    tail = keep - head
    note = "\n...[truncated %d chars from the middle]...\n" % (len(text) - keep)
    return text[:head] + note + text[-tail:], True


def content_to_text(content):
    if isinstance(content, str):
        return safe_text(content)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return safe_text(text)
        return json.dumps(json_sanitize(content), ensure_ascii=False)
    if isinstance(content, list):
        return safe_text("".join(content_to_text(part) for part in content))
    return safe_text(content)


def common_fields():
    return {
        "agent": AGENT_NAME,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
    }


# ------------------------------------------------------------------- prompts


def default_system_prompt():
    lines = [
        "You are Isolis, an assistant running inside an isolated sandbox built "
        "with LangChain. You cannot see the host filesystem, other agent "
        "containers, or the Docker socket.",
        "Your writable workspace is %s. Keep files there." % WORKSPACE,
        "Match the user's language. Be concise and factual. Verify claims by "
        "running commands before reporting success.",
    ]
    if INSTALL_ENABLED:
        lines.append(
            "This sandbox grants the tooled profile: install development "
            "packages with install_packages (apt, pip, npm)."
        )
    else:
        lines.append(
            "This sandbox grants the strict profile: package installation is "
            "disabled. Work with what the image provides."
        )
    names = "run_command, write_file, read_file, list_files, browse"
    if INSTALL_ENABLED:
        names += ", install_packages, install_android_sdk"
    lines.append(
        "You may configure the sandbox yourself: install SDKs, JDKs, "
        "toolchains and packages as the task requires (tooled profile). "
        "Available tools: %s. Call them through the tool interface." % names
    )
    return " ".join(lines)


def system_prompt():
    env = os.environ.get("ISOLIS_SYSTEM_PROMPT", "").strip()
    return env or default_system_prompt()


# --------------------------------------------------------------------- LLM

_PROTO = None
_PROTO_LOCK = threading.Lock()
_LLM = None
LLM_LOCK = threading.Lock()
STD_TIMEOUT = 60
STD_RETRIES = 1
STD_TEMPERATURE = 0.4
STD_MAX_TOKENS = 4096


class SummaryMessage(object):
    """The single [compressed history] entry.

    It is an assistant-role message on the wire (D: fold product is one
    assistant message, at most one ever exists) while keeping its own type
    name in /history and the archive.
    """

    role = "assistant"

    def __init__(self, content=""):
        self.content = content
        self.tool_calls = []
        self.tool_call_id = ""


def _is_marker_message(message):
    return safe_text(getattr(message, "content", "") or "").startswith(SUMMARY_MARKER)


def _post_json(url, body, headers, timeout=STD_TIMEOUT):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception:
            return exc.code, ""
    except Exception as exc:
        raise RuntimeError("upstream request failed: %s" % exc)


def _wire_headers():
    return {
        "Authorization": "Bearer " + API_KEY,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
        "Accept": "application/json",
    }


def _message_role(message):
    role = getattr(message, "role", "")
    if role:
        return role
    if isinstance(message, SystemMessage):
        return "system"
    if isinstance(message, ToolMessage):
        return "tool"
    if isinstance(message, AIMessage):
        return "assistant"
    return "user"


def _tool_calls_of(message):
    calls = list(getattr(message, "tool_calls", None) or [])
    wire = []
    for call in calls:
        if isinstance(call, dict):
            name = call.get("name", "")
            args = call.get("args") or call.get("input") or {}
            call_id = call.get("id", "")
        else:
            name = getattr(call, "name", "")
            args = getattr(call, "args", None) or getattr(call, "input", None) or {}
            call_id = getattr(call, "id", "")
        wire.append((call_id or name, name, args if isinstance(args, dict) else {}))
    return wire


class _StdReply(object):
    """LangChain-shaped reply for the stdlib wire paths."""

    def __init__(self, content, tool_calls):
        self.content = content
        self.tool_calls = list(tool_calls or [])


class StdlibChat(object):
    """Provider-neutral stdlib client (spec 5.4 / F).

    openai style: POST {base}/chat/completions with OpenAI-shaped messages
    and function tools. anthropic style: POST {base minus /v1}/v1/messages
    with system separated out and tool_result blocks.
    """

    def __init__(self, protocol):
        self.protocol = protocol
        self.tools = []

    def bind_tools(self, schemas):
        self.tools = list(schemas or [])
        return self

    def invoke(self, messages):
        if self.protocol == "anthropic":
            return self._invoke_anthropic(messages)
        return self._invoke_openai(messages)

    def _openai_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": schema["function"]["name"],
                    "description": schema["function"].get("description", ""),
                    "parameters": schema["function"].get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                },
            }
            for schema in self.tools
            if isinstance(schema, dict) and isinstance(schema.get("function"), dict)
        ]

    def _anthropic_tools(self):
        return [
            {
                "name": schema["function"]["name"],
                "description": schema["function"].get("description", ""),
                "input_schema": schema["function"].get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
            for schema in self.tools
            if isinstance(schema, dict) and isinstance(schema.get("function"), dict)
        ]

    def _invoke_openai(self, messages):
        body = {
            "model": MODEL,
            "temperature": STD_TEMPERATURE,
            "messages": self._wire_openai(messages),
        }
        tools = self._openai_tools()
        if tools:
            body["tools"] = tools
        status, raw = _post_json(
            BASE_URL.rstrip("/") + "/chat/completions", body, _wire_headers()
        )
        if status != 200:
            raise RuntimeError("upstream HTTP %s: %s" % (status, raw[:300]))
        try:
            data = json.loads(raw)
        except ValueError:
            raise RuntimeError("upstream returned invalid json")
        choices = data.get("choices") or []
        message = (choices[0] if choices else {}).get("message") or {}
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                args = json.loads(function.get("arguments") or "{}")
            except ValueError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            calls.append(
                {"name": function.get("name", ""), "args": args, "id": call.get("id", "")}
            )
        return _StdReply(safe_text(message.get("content") or ""), calls)

    def _wire_openai(self, messages):
        wire = []
        for message in messages:
            role = _message_role(message)
            content = safe_text(getattr(message, "content", "") or "")
            tool_id = getattr(message, "tool_call_id", "") or ""
            if tool_id:
                wire.append(
                    {"role": "tool", "tool_call_id": tool_id, "content": content}
                )
                continue
            calls = _tool_calls_of(message)
            if calls:
                wire.append(
                    {
                        "role": "assistant",
                        "content": content or None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(args),
                                },
                            }
                            for call_id, name, args in calls
                        ],
                    }
                )
                continue
            wire.append(
                {
                    "role": role if role in ("system", "user", "assistant") else "user",
                    "content": content,
                }
            )
        return wire

    def _invoke_anthropic(self, messages):
        wire, system = self._wire_anthropic(messages)
        body = {
            "model": MODEL,
            "max_tokens": STD_MAX_TOKENS,
            "temperature": STD_TEMPERATURE,
            "messages": wire,
        }
        if system:
            body["system"] = system
        tools = self._anthropic_tools()
        if tools:
            body["tools"] = tools
        status, raw = _post_json(
            anthropic_base_url().rstrip("/") + "/v1/messages", body, _wire_headers()
        )
        if status != 200:
            raise RuntimeError("upstream HTTP %s: %s" % (status, raw[:300]))
        try:
            data = json.loads(raw)
        except ValueError:
            raise RuntimeError("upstream returned invalid json")
        text = []
        calls = []
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                text.append(safe_text(block.get("text") or ""))
            elif kind == "tool_use":
                args = block.get("input")
                calls.append(
                    {
                        "name": block.get("name", ""),
                        "args": args if isinstance(args, dict) else {},
                        "id": block.get("id", ""),
                    }
                )
        return _StdReply("\n".join(part for part in text if part), calls)

    def _wire_anthropic(self, messages):
        wire = []
        system = ""
        for message in messages:
            role = _message_role(message)
            content = safe_text(getattr(message, "content", "") or "")
            tool_id = getattr(message, "tool_call_id", "") or ""
            if role == "system":
                system = content if not system else system + "\n\n" + content
                continue
            if tool_id:
                wire.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": content,
                            }
                        ],
                    }
                )
                continue
            calls = _tool_calls_of(message)
            if calls:
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for call_id, name, args in calls:
                    blocks.append(
                        {"type": "tool_use", "id": call_id, "name": name, "input": args}
                    )
                wire.append({"role": "assistant", "content": blocks})
                continue
            wire.append(
                {
                    "role": role if role in ("user", "assistant") else "user",
                    "content": content,
                }
            )
        return wire, system


def anthropic_base_url():
    base = BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        return base[:-3]
    return base


def probe_protocol():
    """One-token, six-second probe; 404/405 selects the Anthropic client."""
    if not INJECTED:
        return None
    body = {
        "model": MODEL,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        status, _ = _post_json(
            BASE_URL.rstrip("/") + "/chat/completions", body, _wire_headers(), timeout=6
        )
    except RuntimeError:
        return None
    return status


def detect_protocol():
    """Negotiated protocol, cached for the process lifetime (spec 5.4)."""
    global _PROTO
    with _PROTO_LOCK:
        if _PROTO is None:
            status = probe_protocol()
            if status in (404, 405):
                _PROTO = "anthropic"
            elif status is None:
                _PROTO = "unset"
            else:
                _PROTO = "openai"
        return _PROTO


def flip_protocol():
    """A 404/405 during chat flips the cached decision and rebuilds the client."""
    global _PROTO, _LLM
    with _PROTO_LOCK:
        _PROTO = "anthropic" if _PROTO != "anthropic" else "openai"
    with LLM_LOCK:
        _LLM = None


def _is_protocol_mismatch(exc):
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status in (404, 405):
        return True
    text = safe_text(str(exc)).lower()
    return "http 404" in text or "http 405" in text or "method not allowed" in text


def build_llm():
    protocol = detect_protocol()
    if HAS_LANGCHAIN and protocol != "unset":
        if protocol == "anthropic":
            try:
                from langchain_anthropic import ChatAnthropic

                return ChatAnthropic(
                    base_url=anthropic_base_url(),
                    api_key=API_KEY,
                    model=MODEL,
                    timeout=STD_TIMEOUT,
                    max_retries=STD_RETRIES,
                    temperature=STD_TEMPERATURE,
                    max_tokens=STD_MAX_TOKENS,
                )
            except Exception:
                # no Anthropic-capable client installed: the stdlib wire
                # path answers the Anthropic branch for real (spec F)
                return StdlibChat("anthropic")
        return ChatOpenAI(
            model=MODEL,
            api_key=API_KEY,
            base_url=BASE_URL,
            timeout=STD_TIMEOUT,
            max_retries=STD_RETRIES,
            temperature=STD_TEMPERATURE,
        )
    return StdlibChat(protocol if protocol != "unset" else "openai")


def get_llm():
    global _LLM
    with LLM_LOCK:
        if _LLM is None:
            if not INJECTED:
                raise RuntimeError(
                    "MonkeyCode LLM was not injected: MCAI_LLM_BASE_URL and "
                    "MCAI_LLM_API_KEY are required (run doctor to see the checklist)"
                )
            os.environ.setdefault("TIKTOKEN_CACHE_DIR", "/tmp")
            _LLM = build_llm()
        return _LLM


# -------------------------------------------------------------------- tools


def _truncate(text):
    text = safe_text(text)
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + "\n...[truncated %d chars]" % (len(text) - MAX_OUTPUT)


def _workspace_path(rel, root=None):
    if root is None:
        root = WORKSPACE
    target = os.path.realpath(os.path.join(root, rel or "."))
    base = os.path.realpath(root)
    if target != base and not target.startswith(base + os.sep):
        raise ValueError("path escapes the workspace: %s" % rel)
    return target


def run_command(cmd):
    """Run a shell command inside the sandbox. cwd is the workspace."""
    if not isinstance(cmd, str) or not cmd.strip():
        return "error: empty command"
    try:
        os.makedirs(WORKSPACE, exist_ok=True)
        proc = subprocess.run(
            ["/bin/sh", "-c", cmd],
            cwd=WORKSPACE,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=SHELL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "error: command timed out after %ds" % SHELL_TIMEOUT
    except OSError as exc:
        return "error: %s" % exc
    parts = []
    if proc.stdout:
        parts.append(proc.stdout)
    if proc.stderr:
        parts.append("[stderr] " + proc.stderr)
    parts.append("[exit %d]" % proc.returncode)
    return _truncate("\n".join(parts))


def write_file(path, content):
    """Write text content to a file inside the workspace. Creates parents."""
    try:
        target = _workspace_path(path)
    except ValueError as exc:
        return "error: %s" % exc
    try:
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(safe_text(content))
    except OSError as exc:
        return "error: %s" % exc
    return "wrote %d chars to %s" % (
        len(safe_text(content)),
        os.path.relpath(target, WORKSPACE),
    )


def read_file(path):
    """Read a text file inside the workspace and return its contents."""
    try:
        target = _workspace_path(path)
    except ValueError as exc:
        return "error: %s" % exc
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            return _truncate(handle.read())
    except OSError as exc:
        return "error: %s" % exc


def list_files(path="."):
    """List files and directories inside the workspace."""
    try:
        target = _workspace_path(path)
    except ValueError as exc:
        return "error: %s" % exc
    if not os.path.isdir(target):
        return "error: not a directory: %s" % path
    lines = []
    for entry in sorted(os.listdir(target)):
        full = os.path.join(target, entry)
        lines.append(entry + ("/" if os.path.isdir(full) else ""))
    return "\n".join(lines) or "(empty)"


def install_packages(manager, packages):
    """Install development packages. manager is one of apt, pip, npm."""
    if not INSTALL_ENABLED:
        return "error: package installation is disabled in the strict profile"
    if manager not in ("apt", "pip", "npm"):
        return "error: manager must be one of apt, pip, npm"
    names = " ".join((packages or "").split())
    if not names:
        return "error: empty package list"
    if manager == "apt":
        command = (
            "apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
            "--no-install-recommends " + names
        )
    elif manager == "pip":
        command = "python -m pip install --no-cache-dir " + names
    else:
        command = "npm install -g " + names
    try:
        proc = subprocess.run(
            ["/bin/sh", "-c", command],
            cwd=WORKSPACE,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=INSTALL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "error: install timed out after %ds" % INSTALL_TIMEOUT
    except OSError as exc:
        return "error: %s" % exc
    parts = []
    if proc.stdout:
        parts.append(proc.stdout[-4000:])
    if proc.stderr:
        parts.append("[stderr] " + proc.stderr[-4000:])
    parts.append("[exit %d]" % proc.returncode)
    return _truncate("\n".join(parts))


def browse(url, screenshot=False):
    """Open a URL with headless Chromium; return title, url and visible text."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return "error: playwright is unavailable: %s" % exc
    if not isinstance(url, str) or not url.strip():
        return "error: url is empty"
    url = url.strip()
    if not re.match(r"^https?://", url):
        return "error: url must be http(s)"
    try:
        os.makedirs(WORKSPACE, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                title = page.title()
                text = page.inner_text("body", timeout=15000)
                if len(text) > 8000:
                    text = text[:8000] + "\n...[truncated]"
                result = "title: %s\nurl: %s\ntext:\n%s" % (title, page.url, text)
                if screenshot:
                    name = "shot-%d.png" % int(time.time() * 1000)
                    out = _workspace_path(name)
                    page.screenshot(path=out, full_page=True)
                    result += "\nscreenshot: %s" % os.path.relpath(out, WORKSPACE)
                return result
            finally:
                browser.close()
    except Exception as exc:
        return "error: browse failed: %s" % exc


def install_android_sdk(packages=""):
    """Install Android SDK packages into ANDROID_HOME. Idempotent."""
    if not INSTALL_ENABLED:
        return "error: package installation is disabled in the strict profile"
    if not os.path.isfile(INSTALLER_SCRIPT):
        return "error: installer not found: %s" % INSTALLER_SCRIPT
    env = dict(os.environ)
    names = " ".join((packages or "").split())
    if names:
        env["ANDROID_PACKAGES"] = names
    env.setdefault("ANDROID_HOME", os.environ.get("ANDROID_HOME", "").strip() or "/opt/android-sdk")
    try:
        proc = subprocess.run(
            ["/bin/sh", INSTALLER_SCRIPT],
            cwd=WORKSPACE,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=INSTALL_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "error: android sdk install timed out after %ds" % INSTALL_TIMEOUT
    except OSError as exc:
        return "error: %s" % exc
    parts = []
    if proc.stdout:
        parts.append(proc.stdout[-4000:])
    if proc.stderr:
        parts.append("[stderr] " + proc.stderr[-4000:])
    parts.append("[exit %d]" % proc.returncode)
    return _truncate("\n".join(parts))


TOOL_FUNCS = {
    "run_command": run_command,
    "write_file": write_file,
    "read_file": read_file,
    "list_files": list_files,
    "browse": browse,
    "install_packages": install_packages,
    "install_android_sdk": install_android_sdk,
}

BASE_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command inside the sandbox. cwd is the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string"}},
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text to a file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browse",
            "description": "Open a URL with headless Chromium and return title, url and visible text. screenshot=true also writes a full-page PNG into the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "screenshot": {"type": "boolean"},
                },
                "required": ["url"],
            },
        },
    },
]

INSTALL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "install_packages",
            "description": "Install packages with apt, pip or npm (tooled profile only).",
            "parameters": {
                "type": "object",
                "properties": {
                    "manager": {"type": "string", "enum": ["apt", "pip", "npm"]},
                    "packages": {"type": "string"},
                },
                "required": ["manager", "packages"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_android_sdk",
            "description": "Install Android SDK packages into ANDROID_HOME via the image installer. Idempotent; pass e.g. 'platform-tools platforms;android-35 build-tools;35.0.0'.",
            "parameters": {
                "type": "object",
                "properties": {"packages": {"type": "string"}},
            },
        },
    },
]


def select_tool_schemas(install=None):
    if install is None:
        install = INSTALL_ENABLED
    return BASE_SCHEMAS + (INSTALL_SCHEMAS if install else [])


TOOL_SCHEMAS = select_tool_schemas()


def tool_names(install=None):
    return [schema["function"]["name"] for schema in select_tool_schemas(install)]


def call_tool(name, args):
    func = TOOL_FUNCS.get(name)
    if func is None:
        return "error: unknown tool: %s" % name
    if not isinstance(args, dict):
        return "error: tool arguments must be an object"
    try:
        return safe_text(func(**args))
    except TypeError as exc:
        return "error: bad arguments for %s: %s" % (name, exc)
    except Exception as exc:  # a tool failure is data, not a crash
        return "error: %s failed: %s" % (name, exc)


# ------------------------------------------------------------------- memory


class Memory(object):
    """Bounded turn history with archive and repeating compression.

    HISTORY holds live message objects. The newest MAX_TURNS pairs are kept;
    older entries are archived (reason "evicted") and never sent to the model.
    When live plus system tokens exceed CONTEXT_TOKENS the oldest eligible
    COMPRESS_BATCH MESSAGES fold (in whole pairs, newest pair never folded)
    into the single [compressed history] entry; passes repeat until the
    estimate is within budget or no pair is eligible (reason "compressed").
    The marker is an assistant-role message at the front of the live history,
    it is never evicted, and a second pass folds into it in place.
    CONTEXT_TOKENS=0 disables compression.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.history = []
        self.archive = []
        self.compressed_turns = 0

    def _pair_count(self):
        return sum(1 for message in self.history if not _is_marker_message(message)) // 2

    def live_turns(self):
        with self.lock:
            return self._pair_count()

    def tokens(self):
        with self.lock:
            body = sum(
                count_tokens(getattr(message, "content", "") or "")
                for message in self.history
            )
        return body + count_tokens(system_prompt())

    def append(self, message):
        with self.lock:
            self.history.append(message)
            self._trim_locked()

    def extend(self, messages):
        with self.lock:
            self.history.extend(messages)
            self._trim_locked()

    def _trim_locked(self):
        while self._pair_count() > MAX_TURNS:
            removed = 0
            index = 0
            while index < len(self.history) and removed < 2:
                if _is_marker_message(self.history[index]):
                    index += 1
                    continue
                evicted = self.history.pop(index)
                self._archive_locked(evicted, "evicted")
                removed += 1
            if removed < 2:
                break

    def _archive_locked(self, message, reason):
        self.archive.append(
            {
                "role": type(message).__name__,
                "content": safe_text(getattr(message, "content", "") or ""),
                "reason": reason,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        if ARCHIVE_MAX and len(self.archive) > ARCHIVE_MAX:
            del self.archive[: len(self.archive) - ARCHIVE_MAX]

    def snapshot(self):
        """Live history as plain dicts (role = message type name) plus archive."""
        with self.lock:
            live = [
                {
                    "role": type(message).__name__,
                    "content": safe_text(getattr(message, "content", "") or ""),
                }
                for message in self.history
            ]
            archive = list(self.archive)
            marker = ""
            for entry in live:
                if str(entry.get("content", "")).startswith(SUMMARY_MARKER):
                    marker = str(entry["content"])
                    break
            return live, archive, self.compressed_turns, marker

    def window(self):
        """Messages to send: system prompt, then live history.

        The marker enters the wire as an assistant message (assistant role on
        every provider path).
        """
        with self.lock:
            live = list(self.history)
        messages = [SystemMessage(content=system_prompt())]
        for message in live:
            if _is_marker_message(message):
                messages.append(
                    AIMessage(content=safe_text(getattr(message, "content", "") or ""))
                )
                continue
            messages.append(message)
        return messages

    def maybe_compress(self, llm):
        if CONTEXT_TOKENS <= 0:
            return False
        folded_any = False
        while True:
            with self.lock:
                body = [
                    message for message in self.history
                    if not _is_marker_message(message)
                ]
                live_tokens = sum(
                    count_tokens(getattr(message, "content", "") or "")
                    for message in body
                )
                marker_tokens = sum(
                    count_tokens(getattr(message, "content", "") or "")
                    for message in self.history
                    if _is_marker_message(message)
                )
                over = (
                    live_tokens
                    + marker_tokens
                    + count_tokens(system_prompt())
                    > CONTEXT_TOKENS
                )
                if not over:
                    return folded_any
                eligible = max(0, len(body) - 2)  # newest pair stays live
                fold = min(COMPRESS_BATCH, eligible)
                fold -= fold % 2  # whole pairs only
                if fold <= 0:
                    return folded_any
                batch = body[:fold]
                self._drop_locked(batch)
            text = self._summarize(batch, llm)
            with self.lock:
                for message in batch:
                    self._archive_locked(message, "compressed")
                self.compressed_turns += len(batch)
                self._merge_marker_locked(text)
            folded_any = True

    def _drop_locked(self, batch):
        """Remove exactly these message objects, keeping the marker in place."""
        doomed = [id(message) for message in batch]
        self.history = [
            message for message in self.history if id(message) not in doomed
        ]

    def _merge_marker_locked(self, text):
        merged = "%s %s" % (SUMMARY_MARKER, text)
        for index, message in enumerate(self.history):
            if _is_marker_message(message):
                previous = safe_text(getattr(message, "content", "") or "")
                previous = previous[len(SUMMARY_MARKER):].strip()
                combined = (previous + "\n" + text).strip() if previous else text
                merged = ("%s %s" % (SUMMARY_MARKER, combined))[:SUMMARY_MAX_CHARS]
                self.history[index] = SummaryMessage(merged)
                return
        self.history.insert(0, SummaryMessage(merged))

    def _summarize(self, batch, llm):
        joined = []
        total = 0
        for message in batch:
            content = safe_text(getattr(message, "content", "") or "")
            if total + len(content) > SUMMARY_INPUT_CHARS:
                break
            total += len(content)
            joined.append("%s: %s" % (type(message).__name__, content))
        prompt = (
            "Compress this conversation into one factual block: keep decisions, "
            "paths, commands, versions, errors and open questions. Max %d chars."
            % SUMMARY_MAX_CHARS
        )
        try:
            response = llm.invoke(
                [SystemMessage(content=prompt), HumanMessage(content="\n".join(joined))]
            )
            text = content_to_text(getattr(response, "content", response))
        except Exception:
            head = "\n".join(joined)[:SUMMARY_MAX_CHARS]
            text = "[summary-lossy] " + head
        return text[:SUMMARY_MAX_CHARS]

    def stats(self):
        with self.lock:
            return {
                "history_turns": self._pair_count(),
                "history_tokens": self.tokens(),
                "compressed_turns": self.compressed_turns,
                "archived_turns": len(self.archive),
                "context_tokens": CONTEXT_TOKENS,
                "context_hard_limit": CONTEXT_HARD_LIMIT,
                "max_turns": MAX_TURNS,
                "compress_batch": COMPRESS_BATCH,
                "max_message_chars": MAX_MESSAGE_CHARS,
            }


MEMORY = Memory()


# ------------------------------------------------------------- file exchange


def _safe_members(members, root):
    base = os.path.realpath(root)
    for member in members:
        name = member.name
        if name.startswith("/") or os.path.isabs(name):
            raise ValueError("absolute path in archive: %s" % name)
        target = os.path.realpath(os.path.join(base, name))
        if target != base and not target.startswith(base + os.sep):
            raise ValueError("path escapes the workspace: %s" % name)
        if member.issym() or member.islnk():
            raise ValueError("link member rejected: %s" % name)
        if member.ischr() or member.isblk() or member.isdev():
            raise ValueError("device member rejected: %s" % name)
        if member.isfifo():
            raise ValueError("fifo member rejected: %s" % name)
        yield member


def _extract_member(tar, member, root):
    """Extract one pre-screened member, preferring filter="data"."""
    try:
        tar.extract(member, path=root, filter="data")
        return
    except TypeError:
        pass  # runtime without the data filter: equivalent manual pass
    name = member.name
    target = os.path.realpath(os.path.join(root, name))
    base = os.path.realpath(root)
    if target != base and not target.startswith(base + os.sep):
        raise ValueError("path escapes the workspace: %s" % name)
    if member.isdir():
        os.makedirs(target, exist_ok=True)
        return
    if not member.isfile():
        raise ValueError("member rejected: %s" % name)
    os.makedirs(os.path.dirname(target) or root, exist_ok=True)
    source = tar.extractfile(member)
    if source is None:
        raise ValueError("member has no data: %s" % name)
    with open(target, "wb") as handle:
        while True:
            chunk = source.read(65536)
            if not chunk:
                break
            handle.write(chunk)


def build_workspace_tar(root=None):
    """tar.gz bytes of the workspace; .isolis and caches are skipped."""
    if root is None:
        root = WORKSPACE
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                if name in (".cache", "__pycache__", ".pytest_cache"):
                    continue
                tar.add(os.path.join(root, name), arcname=name, recursive=True)
    blob = buffer.getvalue()
    if len(blob) > EXPORT_MAX_BYTES:
        raise ValueError("workspace export exceeds %d bytes" % EXPORT_MAX_BYTES)
    return blob


def extract_workspace_tar(root, blob):
    """Extract a tar.gz blob into the workspace. Returns the member count."""
    if len(blob) > IMPORT_MAX_BYTES:
        raise ValueError("archive exceeds %d bytes" % IMPORT_MAX_BYTES)
    os.makedirs(root, exist_ok=True)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in _safe_members(tar.getmembers(), root):
            _extract_member(tar, member, root)
            count += 1
    return count


# ------------------------------------------------------------------ chat run


def run_chat(message):
    """One chat turn: tool loop with bounded steps and memory bookkeeping."""
    text, trimmed = truncate_message(message)
    if not text.strip():
        raise ValueError("message is empty")
    llm = get_llm()
    bound = llm.bind_tools(select_tool_schemas())
    tools_used = []
    messages = MEMORY.window()
    messages.append(HumanMessage(content=text))
    reply = ""
    for _step in range(MAX_TOOL_STEPS):
        try:
            response = bound.invoke(messages)
        except Exception as exc:
            if not _is_protocol_mismatch(exc):
                raise
            flip_protocol()
            bound = get_llm().bind_tools(select_tool_schemas())
            response = bound.invoke(messages)
        calls = list(getattr(response, "tool_calls", None) or [])
        if not calls:
            reply = content_to_text(getattr(response, "content", response))
            break
        messages.append(response)
        for call in calls:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", "")
            args = call.get("args") if isinstance(call, dict) else getattr(call, "args", {})
            call_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", "")
            if not isinstance(args, dict):
                args = {}
            result = call_tool(name, args)
            tools_used.append({"tool": name, "args": args, "chars": len(result)})
            messages.append(ToolMessage(content=result, tool_call_id=call_id or name))
    else:
        reply = "stopped after %d tool steps; here is what I have so far." % MAX_TOOL_STEPS
    if not reply:
        reply = safe_text(str(response))
    MEMORY.append(HumanMessage(content=text))
    MEMORY.append(AIMessage(content=reply))
    MEMORY.maybe_compress(bound)
    stats = MEMORY.stats()
    stats["trimmed_input"] = trimmed
    return reply, tools_used, stats


# -------------------------------------------------------------------- HTTP


class IsolisHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "isolis-agent/1.1"

    def log_message(self, fmt, *args):
        return

    def send_json(self, status, payload):
        body = json.dumps(json_sanitize(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_blob(self, blob, filename):
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(blob)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def path_only(self):
        return urlparse(self.path).path

    def info_payload(self):
        base = os.environ.get("MCAI_LLM_BASE_URL", "").strip()
        key = os.environ.get("MCAI_LLM_API_KEY", "").strip()
        payload = {
            "ok": True,
            "framework": "langchain" if HAS_LANGCHAIN else "stdlib",
            "source": AGENT_SOURCE,
            "profile": AGENT_PROFILE,
            "install": INSTALL_ENABLED,
            "android_home": os.environ.get("ANDROID_HOME", "").strip(),
            "model": MODEL,
            "llm_host": urlparse(base).netloc if base else "",
            "via": "mc-direct",
            "injected": bool(base and key),
            "protocol": detect_protocol() if (base and key) else "unset",
            "tools": tool_names(),
            "workspace": WORKSPACE,
            "uptime_s": int(time.time() - STARTED_AT),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(STARTED_AT)),
        }
        payload.update(common_fields())
        payload.update(MEMORY.stats())
        return payload

    def do_GET(self):
        path = self.path_only()
        if path == "/health":
            self.send_json(200, {"ok": True})
            return
        if path == "/info":
            self.send_json(200, self.info_payload())
            return
        if path == "/history":
            live, archive, compressed, summary = MEMORY.snapshot()
            payload = {
                "ok": True,
                "context_tokens": CONTEXT_TOKENS,
                "history_turns": MEMORY.live_turns(),
                "compressed_turns": compressed,
                "summary": summary,
                "history": live,
                "archive": archive,
            }
            payload.update(common_fields())
            self.send_json(200, payload)
            return
        if path == "/files":
            try:
                blob = build_workspace_tar()
            except ValueError as exc:
                self.send_json(500, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                self.send_json(500, {"ok": False, "error": str(exc), "trace": traceback.format_exc()})
                return
            self.send_blob(blob, "%s-work.tar.gz" % AGENT_NAME)
            return
        self.send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.path_only()
        if path not in ("/chat", "/files"):
            self.send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        if path == "/files":
            try:
                count = extract_workspace_tar(WORKSPACE, raw)
            except ValueError as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc), "trace": traceback.format_exc()})
                return
            payload = {"ok": True, "files": count}
            payload.update(common_fields())
            self.send_json(200, payload)
            return
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            self.send_json(400, {"ok": False, "error": "invalid json"})
            return
        if not isinstance(data, dict):
            self.send_json(400, {"ok": False, "error": "message is empty"})
            return
        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            self.send_json(400, {"ok": False, "error": "message is empty"})
            return
        try:
            reply, tools_used, stats = run_chat(message)
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc), "trace": traceback.format_exc()})
            return
        payload = {"ok": True, "reply": reply, "tools_used": tools_used, "memory": stats}
        payload.update(common_fields())
        self.send_json(200, payload)


def main():
    bind = os.environ.get("ISOLIS_BIND", "0.0.0.0").strip() or "0.0.0.0"
    port = _int_env("ISOLIS_PORT", 8080)
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", "/tmp")
    try:
        os.makedirs(WORKSPACE, exist_ok=True)
    except OSError:
        pass
    server = ThreadingHTTPServer((bind, port), IsolisHandler)
    server.daemon_threads = True
    sys.stderr.write(
        "isolis-agent: %s profile=%s install=%s workspace=%s injected=%s\n"
        % (AGENT_NAME, AGENT_PROFILE, INSTALL_ENABLED, WORKSPACE, INJECTED)
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
