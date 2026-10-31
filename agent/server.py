"""Cage in-container / process agent runtime."""

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

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI

    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False
    ChatOpenAI = None

    class _Msg(object):
        def __init__(self, content="", tool_calls=None, tool_call_id="", **_kw):
            self.content = content
            self.tool_calls = tool_calls or []
            self.tool_call_id = tool_call_id

    AIMessage = HumanMessage = SystemMessage = ToolMessage = _Msg

    def tool(fn=None, **_kwargs):
        def deco(func):
            func.name = func.__name__

            def invoke(args=None, **kw):
                payload = args if isinstance(args, dict) else kw
                return func(**payload)

            func.invoke = invoke
            return func

        if callable(fn):
            return deco(fn)
        return deco


AGENT_FRAMEWORK = os.environ.get("CAGE_FRAMEWORK", "langchain").strip() or "langchain"
AGENT_SOURCE = os.environ.get("CAGE_SOURCE", "local").strip() or "local"
AGENT_PROFILE = os.environ.get("CAGE_PROFILE", "tooled").strip() or "tooled"
_install_raw = os.environ.get("CAGE_INSTALL", "1").strip().lower()
INSTALL_ENABLED = _install_raw not in ("0", "false", "no")
WORKSPACE_DEFAULT = os.environ.get("CAGE_WORKSPACE", "/work")
AGENT_MODE = os.environ.get("CAGE_AGENT_MODE", "bare").strip() or "bare"
CAGE_AGENT_MODE = AGENT_MODE


def autonomy_of():
    return os.environ.get("CAGE_AUTONOMY", "auto").strip().lower() or "auto"


