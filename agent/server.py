import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import urllib.error
import urllib.request

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

AGENT_FRAMEWORK = os.environ.get("ISOLIS_FRAMEWORK", "langchain").strip() or "langchain"
AGENT_SOURCE = os.environ.get("ISOLIS_SOURCE", "https://github.com/langchain-ai/langchain").strip()
AGENT_PROFILE = os.environ.get("ISOLIS_PROFILE", "strict").strip() or "strict"
INSTALL_ENABLED = os.environ.get("ISOLIS_INSTALL", "0").strip() in ("1", "true", "yes")
ANDROID_HOME = os.environ.get("ANDROID_HOME", "/opt/android-sdk").strip()
WORKSPACE_DEFAULT = os.environ.get("ISOLIS_WORKSPACE", "/work")


def default_system_prompt():
    lines = [
        "You are Isolis, a coding agent running inside an isolated Docker "
        "container. You cannot see the host filesystem, other agent containers, "
        "or the Docker socket.",
        "Your writable workspace is %s. Keep project files there." % WORKSPACE_DEFAULT,
        "Match the user's language. Be concise and factual. Verify claims by "
        "running commands before reporting success.",
    ]
    if INSTALL_ENABLED:
        lines.append(
            "This container grants the tooled profile: you may install "
            "development packages with install_packages (apt, pip, npm) and set "
            "up the Android SDK with install_android_sdk under ANDROID_HOME=%s."
            % ANDROID_HOME
        )
    else:
        lines.append(
            "This container grants the strict profile: package installation is "
            "disabled. Work with what the image already provides."
        )
    return " ".join(lines)


SYSTEM_PROMPT = os.environ.get("ISOLIS_SYSTEM_PROMPT", "").strip() or default_system_prompt()

MAX_TOOL_STEPS = 8
WORKSPACE = os.environ.get("ISOLIS_WORKSPACE", "/work")
SHELL_TIMEOUT = 120
INSTALL_TIMEOUT = 1800
MAX_OUTPUT = 20000


