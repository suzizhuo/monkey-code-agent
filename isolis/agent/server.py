#!/usr/bin/env python3
"""Isolis in-container / process agent runtime.

Stdlib HTTP server + LangChain (OpenAI-compatible) chat with a bounded tool
set. Everything the agent can touch is confined to the workspace directory.
LangChain is imported lazily through a guard: /health and /info must answer
even when the framework or the platform LLM is missing.

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
  ISOLIS_COMPRESS_BATCH    pairs folded per compression pass (default 40)
  ISOLIS_ARCHIVE_MAX       archive entries kept, never sent to the model (4000)
  ISOLIS_BIND              listen address (default 0.0.0.0)
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

    AIMessage = HumanMessage = SystemMessage = ToolMessage = _Msg

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
    or "monkeycode-pro/gpt-5.6-terra"
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


def get_llm():
    global _LLM
    with LLM_LOCK:
        if _LLM is None:
            if not HAS_LANGCHAIN:
                raise RuntimeError("langchain is not installed")
            if not INJECTED:
                raise RuntimeError(
                    "MonkeyCode LLM was not injected: MCAI_LLM_BASE_URL and "
                    "MCAI_LLM_API_KEY are required"
                )
            os.environ.setdefault("TIKTOKEN_CACHE_DIR", "/tmp")
            _LLM = ChatOpenAI(
                model=MODEL,
                api_key=API_KEY,
                base_url=BASE_URL,
                timeout=60,
                max_retries=1,
                temperature=0.4,
            )
        return _LLM


_LLM = None
LLM_LOCK = threading.Lock()


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
    """Bounded turn history with archive and one-pass compression.

    HISTORY holds live message objects. The newest MAX_TURNS pairs are kept;
    older entries are archived (reason "evicted") and never sent to the model.
    When live plus system tokens exceed CONTEXT_TOKENS the oldest
    COMPRESS_BATCH pairs fold into a single [compressed history] system
    message at the front (reason "compressed"); the newest pair is never
    folded. CONTEXT_TOKENS=0 disables compression.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.history = []
        self.archive = []
        self.compressed_turns = 0
        self.summary = ""

    def live_turns(self):
        with self.lock:
            return len(self.history) // 2

    def tokens(self):
        with self.lock:
            body = sum(
                count_tokens(getattr(message, "content", "") or "")
                for message in self.history
            )
        return body + count_tokens(system_prompt()) + count_tokens(self.summary)

    def append(self, message):
        with self.lock:
            self.history.append(message)
            self._trim_locked()

    def extend(self, messages):
        with self.lock:
            self.history.extend(messages)
            self._trim_locked()

    def _trim_locked(self):
        limit = MAX_TURNS * 2
        while len(self.history) > limit:
            evicted = self.history.pop(0)
            self._archive_locked(evicted, "evicted")

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
        with self.lock:
            live = list(self.history)
            archive = list(self.archive)
            return live, archive, self.compressed_turns, self.summary

    def window(self):
        """Messages to send: summary first, then live history."""
        with self.lock:
            live = list(self.history)
        messages = [SystemMessage(content=system_prompt())]
        if self.summary:
            messages.append(SystemMessage(content=self.summary))
        messages.extend(live)
        return messages

    def maybe_compress(self, llm):
        if CONTEXT_TOKENS <= 0:
            return False
        with self.lock:
            live_tokens = sum(
                count_tokens(getattr(message, "content", "") or "")
                for message in self.history
            )
            over = live_tokens + count_tokens(system_prompt()) > CONTEXT_TOKENS
            if not over:
                return False
            fold = min(COMPRESS_BATCH * 2, max(0, len(self.history) - 2))
            if fold <= 0:
                return False
            batch = self.history[:fold]
            del self.history[:fold]
        text = self._summarize(batch, llm)
        with self.lock:
            for message in batch:
                self._archive_locked(message, "compressed")
            self.compressed_turns += len(batch) // 2
            self.summary = "%s %s" % (SUMMARY_MARKER, text)
        return True

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
                "history_turns": len(self.history) // 2,
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
        if member.isdev():
            raise ValueError("device member rejected: %s" % name)
        yield member


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
            tar.extract(member, path=root, filter="data")
            count += 1
    return count


# ------------------------------------------------------------------ chat run


def run_chat(message):
    """One chat turn: tool loop with bounded steps and memory bookkeeping."""
    text, trimmed = truncate_message(message)
    if not text.strip():
        raise ValueError("message is empty")
    if not HAS_LANGCHAIN:
        raise RuntimeError("langchain is not installed")
    llm = get_llm()
    bound = llm.bind_tools(select_tool_schemas())
    tools_used = []
    messages = MEMORY.window()
    messages.append(HumanMessage(content=text))
    reply = ""
    for _step in range(MAX_TOOL_STEPS):
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
            "framework": "langchain" if HAS_LANGCHAIN else "none",
            "source": AGENT_SOURCE,
            "profile": AGENT_PROFILE,
            "install": INSTALL_ENABLED,
            "android_home": os.environ.get("ANDROID_HOME", "").strip(),
            "model": MODEL,
            "llm_host": urlparse(base).netloc if base else "",
            "via": "mc-direct",
            "injected": bool(base and key),
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
                "history_turns": len(live) // 2,
                "compressed_turns": compressed,
                "summary": summary,
                "history": [
                    {
                        "role": type(message).__name__,
                        "content": safe_text(getattr(message, "content", "") or ""),
                    }
                    for message in live
                ],
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
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", "/tmp")
    try:
        os.makedirs(WORKSPACE, exist_ok=True)
    except OSError:
        pass
    server = ThreadingHTTPServer((bind, 8080), IsolisHandler)
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