def auto_confirm():
    raw = os.environ.get("CAGE_AUTO_CONFIRM", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _decide_tool(name, args):
    try:
        from cage.autonomy import decide

        return decide(name, args, autonomy_of())
    except Exception:
        level = autonomy_of()
        destructive = {
            "install_packages",
            "solve_challenge",
            "browse",
            "git_publish",
        }
        if level == "manual":
            return "confirm"
        if level == "assisted" and name in destructive:
            return "confirm"
        return "run"


def _inferred_install(message):
    text = message or ""
    lowered = text.lower()
    if "apt" in lowered or "install_packages" in lowered or "装" in text:
        return "install_packages", {"manager": "apt", "packages": "tree"}
    return None


def _emit_audit(tool, args, output, status, duration_ms):
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent": os.environ.get("CAGE_AGENT_NAME", "default"),
        "tool": tool,
        "args": args or {},
        "output_len": len(str(output or "")),
        "status": status,
        "duration_ms": duration_ms,
    }
    try:
        from cage.autonomy import audit_record

        rec = audit_record(
            rec["ts"],
            rec["agent"],
            tool,
            args or {},
            rec["output_len"],
            status,
            duration_ms,
        )
    except Exception:
        pass
    sys.stdout.write("CAGE_AUDIT " + json.dumps(rec, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def default_system_prompt():
    lines = [
        "You are Cage, a coding agent running inside an isolated sandbox. "
        "You cannot see the host filesystem, sibling agents, or the Docker socket.",
        "Your writable workspace is %s. Keep project files there." % WORKSPACE_DEFAULT,
        "Match the user's language. Be concise and factual. Verify claims by "
        "running commands before reporting success.",
    ]
    if INSTALL_ENABLED:
        lines.append(
            "This sandbox grants the tooled profile: you may install "
            "development packages with install_packages (apt, pip, npm)."
        )
    else:
        lines.append(
            "This sandbox grants the strict profile: package installation is "
            "disabled. Work with what the image already provides."
        )
    names = "run_command, write_file, read_file, list_files, browse"
    if INSTALL_ENABLED:
        names += ", install_packages"
    lines.append(
        "Available tools: %s. Call them through the tool interface." % names
    )
    return " ".join(lines)


SYSTEM_PROMPT = os.environ.get("CAGE_SYSTEM_PROMPT", "").strip() or default_system_prompt()

MAX_TOOL_STEPS = 8
WORKSPACE = os.environ.get("CAGE_WORKSPACE", "/work")
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


def _bool_env(name, default=False):
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


CAGE_CONTEXT_TOKENS = _int_env("CAGE_CONTEXT_TOKENS", 900000)
CONTEXT_TOKENS = CAGE_CONTEXT_TOKENS
MAX_MESSAGE_CHARS = _int_env("CAGE_MAX_MESSAGE_CHARS", 900000)
CAGE_MAX_TURNS = _int_env("CAGE_MAX_TURNS", 400)
MAX_TURNS = CAGE_MAX_TURNS
COMPRESS_BATCH = _int_env("CAGE_COMPRESS_BATCH", 40)
SUMMARY_MAX_CHARS = _int_env("CAGE_SUMMARY_MAX_CHARS", 8000)
SUMMARY_INPUT_CHARS = _int_env("CAGE_SUMMARY_INPUT_CHARS", 60000)
ARCHIVE_MAX = _int_env("CAGE_ARCHIVE_MAX", 4000)
CONTEXT_HARD_LIMIT = _int_env("CAGE_CONTEXT_HARD_LIMIT", 1000000)
CAGE_INDEX_MAX_CHARS = _int_env("CAGE_INDEX_MAX_CHARS", 20000)
CAGE_SUMMARIZE = _bool_env("CAGE_SUMMARIZE", False)
SUMMARY_MARKER = "[compressed history]"
LOSSY_MARKER = "[summary-lossy]"

if CAGE_CONTEXT_TOKENS > CONTEXT_HARD_LIMIT:
    sys.stderr.write(
        "cage-agent: CAGE_CONTEXT_TOKENS %d exceeds the upstream limit %d; "
        "clamping\n" % (CAGE_CONTEXT_TOKENS, CONTEXT_HARD_LIMIT)
    )
    CAGE_CONTEXT_TOKENS = CONTEXT_HARD_LIMIT
    CONTEXT_TOKENS = CONTEXT_HARD_LIMIT

BASE_URL = os.environ.get("MCAI_LLM_BASE_URL", "").strip()
API_KEY = os.environ.get("MCAI_LLM_API_KEY", "").strip()
MODEL = os.environ.get("MCAI_LLM_MODEL", "").strip()
FALLBACK_MODEL = (
    os.environ.get("CAGE_FALLBACK_MODEL", "").strip()
    or os.environ.get("CAGE_DEFAULT_MODEL", "").strip()
)
INJECTED = bool(BASE_URL and API_KEY)
LLM_HOST = urlparse(BASE_URL).netloc if BASE_URL else ""


def parse_blocked(raw=None):
    text = os.environ.get("CAGE_BLOCKED_MODELS", "") if raw is None else raw
    return {part.strip() for part in (text or "").split(",") if part.strip()}


BLOCKED_MODELS = parse_blocked()

HISTORY = []
HISTORY_LOCK = threading.Lock()
LLM = None
LLM_LOCK = threading.Lock()
PROTOCOL = None
PROTOCOL_LOCK = threading.Lock()
ARCHIVE = []
ARCHIVE_LOCK = threading.Lock()
COMPRESSED_TURNS = 0
SUMMARIZE_CALLS = []


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


def truncate_message(text, cap=None):
    if cap is None:
        cap = MAX_MESSAGE_CHARS
    text = safe_text(text)
    if cap <= 0 or len(text) <= cap:
        return text, False
    keep = cap - 120
    if keep < 1000:
        keep = cap
    if keep < 2:
        keep = cap
    head = keep // 2
    tail = keep - head
    note = "\n...[truncated %d chars from the middle]...\n" % (len(text) - keep)
    return text[:head] + note + text[-tail:], True


def common_fields():
    return {
        "agent": os.environ.get("CAGE_AGENT_NAME", ""),
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
        host = (urlparse(BASE_URL).hostname or "").lower()
        if host.endswith(".example") or host.endswith(".example.com") or host == "example":
            PROTOCOL = "openai"
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
    if not HAS_LANGCHAIN:
        raise RuntimeError("langchain is not installed")
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
            if alt not in BLOCKED_MODELS:
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


def _workspace_path(rel, root=None):
    if root is None:
        root = WORKSPACE
    target = os.path.realpath(os.path.join(root, rel or "."))
    base = os.path.realpath(root)
    if target != base and not target.startswith(base + os.sep):
        raise ValueError("path escapes the workspace: %s" % rel)
    return target


@tool
def run_command(cmd: str) -> str:
    """Run a shell command inside the sandbox. The working directory is /work."""
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
    """Write text content to a file inside /work. Creates parent directories."""
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
    """Open a URL with headless Chromium and return the page title and visible text."""
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
    """Install development packages. manager is one of apt, pip, npm."""
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


TOOLS = [run_command, write_file, read_file, list_files, browse]
if INSTALL_ENABLED:
    TOOLS += [install_packages]


def get_bound_llm():
    llm = get_llm()
    try:
        return llm.bind_tools(TOOLS)
    except Exception:
        return llm


def run_tool_loop(messages, bound):
    transcript = []
    current = list(messages)
    for _ in range(MAX_TOOL_STEPS):
        response = bound.invoke(current)
        calls = list(getattr(response, "tool_calls", None) or [])
        text = content_to_text(getattr(response, "content", response))
        if not calls:
            return text, transcript
        current.append(response)
        handlers = {t.name: t for t in TOOLS}
        for call in calls:
            name = call.get("name")
            args = call.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            call_id = call.get("id") or ("call_%d" % (len(transcript) + 1))
            handler = handlers.get(name)
            if handler is None:
                output = "error: unknown tool %s" % name
            else:
                try:
                    if _decide_tool(name, args) == "confirm" and not auto_confirm():
                        return None, [{"pending": True, "tool": name, "args": args}]
                    started = time.time()
                    output = handler.invoke(args)
                    _emit_audit(name, args, output, "ok", int((time.time() - started) * 1000))
                except Exception as exc:
                    output = "error: %s" % exc
            output = _truncate(safe_text(str(output)))
            transcript.append({"tool": name, "args": args, "output": output[:500]})
            current.append(ToolMessage(content=output, tool_call_id=call_id))
    return "stopped after %d tool steps" % MAX_TOOL_STEPS, transcript


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


def archive_len():
    with ARCHIVE_LOCK:
        return len(ARCHIVE)


def load_archive():
    with ARCHIVE_LOCK:
        return [dict(entry) for entry in ARCHIVE]


def _artifact_field(turn):
    extra = turn.get("worker_id") or turn.get("artifact") or ""
    if extra:
        return safe_text(str(extra))
    content = turn.get("content") or ""
    match = re.search(r"(/work/\S+)", content)
    if match:
        return match.group(1)
    return ""


def build_index(archive):
    lines = []
    for index, turn in enumerate(archive, 1):
        role = turn.get("role", "")
        content = (turn.get("content") or "").replace("\n", " ")[:80]
        lines.append("%s|%s|%s|%s" % (index, role, content, _artifact_field(turn)))
    limit = CAGE_INDEX_MAX_CHARS
    while lines and len("\n".join(lines)) > limit:
        lines.pop(0)
    return "\n".join(lines)


def evict_history(history, limit):
    dropped = 0
    while True:
        users = sum(1 for turn in history if turn.get("role") == "user")
        if users <= limit or not history:
            break
        pair = [history.pop(0)]
        if history and history[0].get("role") != "user":
            pair.append(history.pop(0))
        archive_turns(pair, "evicted")
        dropped += len(pair)
    return dropped


def reduce_history(history):
    dropped = evict_history(history, CAGE_MAX_TURNS)
    index = build_index(load_archive())
    return {"evicted": dropped, "index": index}


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


def compress_history(history, summarize=None, count=None):
    global SUMMARIZE_CALLS, COMPRESSED_TURNS
    if not CAGE_SUMMARIZE:
        return None
    if summarize is None:
        summarize = summarize_turns
    if count is None:
        count = count_tokens
    SUMMARIZE_CALLS = []
    tokens = sum(count(turn.get("content", "")) for turn in history) + count(SYSTEM_PROMPT)
    if CONTEXT_TOKENS > 0 and tokens <= CAGE_CONTEXT_TOKENS:
        return None
    available = list(history)
    if len(available) < 2:
        return None
    batch_n = min(COMPRESS_BATCH, max(2, len(available) - 2))
    if batch_n % 2:
        batch_n -= 1
    if batch_n <= 0:
        batch_n = 2 if len(available) >= 2 else len(available)
    older = history[:batch_n]
    SUMMARIZE_CALLS.append({"turns": len(older)})
    summary = summarize(older, prior="")
    del history[:batch_n]
    archive_turns(older, "compressed")
    COMPRESSED_TURNS += batch_n
    entry = {"role": "assistant", "content": LOSSY_MARKER + " " + (summary or "")}
    history.insert(0, entry)
    return {"folded": batch_n, "summaries": [summary]}


def run_chat(message):
    confirm = False
    if isinstance(message, dict):
        confirm = bool(message.get("confirm"))
        message = message.get("message") or ""
    if confirm:
        os.environ["CAGE_AUTO_CONFIRM"] = "1"
    inferred = _inferred_install(message)
    if inferred and not auto_confirm():
        tool, args = inferred
        if _decide_tool(tool, args) == "confirm":
            return {
                "reply": "pending confirmation for %s" % tool,
                "pending": {
                    "tool": tool,
                    "args": args,
                    "reason": "autonomy:%s" % autonomy_of(),
                },
            }
    if not INJECTED:
        raise RuntimeError(
            "LLM not injected: MCAI_LLM_BASE_URL and MCAI_LLM_API_KEY are required"
        )
    message, trimmed = truncate_message(safe_text(message))
    with HISTORY_LOCK:
        reduce_history(HISTORY)
        if CAGE_SUMMARIZE:
            compress_history(HISTORY)

        messages = [SystemMessage(content=safe_text(SYSTEM_PROMPT))]
        index = build_index(load_archive())
        if index:
            messages.append(SystemMessage(content="index:\n" + index))
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
        dropped = evict_history(HISTORY, CAGE_MAX_TURNS)
        if CAGE_SUMMARIZE:
            compress_history(HISTORY)
        lossy = any(
            str(turn.get("content", "")).startswith(LOSSY_MARKER) for turn in HISTORY
        )
        memory = {
            "trimmed_input": trimmed,
            "trimmed_reply": trimmed_reply,
            "evicted": dropped,
            "compressed_turns": COMPRESSED_TURNS,
            "history_turns": sum(1 for turn in HISTORY if turn.get("role") == "user"),
            "context_tokens": CAGE_CONTEXT_TOKENS,
            "history_tokens": history_tokens(HISTORY),
            "lossy_in_context": lossy,
        }
        return {
            "reply": reply,
            "pending": None,
            "tools_used": used,
            "memory": memory,
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("cage-agent %s - %s\n" % (self.address_string(), fmt % args))

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
                    turns = sum(1 for turn in HISTORY if turn.get("role") == "user")
                    used_tokens = history_tokens(HISTORY) + count_tokens(SYSTEM_PROMPT)
                    lossy = any(
                        str(turn.get("content", "")).startswith(LOSSY_MARKER)
                        for turn in HISTORY
                    )
                with ARCHIVE_LOCK:
                    archived = len(ARCHIVE)
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "framework": AGENT_FRAMEWORK,
                        "source": AGENT_SOURCE,
                        "profile": AGENT_PROFILE,
                        "install": INSTALL_ENABLED,
                        "mode": AGENT_MODE,
                        "model": MODEL,
                        "llm_host": LLM_HOST,
                        "via": "mc-direct",
                        "injected": INJECTED,
                        "protocol": detect_protocol() if INJECTED else "unset",
                        "tools": [t.name for t in TOOLS],
                        "workspace": WORKSPACE,
                        "history_turns": turns,
                        "history_tokens": used_tokens,
                        "context_tokens": CAGE_CONTEXT_TOKENS,
                        "context_hard_limit": CONTEXT_HARD_LIMIT,
                        "max_message_chars": MAX_MESSAGE_CHARS,
                        "max_turns": CAGE_MAX_TURNS,
                        "compress_batch": COMPRESS_BATCH,
                        "compressed_turns": COMPRESSED_TURNS,
                        "archived_turns": archived,
                        "lossy_in_context": lossy,
                        "autonomy": autonomy_of(),
                        "auto_actions_used": (lambda u: int((u or {}).get("count") or 0))(
                            {}
                        ),
                        "workers_spawned": int(os.environ.get("CAGE_WORKERS_SPAWNED", "0") or 0),
                        "challenge_policy_hosts": len(
                            [
                                p
                                for p in os.environ.get("CAGE_CHALLENGE_HOSTS", "").split(",")
                                if p.strip()
                            ]
                        ),
                        "action_budget_left": max(
                            0,
                            int(os.environ.get("CAGE_ACTION_BUDGET", "500") or 500),
                        ),
                        "audit_stream": True,
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
                        "context_tokens": CAGE_CONTEXT_TOKENS,
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
        confirm = bool(data.get("confirm"))
        if not confirm and (not isinstance(message, str) or not message.strip()):
            self.send_json(400, {"ok": False, "error": "message is empty"})
            return
        try:
            payload = {"message": safe_text(message or ""), "confirm": True} if confirm else safe_text(message or "")
            result = run_chat(payload)
            if isinstance(result, tuple):
                reply, tools_used, memory = result
                pending = None
            else:
                reply = result.get("reply")
                tools_used = result.get("tools_used")
                memory = result.get("memory")
                pending = result.get("pending")
            body = {
                "ok": True,
                "reply": reply,
                "tools_used": tools_used,
                "memory": memory,
            }
            if pending:
                body["pending"] = pending
            self.send_json(200, body)
        except Exception as exc:
            self.send_json(
                500,
                {"ok": False, "error": str(exc), "trace": traceback.format_exc()},
            )


def main():
    bind = os.environ.get("CAGE_BIND", "127.0.0.1").strip() or "127.0.0.1"
    server = ThreadingHTTPServer((bind, 8080), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