def _int_env(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


CONTEXT_TOKENS = _int_env("ISOLIS_CONTEXT_TOKENS", 900000)
MAX_MESSAGE_CHARS = _int_env("ISOLIS_MAX_MESSAGE_CHARS", 900000)
MAX_TURNS = _int_env("ISOLIS_MAX_TURNS", 400)
COMPRESS_BATCH = _int_env("ISOLIS_COMPRESS_BATCH", 40)
SUMMARY_MAX_CHARS = _int_env("ISOLIS_SUMMARY_MAX_CHARS", 8000)
SUMMARY_INPUT_CHARS = _int_env("ISOLIS_SUMMARY_INPUT_CHARS", 60000)
ARCHIVE_MAX = _int_env("ISOLIS_ARCHIVE_MAX", 4000)
CONTEXT_HARD_LIMIT = _int_env("ISOLIS_CONTEXT_HARD_LIMIT", 1000000)
SUMMARY_MARKER = "[compressed history]"

if CONTEXT_TOKENS > CONTEXT_HARD_LIMIT:
    sys.stderr.write(
        "isolis-agent: ISOLIS_CONTEXT_TOKENS %d exceeds the upstream limit %d; "
        "clamping\n" % (CONTEXT_TOKENS, CONTEXT_HARD_LIMIT)
    )
    CONTEXT_TOKENS = CONTEXT_HARD_LIMIT

BASE_URL = os.environ.get("MCAI_LLM_BASE_URL", "").strip()
API_KEY = os.environ.get("MCAI_LLM_API_KEY", "").strip()
MODEL = os.environ.get("MCAI_LLM_MODEL", "").strip()
FALLBACK_MODEL = (
    os.environ.get("ISOLIS_FALLBACK_MODEL", "").strip()
    or os.environ.get("ISOLIS_DEFAULT_MODEL", "").strip()
)
INJECTED = bool(BASE_URL and API_KEY)
LLM_HOST = urlparse(BASE_URL).netloc if BASE_URL else ""

HISTORY = []
HISTORY_LOCK = threading.Lock()
LLM = None
LLM_LOCK = threading.Lock()
PROTOCOL = None
PROTOCOL_LOCK = threading.Lock()
ARCHIVE = []
ARCHIVE_LOCK = threading.Lock()
COMPRESSED_TURNS = 0


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


def history_tokens(history):
    return sum(count_tokens(turn.get("content", "")) for turn in history)


def truncate_message(text):
    if MAX_MESSAGE_CHARS <= 0 or len(text) <= MAX_MESSAGE_CHARS:
        return text, False
    keep = MAX_MESSAGE_CHARS - 120
    if keep < 1000:
        keep = MAX_MESSAGE_CHARS
    head = keep // 2
    tail = keep - head
    note = "\n...[truncated %d chars from the middle]...\n" % (
        len(text) - keep
    )
    return text[:head] + note + text[-tail:], True


def common_fields():
    return {
        "agent": os.environ.get("ISOLIS_AGENT_NAME", ""),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
    }


def content_to_text(content):
    if isinstance(content, str):
        return safe_text(content)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return safe_text(text)
        return json.dumps(json_sanitize(content), ensure_ascii=False)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(safe_text(block))
            elif isinstance(block, dict):
                block_type = block.get("type")
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(safe_text(text))
                elif block_type in ("thinking", "redacted_thinking", "signature"):
                    continue
                else:
                    parts.append(json.dumps(json_sanitize(block), ensure_ascii=False))
            else:
                parts.append(safe_text(str(block)))
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return safe_text(str(content))


def probe(url, body, timeout=6):
    data = json.dumps(json_sanitize(body), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Authorization", "Bearer " + API_KEY)
    request.add_header("Content-Type", "application/json")
    request.add_header("anthropic-version", "2023-06-01")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(400).decode("utf-8", "replace")
            return response.status, raw
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(300).decode("utf-8", "replace")
        except Exception:
            raw = ""
        return exc.code, raw
    except Exception:
        return None, ""


def detect_protocol():
    global PROTOCOL
    with PROTOCOL_LOCK:
        if PROTOCOL is not None:
            return PROTOCOL
        base = BASE_URL.rstrip("/")
        answer = {
            "model": MODEL,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
        status, _ = probe(base + "/chat/completions", answer)
        PROTOCOL = "anthropic" if status in (404, 405) else "openai"
        return PROTOCOL


def anthropic_base_url():
    base = BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        return base[:-3]
    return base


def build_llm():
    protocol = detect_protocol()
    if protocol == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            base_url=anthropic_base_url(),
            api_key=API_KEY,
            model=MODEL,
            timeout=60,
            max_retries=1,
            temperature=0.4,
            max_tokens=4096,
        )
    return ChatOpenAI(
        base_url=BASE_URL,
        api_key=API_KEY,
        model=MODEL,
        timeout=60,
        max_retries=1,
        temperature=0.4,
    )


def get_llm():
    global LLM
    with LLM_LOCK:
        if LLM is None:
            LLM = build_llm()
        return LLM


def error_status(err):
    status = getattr(err, "status_code", None)
    if status is not None:
        return status
    response = getattr(err, "response", None)
    return getattr(response, "status_code", None)


def describe_error(err):
    text = safe_text(str(err)).replace("\n", " ").strip() or type(err).__name__
    status = error_status(err)
    if status == 403 or text.lower() == "forbidden":
        return (
            "upstream HTTP 403 Forbidden. The gateway rejected this request "
            "(check model access)."
        )
    if status == 401:
        return "upstream HTTP 401. Injected credentials were rejected."
    if len(text) > 400:
        text = text[:400] + "..."
    return text


def flip_protocol():
    global PROTOCOL, LLM
    with PROTOCOL_LOCK:
        PROTOCOL = "anthropic" if PROTOCOL != "anthropic" else "openai"
    with LLM_LOCK:
        LLM = None


def use_model(model):
    global MODEL, LLM
    MODEL = model
    with LLM_LOCK:
        LLM = None


def invoke_chat(messages):
    bound = get_bound_llm()
    if bound is None:
        response = get_llm().invoke(messages)
        return content_to_text(getattr(response, "content", response)), []
    return run_tool_loop(messages, bound)


def call_with_protocol_retry(messages):
    try:
        return invoke_chat(messages)
    except Exception as err:
        status = error_status(err)
        text = safe_text(str(err)).lower()
        mismatch = status in (404, 405) or "method not allowed" in text
        if mismatch:
            flip_protocol()
            try:
                return invoke_chat(messages)
            except Exception as err2:
                err = err2
                status = error_status(err2)
                text = safe_text(str(err2)).lower()
        alt = FALLBACK_MODEL
        if (status == 403 or "forbidden" in text) and alt and alt != MODEL:
            use_model(alt)
            try:
                return invoke_chat(messages)
            except Exception as err3:
                raise RuntimeError(describe_error(err3)) from err3
        raise RuntimeError(describe_error(err)) from err


def _truncate(text):
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + "\n...[truncated %d chars]" % (len(text) - MAX_OUTPUT)


def _workspace_path(rel):
    target = os.path.realpath(os.path.join(WORKSPACE, rel or "."))
    root = os.path.realpath(WORKSPACE)
    if target != root and not target.startswith(root + os.sep):
        raise ValueError("path escapes the workspace: %s" % rel)
    return target


@tool
def run_command(cmd: str) -> str:
    """Run a shell command inside the sandbox. The working directory is /work,
    a writable workspace. stdout and stderr are returned. Use for git, curl,
    python scripts, file inspection and other non-interactive automation."""
    if not isinstance(cmd, str) or not cmd.strip():
        return "error: empty command"
    try:
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


@tool
def write_file(path: str, content: str) -> str:
    """Write text content to a file inside /work. Creates parent directories.
    Relative paths resolve against /work."""
    try:
        target = _workspace_path(path)
    except ValueError as exc:
        return "error: %s" % exc
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(safe_text(content))
    except OSError as exc:
        return "error: %s" % exc
    return "wrote %d chars to %s" % (len(content), os.path.relpath(target, WORKSPACE))


@tool
def read_file(path: str) -> str:
    """Read a text file inside /work and return its contents."""
    try:
        target = _workspace_path(path)
    except ValueError as exc:
        return "error: %s" % exc
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            return _truncate(handle.read())
    except OSError as exc:
        return "error: %s" % exc


@tool
def list_files(path: str = ".") -> str:
    """List files and directories inside /work."""
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


@tool
def browse(url: str, screenshot: bool = False) -> str:
    """Open a URL with headless Chromium and return the page title and visible
    text. Set screenshot=true to also save a PNG into /work and return its
    path."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return "error: playwright is unavailable: %s" % exc
    if not isinstance(url, str) or not re.match(r"^https?://", url):
        return "error: url must start with http:// or https://"
    try:
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
                result = "title: %s\nurl: %s\ntext:\n%s" % (
                    title,
                    page.url,
                    _truncate(text),
                )
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


@tool
def install_packages(manager: str, packages: str) -> str:
    """Install development packages inside the container. manager is one of
    apt, pip, npm. packages is a space separated list. Requires the tooled
    profile. Use apt for system tools (openjdk, gcc, imagemagick), pip for
    Python libraries, npm for Node packages."""
    if not INSTALL_ENABLED:
        return "error: package installation is disabled in the strict profile"
    if not isinstance(manager, str) or manager not in ("apt", "pip", "npm"):
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


@tool
def install_android_sdk(packages: str = "") -> str:
    """Install the Android command-line tools and SDK packages into
    ANDROID_HOME (/opt/android-sdk). packages overrides the default set, which
    is platform-tools, platforms;android-34 and build-tools;34.0.0. Requires
    the tooled profile and Java, which the image already provides."""
    if not INSTALL_ENABLED:
        return "error: package installation is disabled in the strict profile"
    command = "/usr/local/bin/install-android-sdk"
    env = dict(os.environ)
    extra = " ".join((packages or "").split())
    if extra:
        env["ANDROID_PACKAGES"] = extra
    try:
        proc = subprocess.run(
            ["/bin/sh", "-c", command],
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


TOOLS = [run_command, write_file, read_file, list_files, browse]
if INSTALL_ENABLED:
    TOOLS += [install_packages, install_android_sdk]


def get_bound_llm():
    protocol = detect_protocol()
    if protocol != "anthropic":
        return None
    return get_llm().bind_tools(TOOLS)


def run_tool_loop(messages, bound):
    transcript = []
    current = list(messages)
    for _ in range(MAX_TOOL_STEPS):
        response = bound.invoke(current)
        calls = getattr(response, "tool_calls", None) or []
        text = content_to_text(getattr(response, "content", response))
        if not calls:
            return text, transcript
        current.append(response)
        for call in calls:
            name = call.get("name")
            args = call.get("args") or {}
            handler = {t.name: t for t in TOOLS}.get(name)
            if handler is None:
                output = "error: unknown tool %s" % name
            else:
                try:
                    output = handler.invoke(args)
                except Exception as exc:
                    output = "error: %s" % exc
            output = _truncate(safe_text(str(output)))
            transcript.append({"tool": name, "args": args, "output": output[:500]})
            current.append(ToolMessage(content=output, tool_call_id=call.get("id")))
    final = content_to_text(getattr(current[-1], "content", ""))
    return final or "stopped after %d tool steps" % MAX_TOOL_STEPS, transcript


def archive_turns(turns, reason):
    if not turns:
        return
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with ARCHIVE_LOCK:
        for turn in turns:
            ARCHIVE.append(
                {
                    "role": turn.get("role", ""),
                    "content": turn.get("content", ""),
                    "reason": reason,
                    "at": stamp,
                }
            )
        if len(ARCHIVE) > ARCHIVE_MAX:
            del ARCHIVE[: len(ARCHIVE) - ARCHIVE_MAX]


def render_for_summary(turns):
    lines = []
    budget = SUMMARY_INPUT_CHARS
    for turn in turns:
        role = "user" if turn.get("role") == "user" else "assistant"
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if len(content) > 2000:
            content = content[:2000] + "...[cut]"
        block = "%s: %s" % (role, content)
        if budget - len(block) < 0:
            lines.append("...[earlier turns omitted]...")
            break
        lines.append(block)
        budget -= len(block)
    return "\n\n".join(lines)


def summarize_turns(turns, prior=""):
    blocks = []
    if prior:
        blocks.append("earlier summary: " + prior)
    text = render_for_summary(turns)
    if text:
        blocks.append(text)
    body = "\n\n".join(blocks)
    if not body:
        return prior
    prompt = (
        "Compress the conversation excerpt below into a compact briefing for "
        "continuing the same task. Keep facts, file paths, commands, versions, "
        "decisions, errors and unresolved questions. Drop pleasantries and "
        "repetition. Write at most 200 words, plain text, no preamble.\n\n"
        + body
    )
    try:
        response = get_llm().invoke(
            [HumanMessage(content=prompt[: SUMMARY_INPUT_CHARS + 4000])]
        )
        summary = content_to_text(getattr(response, "content", response)).strip()
    except Exception as exc:
        summary = prior or "summary unavailable (%s)" % exc
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[:SUMMARY_MAX_CHARS] + "...[cut]"
    return summary


def compression_start():
    if HISTORY and str(HISTORY[0].get("content", "")).startswith(SUMMARY_MARKER):
        return 1
    return 0


def history_pairs():
    return sum(1 for turn in HISTORY if turn.get("role") == "user")


def evict_history(limit):
    global COMPRESSED_TURNS
    dropped = 0
    while history_pairs() > limit:
        start = compression_start()
        if len(HISTORY) - start < 2:
            break
        overflow = HISTORY[start:start + 2]
        del HISTORY[start:start + 2]
        archive_turns(overflow, "evicted")
        dropped += 2
    return dropped


def compress_history(force=False):
    global COMPRESSED_TURNS
    if CONTEXT_TOKENS <= 0:
        return None
    folded = 0
    summaries = []
    passes = 0
    while passes < 8:
        passes += 1
        tokens = history_tokens(HISTORY) + count_tokens(SYSTEM_PROMPT)
        if not force and tokens <= CONTEXT_TOKENS:
            break
        force = False
        start = compression_start()
        available = len(HISTORY) - start - 2
        if available < 4:
            break
        batch = min(COMPRESS_BATCH, available)
        if batch % 2:
            batch -= 1
        if batch <= 0:
            break
        prior = ""
        if start:
            prior = str(HISTORY[0].get("content", ""))[len(SUMMARY_MARKER):].strip()
        older = HISTORY[start:start + batch]
        summary = summarize_turns(older, prior)
        del HISTORY[start:start + batch]
        archive_turns(older, "compressed")
        folded += batch
        if not summary:
            continue
        COMPRESSED_TURNS += batch
        summaries.append(summary)
        entry = {"role": "assistant", "content": SUMMARY_MARKER + " " + summary}
        if start:
            HISTORY[0] = entry
        else:
            HISTORY.insert(0, entry)
    if not folded:
        return None
    return {"folded": folded, "summaries": summaries}


def run_chat(message):
    if not INJECTED:
        raise RuntimeError(
            "MonkeyCode LLM was not injected: MCAI_LLM_BASE_URL and "
            "MCAI_LLM_API_KEY are required"
        )
    message, trimmed = truncate_message(safe_text(message))
    with HISTORY_LOCK:
        compress_history()

        messages = [SystemMessage(content=safe_text(SYSTEM_PROMPT))]
        for turn in HISTORY:
            if turn["role"] == "user":
                messages.append(HumanMessage(content=safe_text(turn["content"])))
            else:
                messages.append(AIMessage(content=safe_text(turn["content"])))
        messages.append(HumanMessage(content=safe_text(message)))

        reply, used = call_with_protocol_retry(messages)

        reply, trimmed_reply = truncate_message(safe_text(reply))
        HISTORY.append({"role": "user", "content": safe_text(message)})
        HISTORY.append({"role": "assistant", "content": reply})
        dropped = evict_history(MAX_TURNS)
        compress_history()
        return reply, used, {
            "trimmed_input": trimmed,
            "trimmed_reply": trimmed_reply,
            "evicted": dropped,
            "compressed_turns": COMPRESSED_TURNS,
            "history_turns": history_pairs(),
            "context_tokens": CONTEXT_TOKENS,
            "history_tokens": history_tokens(HISTORY),
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("isolis-agent %s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, status, payload):
        payload = dict(payload)
        payload.update(common_fields())
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

    def path_only(self):
        return urlparse(self.path).path

    def do_GET(self):
        path = self.path_only()
        try:
            if path == "/health":
                self.send_json(200, {"ok": True})
            elif path == "/info":
                with HISTORY_LOCK:
                    turns = len(HISTORY) // 2
                    used_tokens = history_tokens(HISTORY) + count_tokens(SYSTEM_PROMPT)
                with ARCHIVE_LOCK:
                    archived = len(ARCHIVE)
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "framework": AGENT_FRAMEWORK,
                        "langchain": "https://github.com/langchain-ai/langchain",
                        "langchain_source": AGENT_SOURCE,
                        "source": AGENT_SOURCE,
                        "profile": AGENT_PROFILE,
                        "install": INSTALL_ENABLED,
                        "android_home": ANDROID_HOME,
                        "model": MODEL,
                        "llm_host": LLM_HOST,
                        "via": "mc-direct",
                        "injected": INJECTED,
                        "protocol": detect_protocol() if INJECTED else "unset",
                        "tools": [t.name for t in TOOLS],
                        "workspace": WORKSPACE,
                        "history_turns": turns,
                        "history_tokens": used_tokens,
                        "context_tokens": CONTEXT_TOKENS,
                        "context_hard_limit": CONTEXT_HARD_LIMIT,
                        "max_message_chars": MAX_MESSAGE_CHARS,
                        "max_turns": MAX_TURNS,
                        "compress_batch": COMPRESS_BATCH,
                        "compressed_turns": COMPRESSED_TURNS,
                        "archived_turns": archived,
                    },
                )
            elif path == "/history":
                with HISTORY_LOCK:
                    live = [dict(turn) for turn in HISTORY]
                    tokens = history_tokens(HISTORY)
                with ARCHIVE_LOCK:
                    archived = [dict(entry) for entry in ARCHIVE]
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "context_tokens": CONTEXT_TOKENS,
                        "history_tokens": tokens,
                        "compressed_turns": COMPRESSED_TURNS,
                        "history": live,
                        "archive": archived,
                    },
                )
            else:
                self.send_json(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            self.send_json(
                500,
                {"ok": False, "error": str(exc), "trace": traceback.format_exc()},
            )

    def do_POST(self):
        path = self.path_only()
        if path != "/chat":
            self.send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
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
            reply, tools_used, memory = run_chat(safe_text(message))
            self.send_json(
                200,
                {
                    "ok": True,
                    "reply": reply,
                    "tools_used": tools_used,
                    "memory": memory,
                },
            )
        except Exception as exc:
            self.send_json(
                500,
                {"ok": False, "error": str(exc), "trace": traceback.format_exc()},
            )


def main():
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
