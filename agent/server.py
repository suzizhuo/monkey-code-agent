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

MAX_TURNS = 24
MAX_TOOL_STEPS = 8
WORKSPACE = os.environ.get("ISOLIS_WORKSPACE", "/work")
SHELL_TIMEOUT = 120
INSTALL_TIMEOUT = 1800
MAX_OUTPUT = 20000

BASE_URL = os.environ.get("MCAI_LLM_BASE_URL", "").strip()
API_KEY = os.environ.get("MCAI_LLM_API_KEY", "").strip()
MODEL = os.environ.get("MCAI_LLM_MODEL", "").strip()
INJECTED = bool(BASE_URL and API_KEY)
LLM_HOST = urlparse(BASE_URL).netloc if BASE_URL else ""

HISTORY = []
HISTORY_LOCK = threading.Lock()
LLM = None
LLM_LOCK = threading.Lock()
PROTOCOL = None
PROTOCOL_LOCK = threading.Lock()


def common_fields():
    return {
        "agent": os.environ.get("ISOLIS_AGENT_NAME", ""),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
    }


def content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                block_type = block.get("type")
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif block_type in ("thinking", "redacted_thinking", "signature"):
                    continue
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return str(content)


def probe(url, body, timeout=6):
    data = json.dumps(body).encode("utf-8")
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
            text=True,
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
            handle.write(content)
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
            text=True,
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
            text=True,
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
            output = _truncate(str(output))
            transcript.append({"tool": name, "args": args, "output": output[:500]})
            current.append(ToolMessage(content=output, tool_call_id=call.get("id")))
    final = content_to_text(getattr(current[-1], "content", ""))
    return final or "stopped after %d tool steps" % MAX_TOOL_STEPS, transcript


def run_chat(message):
    if not INJECTED:
        raise RuntimeError(
            "MonkeyCode LLM was not injected: MCAI_LLM_BASE_URL and "
            "MCAI_LLM_API_KEY are required"
        )
    with HISTORY_LOCK:
        messages = [SystemMessage(content=SYSTEM_PROMPT)]
        for turn in HISTORY:
            if turn["role"] == "user":
                messages.append(HumanMessage(content=turn["content"]))
            else:
                messages.append(AIMessage(content=turn["content"]))
        messages.append(HumanMessage(content=message))

        bound = get_bound_llm()
        if bound is None:
            response = get_llm().invoke(messages)
            reply = content_to_text(getattr(response, "content", response))
            used = []
        else:
            reply, used = run_tool_loop(messages, bound)

        HISTORY.append({"role": "user", "content": message})
        HISTORY.append({"role": "assistant", "content": reply})
        if len(HISTORY) > MAX_TURNS * 2:
            del HISTORY[: len(HISTORY) - MAX_TURNS * 2]
        return reply, used


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("isolis-agent %s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, status, payload):
        payload = dict(payload)
        payload.update(common_fields())
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
            data = json.loads(raw.decode("utf-8"))
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
            reply, tools_used = run_chat(message)
            self.send_json(
                200,
                {"ok": True, "reply": reply, "tools_used": tools_used},
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
