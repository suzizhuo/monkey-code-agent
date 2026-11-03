#!/usr/bin/env python3
"""Isolis: host TUI that runs isolated LangChain agents in Docker."""

import hashlib
import http.client
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DOCKERD_LOG = "/tmp/isolis-dockerd.log"
DAEMON_JSON = "/etc/docker/daemon.json"
DEFAULT_MIRROR = "https://docker.m.daocloud.io"
HUB_PROBE = "https://registry-1.docker.io/v2/"
DOCKERD_START_TIMEOUT = 45
NETWORK = "isolis-net"
DEFAULT_NAME = "default"
NAME_MAX = 40
PORT_MIN = 18080
PORT_MAX = 18119
CONTAINER_PORT = 8080
STATE_DIR = ".isolis"
STATE_FILE = os.path.join(STATE_DIR, "state.json")
AGENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent")
BANNED_MODELS = {
    "auto",
    "step-5-preview",
    "deepseek-chat",
    "deepseek-flash",
}
DEFAULT_MODEL = "isolis-chat-default"
MODEL_PREFIX = "monkeycode-pro/"
PROFILES = {
    "strict": {
        "memory": "512m",
        "cpus": "1.0",
        "pids": "128",
        "workspace": "512m",
        "tmp": "64m",
        "shm": "64m",
        "user": "nobody",
        "caps": [],
        "read_only": True,
        "install": False,
    },
    "tooled": {
        "memory": "4g",
        "cpus": "2.0",
        "pids": "512",
        "workspace": "2g",
        "tmp": "512m",
        "shm": "512m",
        "user": "0:0",
        "caps": [
            "CHOWN",
            "DAC_OVERRIDE",
            "FOWNER",
            "SETUID",
            "SETGID",
            "SETFCAP",
        ],
        "read_only": False,
        "install": True,
    },
}
DEFAULT_PROFILE = "tooled"
ANDROID_PATH_ENV = (
    "PATH=/opt/android-sdk/platform-tools:"
    "/opt/android-sdk/cmdline-tools/latest/bin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)
STATE_DIR = ".isolis"
STATE_FILE = os.path.join(STATE_DIR, "state.json")
PROCESS_DIR = os.path.join(STATE_DIR, "run")
PROCESS_LOG_DIR = os.path.join(STATE_DIR, "logs")
PROCESS_WORK_DIR = os.path.join(STATE_DIR, "workspaces")
AGENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent")
AGENT_SRC_DIR = os.path.join(STATE_DIR, "agent-src")


class IsolisError(Exception):
    pass


def configure_stdio():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


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


def announce_name(raw, name):
    if raw is not None and name != raw:
        print("notice: agent name sanitized to '%s'" % name)


def sanitize_name(raw):
    if raw is None:
        raw = ""
    value = re.sub(r"[^A-Za-z0-9_-]", "-", str(raw))
    value = re.sub(r"-+", "-", value)
    value = value.strip("-_")
    value = value[:NAME_MAX].strip("-_")
    return value


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def docker_bin():
    path = shutil.which("docker")
    if not path:
        raise IsolisError("docker CLI not found (install Docker and retry)")
    return path


def run_docker(args, timeout=60, check=True):
    cmd = [docker_bin()] + list(args)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise IsolisError("docker command timed out: docker " + " ".join(args))
    except OSError as exc:
        raise IsolisError("cannot run docker: %s" % exc)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        detail = detail.splitlines()[-1] if detail else "exit %d" % proc.returncode
        raise IsolisError("docker %s failed: %s" % (args[0], detail))
    return proc


def docker_available():
    return docker_usable()


def docker_cli_version():
    try:
        proc = run_docker(["--version"], timeout=15, check=False)
    except IsolisError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def reachable(url, timeout=8):
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, response.status
    except urllib.error.HTTPError as exc:
        return True, exc.code
    except (urllib.error.URLError, ssl.SSLError, OSError):
        return False, None


def read_daemon_config():
    try:
        with open(DAEMON_JSON, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def ensure_registry_mirror():
    hub_ok, _ = reachable(HUB_PROBE)
    config = read_daemon_config()
    if config is None:
        return "skip", "daemon.json is not valid JSON; leaving it untouched"
    mirrors = config.get("registry-mirrors")
    if isinstance(mirrors, list) and mirrors:
        return "ok", "mirror already configured: %s" % mirrors[0]
    if hub_ok:
        return "ok", "Docker Hub reachable; no mirror needed"
    mirrors = [DEFAULT_MIRROR]
    config["registry-mirrors"] = mirrors
    os.makedirs(os.path.dirname(DAEMON_JSON), exist_ok=True)
    tmp = DAEMON_JSON + ".isolis.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, DAEMON_JSON)
    except OSError as exc:
        raise IsolisError(
            "cannot write %s (%s); run as root or configure a mirror manually"
            % (DAEMON_JSON, exc)
        )
    return "add", "Docker Hub unreachable; added mirror %s" % DEFAULT_MIRROR


def start_dockerd():
    if os.geteuid() != 0:
        raise IsolisError(
            "docker daemon is not running and cannot be started without root; "
            "start Docker manually"
        )
    dockerd = shutil.which("dockerd")
    if not dockerd:
        raise IsolisError(
            "docker CLI is installed but dockerd is missing; install the Docker engine"
        )
    log = open(DOCKERD_LOG, "ab")
    try:
        subprocess.Popen(
            [
                dockerd,
                "--host=unix:///var/run/docker.sock",
                "--storage-driver=overlay2",
                "--iptables=true",
            ],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        raise IsolisError("cannot start dockerd: %s" % exc)
    finally:
        log.close()
    deadline = time.time() + DOCKERD_START_TIMEOUT
    while time.time() < deadline:
        if docker_available():
            return "started"
        time.sleep(1)
    tail = ""
    try:
        with open(DOCKERD_LOG, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().strip().splitlines()
            if lines:
                tail = lines[-1]
    except OSError:
        pass
    raise IsolisError(
        "dockerd did not become ready within %ds%s"
        % (DOCKERD_START_TIMEOUT, (": " + tail) if tail else " (see " + DOCKERD_LOG + ")")
    )


def ensure_docker(auto_start=True):
    if docker_available():
        return "running"
    if not shutil.which("docker"):
        raise IsolisError("docker CLI not found (install Docker and retry)")
    if not auto_start:
        raise IsolisError(
            "docker daemon is not available (start Docker and retry)"
        )
    ensure_registry_mirror()
    print("docker daemon is not running, starting dockerd ...")
    return start_dockerd()


# ------------------------------------------------------------ runtime modes

def docker_usable():
    """True when the Docker CLI exists and the daemon answers a probe."""
    if not shutil.which("docker"):
        return False
    try:
        proc = run_docker(
            ["info", "--format", "{{.ServerVersion}}"], timeout=20, check=False
        )
    except IsolisError:
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def detect_mode():
    """Runtime mode: docker when the CLI and the daemon answer, else process.

    A missing Docker CLI or an unreachable daemon selects the local process
    mode: agent/server.py runs on the host bound to 127.0.0.1 with the same
    env whitelist, so every command keeps working without Docker.
    """
    return "docker" if docker_usable() else "process"


def runtime_for(mode):
    return DockerRuntime() if mode == "docker" else ProcessRuntime()


_NOTICE_ONCE = {"degraded": False}


def _notice_reset():
    """One degrade notice budget per command (status loops, chat re-parses)."""
    _NOTICE_ONCE["degraded"] = False


def _note_once(text):
    if _NOTICE_ONCE["degraded"]:
        return
    _NOTICE_ONCE["degraded"] = True
    print(text)


def _runtime_and_mode(entry):
    """Effective (runtime, mode) for a state record, degrading when unusable.

    The recorded mode wins; a "docker" record on a host without a usable
    Docker CLI/daemon degrades to the local process mode (spec v1.3 H.7) with
    one notice instead of failing the whole command.
    """
    recorded = (entry or {}).get("mode") or "docker"
    if recorded == "docker" and not docker_usable():
        _note_once(
            "notice: recorded mode docker is unavailable (no Docker CLI or "
            "daemon); using process mode"
        )
        return runtime_for("process"), "process"
    return runtime_for(recorded), recorded


def _atomic_write_json(path, payload):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise IsolisError("cannot write %s: %s" % (path, exc))


def _process_meta_path(name):
    return os.path.join(PROCESS_DIR, name + ".json")


def _read_process_meta(name):
    try:
        with open(_process_meta_path(name), "r", encoding="utf-8") as handle:
            data = json.loads(handle.read())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_process_meta(name, payload):
    _atomic_write_json(_process_meta_path(name), payload)


def _clear_process_meta(name):
    try:
        os.remove(_process_meta_path(name))
    except OSError:
        pass


def container_env(name, port, llm, key, cfg, source):
    """Env whitelist shared by the container -e flags and the process mode.

    ISOLIS_PROMPT.md section G: MCAI_LLM_*, ISOLIS_AGENT_NAME, ISOLIS_PROFILE,
    ISOLIS_INSTALL, ISOLIS_MAX_TURNS, ISOLIS_MAX_MESSAGE_CHARS,
    ISOLIS_CONTEXT_TOKENS, ISOLIS_CONTEXT_HARD_LIMIT, ISOLIS_COMPRESS_BATCH,
    ISOLIS_ARCHIVE_MAX, plus ISOLIS_SYSTEM_PROMPT / ISOLIS_DEFAULT_MODEL /
    ISOLIS_MODEL when set. ISOLIS_FALLBACK_MODEL does not exist (defect D5).
    """
    entries = {
        "ISOLIS_AGENT_NAME": name,
        "ISOLIS_PROFILE": key,
        "ISOLIS_INSTALL": "1" if cfg["install"] else "0",
        "ISOLIS_SOURCE": sanitize_source(source),
        "ISOLIS_CONTEXT_TOKENS": str(context_tokens()),
        "ISOLIS_CONTEXT_HARD_LIMIT": str(CONTEXT_HARD_LIMIT),
        "ISOLIS_MAX_MESSAGE_CHARS": str(max_message_chars()),
        "ISOLIS_MAX_TURNS": str(max_turns()),
        "ISOLIS_COMPRESS_BATCH": str(compress_batch()),
        "ISOLIS_ARCHIVE_MAX": str(_env_int("ISOLIS_ARCHIVE_MAX", 4000)),
        "MCAI_LLM_BASE_URL": llm["base_url"],
        "MCAI_LLM_API_KEY": llm["api_key"],
        "MCAI_LLM_MODEL": llm["model"],
    }
    for optional in ("ISOLIS_SYSTEM_PROMPT", "ISOLIS_DEFAULT_MODEL", "ISOLIS_MODEL"):
        value = os.environ.get(optional, "").strip()
        if value:
            entries[optional] = value
    return entries


class DockerRuntime(object):
    """Container runtime: full isolation contract of ISOLIS_PROMPT.md 4.x."""

    mode = "docker"

    def ensure_image(self, profile, source, agent_dir=None, force=False):
        return build_image(force, profile, source, agent_dir)

    def ensure_network(self):
        return ensure_network()

    def start(self, name, port, llm, profile, source):
        start_container(name, "isolis-" + name, port, llm, profile, None, source)

    def start_existing(self, name):
        run_docker(["start", "isolis-" + name], timeout=120, check=False)
        return True

    def status(self, name):
        state = inspect_container("isolis-" + name)
        if state is None:
            return "missing"
        return "running" if state else "stopped"

    def port_of(self, name):
        return container_port("isolis-" + name)

    def stop(self, name):
        return remove_container("isolis-" + name)

    def logs(self, name):
        return last_log_line("isolis-" + name)


class ProcessRuntime(object):
    """No-Docker runtime: agent/server.py as a detached host process.

    Same HTTP contract, same env whitelist, bound to 127.0.0.1 on an allocated
    host port. The workspace lives under .isolis/workspaces/<name> so /files,
    export, import and batch keep working. Isolation lines report process mode
    honestly; this runtime is a degraded mode, not a claim of container-grade
    isolation.
    """

    mode = "process"

    def ensure_image(self, profile, source, agent_dir=None, force=False):
        key, _ = profile_config(profile)
        return image_tag(key, sanitize_source(source))

    def ensure_network(self):
        return None

    def start(self, name, port, llm, profile, source):
        key, cfg = profile_config(profile)
        env = dict(os.environ)
        env.update(
            container_env(name, port, llm, key, cfg, source)
        )
        workspace = os.path.join(PROCESS_WORK_DIR, name)
        home = os.path.join(PROCESS_DIR, "home", name)
        tiktoken = os.path.join(PROCESS_DIR, "tiktoken")
        env["ISOLIS_BIND"] = "127.0.0.1"
        env["ISOLIS_PORT"] = str(port)
        env["ISOLIS_WORKSPACE"] = workspace
        env["HOME"] = home
        env["LANG"] = "C.UTF-8"
        env["LC_ALL"] = "C.UTF-8"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env["TIKTOKEN_CACHE_DIR"] = tiktoken
        android = os.environ.get("ANDROID_HOME", "").strip()
        if android:
            env["ANDROID_HOME"] = android
        for path in (PROCESS_LOG_DIR, PROCESS_DIR, home, tiktoken, workspace):
            os.makedirs(path, exist_ok=True)
        server = os.path.join(AGENT_DIR, "server.py")
        if not os.path.isfile(server):
            raise IsolisError("agent server missing: %s" % server)
        log = open(os.path.join(PROCESS_LOG_DIR, name + ".log"), "ab")
        try:
            proc = subprocess.Popen(
                [sys.executable, server],
                cwd=workspace,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise IsolisError("cannot start agent process: %s" % exc)
        finally:
            log.close()
        _write_process_meta(
            name, {"pid": proc.pid, "port": int(port), "started_at": now_iso()}
        )
        return str(proc.pid)

    def start_existing(self, name):
        return False

    def status(self, name):
        meta = _read_process_meta(name)
        pid = (meta or {}).get("pid")
        if not isinstance(pid, int):
            return "missing"
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "stopped"
        except OSError:
            return "running"
        return "running"

    def port_of(self, name):
        meta = _read_process_meta(name)
        port = (meta or {}).get("port")
        try:
            port = int(port)
        except (TypeError, ValueError):
            return None
        if PORT_MIN <= port <= PORT_MAX:
            return port
        return None

    def stop(self, name):
        meta = _read_process_meta(name)
        pid = (meta or {}).get("pid")
        if isinstance(pid, int):
            self._kill(pid)
        _clear_process_meta(name)

    def logs(self, name):
        path = os.path.join(PROCESS_LOG_DIR, name + ".log")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().strip().splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-40:])

    @staticmethod
    def _kill(pid):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except OSError:
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass
            deadline = time.time() + (10 if sig == signal.SIGTERM else 3)
            gone = False
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                except OSError:
                    gone = True
                    break
                time.sleep(0.2)
            if gone:
                return


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"version": 1, "current": None, "agents": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        raise IsolisError("cannot read state file")
    if not isinstance(data, dict):
        raise IsolisError("cannot read state file")
    agents = data.get("agents")
    if not isinstance(agents, dict):
        agents = {}
    clean = {}
    for name, entry in agents.items():
        if isinstance(name, str) and isinstance(entry, dict):
            clean[name] = entry
    current = data.get("current")
    if not isinstance(current, str):
        current = None
    return {"version": 1, "current": current, "agents": clean}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    payload = {
        "version": 1,
        "current": state.get("current"),
        "agents": state.get("agents") or {},
    }
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, STATE_FILE)
    except OSError as exc:
        raise IsolisError("cannot write state file: %s" % exc)


def http_json(method, url, payload=None, timeout=15, token=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(json_sanitize(payload), ensure_ascii=False).encode("utf-8")
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
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        raise IsolisError("request failed: %s (%s)" % (url, exc))
    try:
        body = json.loads(raw.decode("utf-8", "replace")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        body = {}
    return status, body


def models_url(base):
    return base.rstrip("/") + "/models"


def is_banned_model(model_id):
    name = (model_id or "").strip()
    if not name:
        return False
    if name in BANNED_MODELS:
        return True
    return name.rsplit("/", 1)[-1] in BANNED_MODELS


def pick_model(injected, body):
    candidate = (injected or "").strip()
    if candidate and not is_banned_model(candidate):
        return candidate
    fallback = os.environ.get("ISOLIS_DEFAULT_MODEL", "").strip()
    items = body.get("data") if isinstance(body, dict) else None
    if isinstance(items, list):
        usable = []
        for item in items:
            model_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(model_id, str) and not is_banned_model(model_id):
                usable.append(model_id)
        for model_id in usable:
            if model_id.startswith(MODEL_PREFIX):
                return model_id
        if fallback and not is_banned_model(fallback):
            return fallback
        if usable:
            return usable[0]
    if fallback and not is_banned_model(fallback):
        return fallback
    return DEFAULT_MODEL


def detect_llm():
    base = os.environ.get("MCAI_LLM_BASE_URL", "").strip()
    key = os.environ.get("MCAI_LLM_API_KEY", "").strip()
    if not base or not key:
        raise IsolisError(
            "MonkeyCode LLM was not injected: MCAI_LLM_BASE_URL and "
            "MCAI_LLM_API_KEY are required (run doctor to see the checklist)"
        )
    try:
        status, body = http_json("GET", models_url(base), timeout=20, token=key)
    except IsolisError:
        # The gateway did not answer the model list. The injected model name
        # and the documented override order still resolve a model, so the
        # command keeps working with one notice line.
        status, body = None, {}
        print(
            "notice: gateway model list unreachable; using the injected model name"
        )
    if status in (401, 403):
        raise IsolisError(
            "MonkeyCode LLM rejected the injected key (HTTP %d); "
            "do not switch gateways" % status
        )
    if status not in (200, 404, 405, None):
        raise IsolisError(
            "MonkeyCode LLM at %s is unavailable (HTTP %d)" % (base, status)
        )
    model = pick_model(
        os.environ.get("ISOLIS_MODEL", "").strip()
        or os.environ.get("MCAI_LLM_MODEL", ""),
        body,
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


CONTEXT_HARD_LIMIT = 1000000
CONTEXT_DEFAULT = 900000
MESSAGE_CHARS_DEFAULT = 900000
MAX_TURNS_DEFAULT = 400


def _env_int(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise IsolisError("%s must be an integer (got %r)" % (name, raw))
    if value < 0:
        raise IsolisError("%s must be zero or positive" % name)
    return value


def max_message_chars():
    return _env_int("ISOLIS_MAX_MESSAGE_CHARS", MESSAGE_CHARS_DEFAULT)


def max_turns():
    return _env_int("ISOLIS_MAX_TURNS", MAX_TURNS_DEFAULT)


def compress_batch():
    return _env_int("ISOLIS_COMPRESS_BATCH", 40)


def context_tokens():
    value = _env_int("ISOLIS_CONTEXT_TOKENS", CONTEXT_DEFAULT)
    if value > CONTEXT_HARD_LIMIT:
        print(
            "notice: ISOLIS_CONTEXT_TOKENS %d exceeds the upstream limit %d; "
            "clamping to %d" % (value, CONTEXT_HARD_LIMIT, CONTEXT_HARD_LIMIT)
        )
        return CONTEXT_HARD_LIMIT
    return value


def profile_from_env():
    return os.environ.get("ISOLIS_PROFILE", "").strip() or DEFAULT_PROFILE


def source_from_env():
    return os.environ.get("ISOLIS_AGENT_SOURCE", "").strip() or "local"


def port_in_use(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def docker_published_ports():
    ports = set()
    try:
        proc = run_docker(["ps", "--format", "{{.Ports}}"], timeout=20, check=False)
    except IsolisError:
        return ports
    if proc.returncode != 0:
        return ports
    for match in re.finditer(r":(\d+)->", proc.stdout):
        ports.add(int(match.group(1)))
    return ports


def allocate_port(state):
    recorded = set()
    for entry in (state.get("agents") or {}).values():
        port = entry.get("port")
        if isinstance(port, int):
            recorded.add(port)
    busy = docker_published_ports()
    for port in range(PORT_MIN, PORT_MAX + 1):
        if port in recorded or port in busy or port_in_use(port):
            continue
        return port
    raise IsolisError(
        "no free port in %d-%d (stop an agent and retry)" % (PORT_MIN, PORT_MAX)
    )


def inspect_container(container):
    try:
        proc = run_docker(
            ["inspect", "--format", "{{.State.Running}}", container],
            timeout=20,
            check=False,
        )
    except IsolisError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() == "true"


def container_port(container):
    proc = run_docker(
        [
            "inspect",
            "--format",
            '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}',
            container,
        ],
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    try:
        port = int(text)
    except ValueError:
        return None
    if PORT_MIN <= port <= PORT_MAX:
        return port
    return None


def remove_container(container):
    run_docker(["rm", "-f", container], timeout=60, check=False)


def wait_health(port, seconds):
    deadline = time.time() + seconds
    url = "http://127.0.0.1:%d/health" % port
    while time.time() < deadline:
        try:
            status, body = http_json("GET", url, timeout=3)
            if status == 200 and body.get("ok") is True:
                return True
        except IsolisError:
            pass
        time.sleep(0.5)
    return False


def get_info(port, timeout=10):
    try:
        status, body = http_json(
            "GET", "http://127.0.0.1:%d/info" % port, timeout=timeout
        )
    except IsolisError:
        return None
    if status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
        return None
    return body


def last_log_line(container):
    proc = run_docker(["logs", "--tail", "1", container], timeout=20, check=False)
    text = ((proc.stderr or "") + (proc.stdout or "")).strip()
    return text.splitlines()[-1] if text else ""


def image_exists(tag):
    proc = run_docker(["image", "inspect", tag], timeout=20, check=False)
    return proc.returncode == 0


def build_image(force, profile=DEFAULT_PROFILE, source="local", agent_dir=None):
    key, cfg = profile_config(profile)
    build_dir = agent_dir or AGENT_DIR
    tag = image_tag(key, sanitize_source(source))
    if not force and image_exists(tag):
        return tag
    print("building image %s ..." % tag)
    run_docker(["build", "-t", tag, build_dir], timeout=5400)
    return tag


def ensure_network():
    proc = run_docker(["network", "inspect", NETWORK], timeout=20, check=False)
    if proc.returncode == 0:
        return
    run_docker(
        [
            "network",
            "create",
            "--opt",
            "com.docker.network.bridge.enable_icc=false",
            NETWORK,
        ],
        timeout=60,
    )


def image_tag(profile, source):
    digest = hashlib.sha256(
        (profile + "\n" + source).encode("utf-8")
    ).hexdigest()[:10]
    return "isolis-agent:%s-%s" % (profile, digest)


def profile_config(name):
    key = (name or DEFAULT_PROFILE).strip().lower()
    if key not in PROFILES:
        raise IsolisError(
            "unknown profile: %s (choose: %s)" % (name, ", ".join(sorted(PROFILES)))
        )
    return key, PROFILES[key]


def sanitize_source(source):
    value = (source or "").strip()
    if not value or value in ("local", "builtin"):
        return "local"
    return value


def source_slug(source):
    slug = re.sub(r"[^A-Za-z0-9_.-]", "-", source.rstrip("/").split("/")[-1])
    return slug.replace(".git", "") or "agent-src"


def source_dir(source):
    value = sanitize_source(source)
    if value == "local":
        return AGENT_DIR
    return os.path.join(AGENT_SRC_DIR, source_slug(value))


def resolve_agent_dir(source):
    source = sanitize_source(source)
    if source == "local":
        return AGENT_DIR
    target = source_dir(source)
    os.makedirs(AGENT_SRC_DIR, exist_ok=True)
    if os.path.isdir(os.path.join(target, ".git")):
        print("updating agent source %s ..." % source)
        proc = subprocess.run(
            ["git", "-C", target, "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise IsolisError(
                "cannot update agent source %s: %s"
                % (source, detail.splitlines()[-1] if detail else "git pull failed")
            )
    else:
        print("cloning agent source %s ..." % source)
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", source, target],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise IsolisError(
                "cannot clone agent source %s: %s"
                % (source, detail.splitlines()[-1] if detail else "git failed")
            )
    if not os.path.isfile(os.path.join(target, "Dockerfile")):
        raise IsolisError("agent source %s has no Dockerfile" % source)
    return target


def start_container(name, container, port, llm, profile="tooled",
                    agent_dir=None, source="local"):
    key, cfg = profile_config(profile)
    tag = image_tag(key, sanitize_source(source))
    args = [
        "run",
        "-d",
        "--name",
        container,
        "--network",
        NETWORK,
        "--network-alias",
        name,
        "--tmpfs",
        "/tmp:rw,exec,nosuid,size=" + cfg["tmp"],
        "--tmpfs",
        "/work:rw,exec,nosuid,mode=1777,size=" + cfg["workspace"],
        "--tmpfs",
        "/dev/shm:rw,nosuid,nodev,size=" + cfg["shm"],
        "--pids-limit",
        cfg["pids"],
        "--memory",
        cfg["memory"],
        "--cpus",
        cfg["cpus"],
        "--security-opt",
        "no-new-privileges:true",
        "--cap-drop",
        "ALL",
    ]
    if cfg["read_only"]:
        args += ["--read-only"]
    for cap in cfg["caps"]:
        args += ["--cap-add", cap]
    args += [
        "-p",
        "127.0.0.1:%d:%d" % (port, CONTAINER_PORT),
        "--user",
        cfg["user"],
        "-e",
        "HOME=/tmp",
        "-e",
        "LANG=C.UTF-8",
        "-e",
        "LC_ALL=C.UTF-8",
        "-e",
        "PYTHONIOENCODING=utf-8",
        "-e",
        ANDROID_PATH_ENV,
        "-e",
        "TIKTOKEN_CACHE_DIR=/tmp",
    ]
    for env_name, env_value in sorted(
        container_env(name, port, llm, key, cfg, source).items()
    ):
        args += ["-e", "%s=%s" % (env_name, env_value)]
    args.append(tag)
    run_docker(args, timeout=300)


def verify_agent(port):
    info = get_info(port)
    if info is None:
        return None
    if info.get("via") != "mc-direct" or info.get("injected") is not True:
        return None
    return info


def entry_record(name, container, port, info, llm, profile, source, created_at,
                 mode="docker"):
    return {
        "name": name,
        "container": container,
        "port": port,
        "hostname": info.get("hostname", ""),
        "model": info.get("model", llm["model"]),
        "profile": profile,
        "source": source,
        "mode": mode,
        "created_at": created_at,
    }


def cmd_init(name_arg=None, force_image=False, llm=None, profile=None, source=None):
    if llm is None:
        llm = detect_llm()
        print(
            "llm: %s via mc-direct (key ok len %d prefix %s)"
            % (llm["model"], llm["key_len"], llm["key_prefix"])
        )
    mode = detect_mode()
    rt = runtime_for(mode)
    if mode == "process":
        print(
            "runtime: process mode (docker CLI or daemon unavailable; "
            "agent/server.py runs on the host with the same env whitelist)"
        )

    raw = name_arg if name_arg is not None else DEFAULT_NAME
    name = sanitize_name(raw)
    if not name:
        name = DEFAULT_NAME
    announce_name(raw, name)

    key, cfg = profile_config(profile or profile_from_env())
    src = sanitize_source(source or source_from_env())
    agent_dir = resolve_agent_dir(src)
    print("profile: %s (install %s, memory %s, cpus %s)"
          % (key, "on" if cfg["install"] else "off", cfg["memory"], cfg["cpus"]))
    print("agent source: %s (%s)" % (src, agent_dir))

    state = load_state()
    container = "isolis-" + name

    tag = rt.ensure_image(key, src, agent_dir, force_image)
    rt.ensure_network()

    existing = rt.status(name)
    record = state["agents"].get(name) or {}
    port = rt.port_of(name)
    if not isinstance(port, int):
        port = record.get("port")
    if not isinstance(port, int):
        state["agents"].pop(name, None)
        port = allocate_port(state)
    same = (record.get("profile") == key and record.get("source") == src)

    def remember(verb, stamp=None):
        state["current"] = name
        state["agents"][name] = entry_record(
            name, container, port, info, llm, key, src,
            stamp or record.get("created_at", now_iso()), mode,
        )
        save_state(state)
        print("agent '%s' %s on 127.0.0.1:%d" % (name, verb, port))

    if existing == "running" and same:
        info = verify_agent(port)
        if info is not None:
            remember("reused")
            return
    if existing == "stopped" and same and rt.start_existing(name):
        if wait_health(port, 45):
            info = verify_agent(port)
            if info is not None:
                remember("started")
                return
    if existing in ("running", "stopped"):
        if not same:
            print("profile or source changed, recreating '%s'" % name)
        rt.stop(name)
        state["agents"].pop(name, None)

    state["agents"].pop(name, None)
    port = allocate_port(state)
    print("starting agent '%s' on 127.0.0.1:%d ..." % (name, port))
    rt.start(name, port, llm, key, src)

    if not wait_health(port, 45):
        line = rt.logs(name)
        rt.stop(name)
        raise IsolisError(
            "agent '%s' failed health check%s"
            % (name, (": " + line) if line else "")
        )

    info = verify_agent(port)
    if info is None:
        line = rt.logs(name)
        rt.stop(name)
        raise IsolisError(
            "agent '%s' is not reporting via=mc-direct%s"
            % (name, (": " + line) if line else "")
        )

    remember("ready", now_iso())
    print("agent '%s' image: %s (runtime %s)" % (name, tag, mode))


def cmd_use(name_arg):
    raw = name_arg or ""
    name = sanitize_name(raw)
    if not name:
        raise IsolisError("usage: use <name>")
    announce_name(raw, name)
    state = load_state()
    if name not in state["agents"]:
        raise IsolisError("unknown agent: %s (run: status)" % name)
    state["current"] = name
    save_state(state)
    print("current agent: %s" % name)


def isolation_summary(profile):
    key, cfg = profile_config(profile)
    return (
        "isolation[%s]: root %s, tmpfs /tmp %s, workspace /work %s, "
        "pids %s, memory %s, cpus %s, user %s, cap-drop ALL, caps %s, "
        "no-new-privileges, install %s, no host mounts, no docker socket"
        % (
            key,
            "read-only" if cfg["read_only"] else "writable",
            cfg["tmp"],
            cfg["workspace"],
            cfg["pids"],
            cfg["memory"],
            cfg["cpus"],
            cfg["user"],
            ",".join(cfg["caps"]) or "none",
            "on" if cfg["install"] else "off",
        )
    )


def _agent_runtime(entry):
    """Runtime object for a state entry, degrading when the record is stale."""
    return _runtime_and_mode(entry)[0]


def _agent_status(entry, name):
    state = _runtime_and_mode(entry)[0].status(name)
    return state if state in ("running", "stopped") else "missing"


def cmd_status(json_mode=False):
    _notice_reset()
    state = load_state()
    agents = state["agents"]
    current = state["current"]
    if not agents:
        if json_mode:
            print(json.dumps({"ok": True, "current": current, "agents": []}, ensure_ascii=False, indent=2))
            return
        print("no agents (run: init)")
        print(isolation_summary(profile_from_env()))
        return
    for name in sorted(agents):
        entry = agents[name]
        status = _agent_status(entry, name)
        marker = "*" if name == current else " "
        print(
            "%s %-20s %-8s 127.0.0.1:%s  %-10s %-10s %s"
            % (
                marker,
                name,
                status,
                entry.get("port", "?"),
                entry.get("profile", DEFAULT_PROFILE),
                entry.get("mode", "docker"),
                entry.get("model", "?"),
            )
        )
    current_entry = agents.get(current) or {}
    if json_mode:
        rows = []
        for name in sorted(agents):
            entry = agents[name]
            rows.append(
                {
                    "name": name,
                    "current": name == current,
                    "container": entry.get("container") or ("isolis-" + name),
                    "status": _agent_status(entry, name),
                    "port": entry.get("port"),
                    "profile": entry.get("profile", DEFAULT_PROFILE),
                    "mode": entry.get("mode", "docker"),
                    "source": entry.get("source"),
                    "model": entry.get("model"),
                    "created_at": entry.get("created_at"),
                    "isolation": PROFILES.get(entry.get("profile", DEFAULT_PROFILE), {}),
                }
            )
        payload = {
            "ok": True,
            "current": current,
            "agents": rows,
            "context_tokens": context_tokens(),
            "context_hard_limit": CONTEXT_HARD_LIMIT,
            "max_turns": max_turns(),
            "max_message_chars": max_message_chars(),
            "default_profile": DEFAULT_PROFILE,
        }
        print(json.dumps(json_sanitize(payload), ensure_ascii=False, indent=2))
        return
    print(isolation_summary(current_entry.get("profile", profile_from_env())))
    if (current_entry.get("mode") or detect_mode()) == "process":
        print(
            "runtime: process mode (agent/server.py on the host; the "
            "isolation line above describes the container profile it is "
            "configured for, not an active container)"
        )
    print(
        "context budget: %d tokens of %d upstream max, turns %d, message cap %d chars"
        % (context_tokens(), CONTEXT_HARD_LIMIT, max_turns(), max_message_chars())
    )
    source = current_entry.get("source")
    if source:
        print("agent source: %s" % source)


def cmd_stop(name_arg=None):
    _notice_reset()
    state = load_state()
    raw = name_arg
    if raw is None:
        name = state.get("current")
        if not name:
            raise IsolisError("no current agent (run: status)")
    else:
        name = sanitize_name(raw)
        if not name:
            raise IsolisError("usage: stop [name]")
        announce_name(raw, name)
    entry = state["agents"].get(name)
    if entry is None:
        raise IsolisError("unknown agent: %s (run: status)" % name)
    _runtime_and_mode(entry)[0].stop(name)
    del state["agents"][name]
    if state.get("current") == name:
        remaining = sorted(state["agents"])
        state["current"] = remaining[0] if remaining else None
    save_state(state)
    print("agent '%s' stopped" % name)



def cmd_doctor():
    ok = True
    print("isolis doctor")

    cli = docker_cli_version()
    if cli:
        print("  [ok]   %s" % cli)
    else:
        print("  [warn] docker CLI not found (process mode will be used)")

    mode = detect_mode()
    if mode == "process":
        print("  [ok]   runtime: process mode (agent/server.py on the host)")

    daemon = docker_available()
    if daemon:
        print("  [ok]   daemon reachable")
    elif mode == "process":
        print("  [warn] daemon not running (process mode will be used)")
    else:
        print("  [warn] daemon not running (init will start it)")

    hub_ok, hub_status = reachable(HUB_PROBE)
    config = read_daemon_config()
    mirrors = (config or {}).get("registry-mirrors")
    if hub_ok:
        print("  [ok]   Docker Hub reachable (HTTP %s)" % hub_status)
    elif isinstance(mirrors, list) and mirrors:
        print("  [ok]   Docker Hub blocked, mirror configured: %s" % mirrors[0])
    elif mode == "process":
        print("  [ok]   registry: not needed (process mode builds no image)")
    else:
        print("  [warn] Docker Hub blocked and no mirror configured")
        print("         init will try to add %s" % DEFAULT_MIRROR)

    base = os.environ.get("MCAI_LLM_BASE_URL", "").strip()
    key = os.environ.get("MCAI_LLM_API_KEY", "").strip()
    if base and key:
        print("  [ok]   MCAI_LLM_BASE_URL and MCAI_LLM_API_KEY present")
        print("  [ok]   llm host: %s" % urllib.parse.urlsplit(base).netloc)
        try:
            detected = detect_llm()
            print("  [ok]   model: %s" % detected["model"])
            print("  [ok]   key: ok len %d prefix %s"
                  % (detected["key_len"], detected["key_prefix"]))
        except IsolisError as exc:
            print("  [fail] llm detection failed: %s" % exc)
            ok = False
    else:
        print("  [fail] MonkeyCode LLM was not injected: "
              "MCAI_LLM_BASE_URL and MCAI_LLM_API_KEY are required "
              "(run doctor to see the checklist)")
        ok = False

    if os.access(AGENT_DIR, os.R_OK):
        print("  [ok]   agent sources: %s" % AGENT_DIR)
    else:
        print("  [fail] agent sources missing: %s" % AGENT_DIR)
        ok = False

    try:
        key, cfg = profile_config(profile_from_env())
        print("  [ok]   profile: %s (install %s)"
              % (key, "on" if cfg["install"] else "off"))
    except IsolisError as exc:
        print("  [fail] %s" % exc)
        ok = False
        key, cfg = DEFAULT_PROFILE, PROFILES[DEFAULT_PROFILE]

    try:
        budget = context_tokens()
        if budget:
            print(
                "  [ok]   context budget: %d tokens of %d upstream max (auto compression on)"
                % (budget, CONTEXT_HARD_LIMIT)
            )
        else:
            print("  [warn] context budget: 0 (auto compression off, turn cap only)")
        print(
            "  [ok]   limits: turns %d, message %d chars"
            % (max_turns(), max_message_chars())
        )
    except IsolisError as exc:
        print("  [fail] %s" % exc)
        ok = False

    src = sanitize_source(source_from_env())
    if src == "local":
        print("  [ok]   agent source: local (%s)" % AGENT_DIR)
    else:
        print("  [ok]   agent source: %s (cloned on init)" % src)

    if daemon:
        tag = image_tag(key, src)
        if image_exists(tag):
            print("  [ok]   image %s present" % tag)
        else:
            print("  [warn] image %s missing (init will build it)" % tag)
        proc = run_docker(["network", "inspect", NETWORK], timeout=20, check=False)
        if proc.returncode == 0:
            print("  [ok]   network %s present" % NETWORK)
        else:
            print("  [warn] network %s missing (init will create it)" % NETWORK)

    try:
        state = load_state()
        print("  [ok]   state file readable (%d agents, current: %s)"
              % (len(state["agents"]), state["current"]))
    except IsolisError as exc:
        print("  [fail] %s" % exc)
        ok = False

    print("result: %s" % ("ready" if ok else "not ready (fix the [fail] lines)"))
    return ok


def cmd_setup(profile=None, source=None):
    llm = detect_llm()
    print("llm: %s via mc-direct (key ok len %d prefix %s)"
          % (llm["model"], llm["key_len"], llm["key_prefix"]))
    mode = detect_mode()
    rt = runtime_for(mode)
    key, cfg = profile_config(profile or profile_from_env())
    src = sanitize_source(source or source_from_env())
    agent_dir = resolve_agent_dir(src)
    if mode == "docker":
        action, detail = ensure_registry_mirror()
        print("registry: %s (%s)" % (action, detail))
        daemon_state = ensure_docker(auto_start=True)
        if daemon_state == "started":
            print("daemon: started (log: %s)" % DOCKERD_LOG)
        else:
            print("daemon: already running")
    else:
        print("docker CLI or daemon unavailable; setup continues in process mode")
        print("registry: skipped (process mode builds no image)")
        print("daemon: skipped (process mode runs agent/server.py on the host)")
    tag = rt.ensure_image(key, src, agent_dir, False)
    rt.ensure_network()
    print("profile: %s" % key)
    print("agent source: %s (%s)" % (src, agent_dir))
    print("image: %s" % tag)
    print("network: %s" % (NETWORK if mode == "docker" else "n/a (process mode)"))
    print("setup complete (run: init)")


def cmd_rebuild(profile=None, source=None):
    _notice_reset()
    llm = detect_llm()
    state = load_state()
    name = state.get("current")
    if not name:
        name = sanitize_name(DEFAULT_NAME) or DEFAULT_NAME
        print("no current agent, rebuilding '%s'" % name)
    record = state["agents"].get(name) or {}
    if record.get("mode"):
        rt, mode = _runtime_and_mode(record)
    else:
        mode = detect_mode()
        rt = runtime_for(mode)
    key = profile or record.get("profile") or profile_from_env()
    src = source or record.get("source") or source_from_env()
    agent_dir = resolve_agent_dir(src)
    if mode == "docker":
        build_image(True, key, src, agent_dir)
        ensure_network()
    else:
        print("process mode: no image to rebuild, recreating the agent process")
    rt.stop(name)
    if name in state["agents"]:
        del state["agents"][name]
    if state.get("current") == name:
        state["current"] = None
    save_state(state)
    cmd_init(name, force_image=False, llm=llm, profile=key, source=src)
    print("rebuild complete: %s (%s, profile %s)" % (name, llm["model"], key))


def chat_request(port, message, timeout=300):
    status, body = http_json(
        "POST",
        "http://127.0.0.1:%d/chat" % port,
        payload={"message": message},
        timeout=timeout,
    )
    if status >= 400 or not isinstance(body, dict) or body.get("ok") is not True:
        error = body.get("error") if isinstance(body, dict) else None
        if not error:
            error = "HTTP %d" % status
        raise IsolisError(str(error))
    reply = body.get("reply")
    if not isinstance(reply, str):
        raise IsolisError("agent returned an empty reply")
    return reply


# ------------------------------------------------------- files, batch, fan-out


def http_get_blob(port, path, timeout=120):
    url = "http://127.0.0.1:%d%s" % (port, path)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        raise IsolisError("agent GET %s failed (%s) %s" % (path, exc.code, detail))
    except Exception as exc:
        raise IsolisError("agent GET %s failed: %s" % (path, exc))


def http_post_blob(port, path, blob, content_type="application/gzip", timeout=300):
    url = "http://127.0.0.1:%d%s" % (port, path)
    request = urllib.request.Request(
        url, data=blob, method="POST",
        headers={"Content-Type": content_type, "Content-Length": str(len(blob))},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        raise IsolisError("agent POST %s failed (%s) %s" % (path, exc.code, detail))
    except Exception as exc:
        raise IsolisError("agent POST %s failed: %s" % (path, exc))
    try:
        return json.loads(body)
    except ValueError:
        return {}


def resolve_agent_port(name=None):
    """Current (or named) agent port; the agent must exist in state."""
    state = load_state()
    key = sanitize_name(name or state.get("current") or DEFAULT_NAME) or DEFAULT_NAME
    entry = state["agents"].get(key)
    port = (entry or {}).get("port")
    if not isinstance(port, int):
        raise IsolisError("agent '%s' is not initialized (run: init)" % key)
    return key, port


def export_dir():
    path = os.path.join(STATE_DIR, "exports")
    os.makedirs(path, exist_ok=True)
    return path


def cmd_export(args):
    for token in args:
        if token.startswith("--"):
            raise IsolisError("unknown option: %s" % token)
    name = None
    target = None
    if len(args) > 2:
        raise IsolisError("usage: export [name] [path]")
    if len(args) == 1:
        token = args[0]
        if "/" in token or token.endswith(".tgz") or token.endswith(".tar.gz"):
            target = token
        else:
            name = token
    elif len(args) == 2:
        name, target = args[0], args[1]
    key, port = resolve_agent_port(name)
    blob = http_get_blob(port, "/files")
    if not target:
        stamp = now_iso().replace(":", "").replace("-", "")
        target = os.path.join(export_dir(), "%s-work-%s.tgz" % (key, stamp))
    with open(target, "wb") as handle:
        handle.write(blob)
    print("exported %s workspace: %d bytes -> %s" % (key, len(blob), target))


def cmd_import(args):
    positional = [token for token in args if not token.startswith("--")]
    if not positional:
        raise IsolisError("usage: import <file.tgz> [name]")
    source = positional[0]
    name = positional[1] if len(positional) > 1 else None
    if not os.path.isfile(source):
        raise IsolisError("file not found: %s" % source)
    key, port = resolve_agent_port(name)
    with open(source, "rb") as handle:
        blob = handle.read()
    payload = http_post_blob(port, "/files", blob)
    print(
        "imported %d archive members into %s workspace (%d bytes)"
        % (payload.get("files", 0), key, len(blob))
    )


def batch_out_dir(option=None):
    path = option or os.path.join(STATE_DIR, "batch")
    os.makedirs(path, exist_ok=True)
    return path


def cmd_batch(args):
    positional = []
    out = None
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--out":
            if index + 1 >= len(args):
                raise IsolisError("--out requires a directory")
            out = args[index + 1]
            index += 2
            continue
        if token.startswith("--"):
            raise IsolisError("unknown option: %s" % token)
        positional.append(token)
        index += 1
    if not positional:
        raise IsolisError("usage: batch <tasks-file> [--out DIR]")
    path = positional[0]
    if not os.path.isfile(path):
        raise IsolisError("file not found: %s" % path)
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name = None
            if "|" in line:
                name, line = line.split("|", 1)
                name = sanitize_name(name) or None
            message = line.strip()
            if not message:
                continue
            row = {"line": number, "ts": now_iso(), "name": name or "current", "message": message}
            try:
                try:
                    key, port = resolve_agent_port(name)
                except IsolisError:
                    if not name:
                        raise
                    key, port = resolve_agent_port(None)  # unknown name -> current agent
                row["agent"] = key
                row["reply"] = chat_request(port, message)
            except Exception as exc:
                row["error"] = safe_text(str(exc))
            rows.append(row)
            if "error" in row:
                print("line %d %s: error: %s" % (number, row["name"], row["error"]))
            else:
                head = row["reply"].splitlines()[0] if row["reply"] else ""
                print("line %d %s: %s" % (number, row["agent"], head))
    target = os.path.join(
        batch_out_dir(out), "batch-%s.jsonl" % now_iso().replace(":", "").replace("-", "")
    )
    with open(target, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(json_sanitize(row), ensure_ascii=False) + "\n")
    print("batch done: %d tasks, %d errors -> %s"
          % (len(rows), sum(1 for row in rows if "error" in row), target))


def cmd_broadcast(args):
    message = " ".join(args).strip()
    if not message:
        raise IsolisError("usage: broadcast <message>")
    state = load_state()
    if not state["agents"]:
        raise IsolisError("no agents (run: init)")
    delivered = 0
    for name in sorted(state["agents"]):
        entry = state["agents"][name]
        port = entry.get("port")
        if not isinstance(port, int):
            print("%s: skipped (no port)" % name)
            continue
        try:
            reply = chat_request(port, message)
        except Exception as exc:
            print("%s: error: %s" % (name, safe_text(str(exc))))
            continue
        delivered += 1
        print("== %s (127.0.0.1:%s, %s)" % (name, port, entry.get("profile", DEFAULT_PROFILE)))
        print(safe_text(reply))
    print("broadcast: %d/%d agents answered" % (delivered, len(state["agents"])))


def transcript_dir():
    path = os.path.join(STATE_DIR, "transcripts")
    os.makedirs(path, exist_ok=True)
    return path


def save_transcript(name, pairs, target=None):
    if not pairs:
        print("nothing to save (no completed turns)")
        return None
    if not target:
        stamp = now_iso().replace(":", "").replace("-", "")
        target = os.path.join(transcript_dir(), "%s-%s.md" % (name, stamp))
    lines = ["# isolis chat %s" % name, "", "- saved: %s" % now_iso(), ""]
    for question, answer in pairs:
        lines.append("## you")
        lines.append("")
        lines.append(question)
        lines.append("")
        lines.append("## isolis")
        lines.append("")
        lines.append(answer)
        lines.append("")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(safe_text("\n".join(lines)))
    print("transcript saved: %s (%d turns)" % (target, len(pairs)))
    return target


def ensure_chat_agent():
    _notice_reset()
    llm = detect_llm()
    state = load_state()
    name = state.get("current") or DEFAULT_NAME
    name = sanitize_name(name) or DEFAULT_NAME
    entry = state["agents"].get(name)
    rt = _agent_runtime(entry)
    prof = (entry or {}).get("profile")
    src = (entry or {}).get("source")

    def reinit():
        cmd_init(name, llm=llm, profile=prof, source=src)

    if entry is None or rt.status(name) != "running":
        reinit()
        state = load_state()
        entry = state["agents"].get(name)
        if entry is None:
            raise IsolisError("agent '%s' is unavailable (run: init %s)" % (name, name))
        rt = _agent_runtime(entry)
    port = entry.get("port")
    if not isinstance(port, int):
        reinit()
        state = load_state()
        entry = state["agents"].get(name) or {}
        port = entry.get("port")
        rt = _agent_runtime(entry)
        if not isinstance(port, int):
            raise IsolisError("agent '%s' has no port (run: init %s)" % (name, name))
    if not wait_health(port, 10):
        print("agent '%s' is unhealthy, recreating ..." % name)
        rt.stop(name)
        if name in state["agents"]:
            del state["agents"][name]
        if state.get("current") == name:
            state["current"] = None
        save_state(state)
        reinit()
        state = load_state()
        entry = state["agents"].get(name) or {}
        port = entry.get("port")
        if not isinstance(port, int):
            raise IsolisError("agent '%s' could not be recreated" % name)
    info = get_info(port)
    if info is None:
        raise IsolisError("agent '%s' /info is unavailable" % name)
    if info.get("via") != "mc-direct":
        raise IsolisError(
            "agent '%s' is not using mc-direct (via=%s)"
            % (name, info.get("via"))
        )
    print(
        "chat: %s | profile %s | hostname %s | model %s | llm_host %s | via %s"
        % (
            name,
            info.get("profile", "?"),
            info.get("hostname", "?"),
            info.get("model", llm["model"]),
            info.get("llm_host", ""),
            info.get("via"),
        )
    )
    tools = info.get("tools") or []
    if tools:
        print("tools: %s" % ", ".join(tools))
    return name, port


def chat_loop(port, name=None):
    print("type /back to return, /quit to exit, /help for help")
    pairs = []
    while True:
        try:
            line = safe_text(input("you> "))
        except (EOFError, KeyboardInterrupt):
            print()
            return "back"
        text = line.strip()
        if not text:
            continue
        command = text.lower()
        if command == "/back":
            return "back"
        if command in ("/quit", "/exit"):
            return "quit"
        if command == "/help":
            print("text: send to the agent")
            print("/export [path]: download the agent workspace as tar.gz")
            print("/import <file>: upload a tar.gz into the agent workspace")
            print("/save [path]: save this chat transcript as markdown")
            print("/back: return to the isolis prompt")
            print("/quit: exit")
            print("/help: show this help")
            continue
        if command == "/export" or command.startswith("/export "):
            target = text[len("/export"):].strip() or None
            try:
                cmd_export(([name] if name else []) + ([target] if target else []))
            except IsolisError as exc:
                print("error: %s" % exc)
            continue
        if command.startswith("/import "):
            source = text[len("/import"):].strip()
            args = [source] + ([name] if name else [])
            try:
                cmd_import(args)
            except IsolisError as exc:
                print("error: %s" % exc)
            continue
        if command == "/save" or command.startswith("/save "):
            target = text[len("/save"):].strip() or None
            try:
                save_transcript(name or DEFAULT_NAME, pairs, target)
            except OSError as exc:
                print("error: %s" % exc)
            continue
        try:
            reply = chat_request(port, text)
            print(safe_text(reply))
            pairs.append((text, safe_text(reply)))
        except KeyboardInterrupt:
            print()
            return "back"
        except Exception as err:
            print("error: %s" % safe_text(str(err)))
        except IsolisError as exc:
            print("error: %s" % exc)


def cmd_chat():
    name, port = ensure_chat_agent()
    return chat_loop(port)


HELP_TEXT = """isolis commands:
  setup         prepare Docker, mirror, image and network
  doctor        check environment and report problems
  init [name]   build and start an isolated agent
  chat          chat with the current agent
  use <name>    switch the current agent
  status        list agents and isolation summary
  rebuild       rebuild the image and current agent
  stop [name]   remove an agent container
  profiles      list capability profiles
  export [name] [path]   download an agent workspace as tar.gz
  import <file.tgz> [name]   upload a tar.gz into an agent workspace
  batch <tasks-file> [--out DIR]   run tasks unattended, results as JSONL
  broadcast <message>   send one message to every running agent
  status --json machine-readable status
  help          show this help
  quit/exit     leave isolis

options (init, setup, rebuild):
  --profile strict|tooled   sandbox capability level (default tooled)
  --source <local|git-url>  agent source (default local)
  --model <name>            override the injected model

environment:
  ISOLIS_PROFILE, ISOLIS_AGENT_SOURCE, ISOLIS_MODEL, ISOLIS_DEFAULT_MODEL
  ISOLIS_CONTEXT_TOKENS    token budget before memory compression (0 = off)
  ISOLIS_MAX_MESSAGE_CHARS per-message character cap
"""


def parse_options(args):
    opts = {"profile": None, "source": None, "model": None}
    positional = []
    index = 0
    while index < len(args):
        token = args[index]
        if token in ("--profile", "--source", "--model"):
            if index + 1 >= len(args):
                raise IsolisError("%s requires a value" % token)
            opts[token.lstrip("-")] = args[index + 1]
            index += 2
            continue
        if token.startswith("--"):
            raise IsolisError("unknown option: %s" % token)
        positional.append(token)
        index += 1
    return opts, positional


def cmd_profiles():
    print("capability profiles:")
    for name in sorted(PROFILES):
        cfg = PROFILES[name]
        print(
            "  %-8s install %-3s memory %-4s cpus %-3s pids %-4s user %s"
            % (
                name,
                "on" if cfg["install"] else "off",
                cfg["memory"],
                cfg["cpus"],
                cfg["pids"],
                cfg["user"],
            )
        )
    print("default: %s" % DEFAULT_PROFILE)


def apply_model_override(opts):
    if opts.get("model"):
        os.environ["ISOLIS_MODEL"] = opts["model"]


def dispatch(command, args):
    if command == "setup":
        opts, _ = parse_options(args)
        apply_model_override(opts)
        cmd_setup(opts["profile"], opts["source"])
        return "continue"
    if command == "doctor":
        return "ok" if cmd_doctor() else "fail"
    if command == "init":
        opts, positional = parse_options(args)
        apply_model_override(opts)
        cmd_init(
            positional[0] if positional else None,
            profile=opts["profile"],
            source=opts["source"],
        )
        return "continue"
    if command == "chat":
        return cmd_chat()
    if command == "export":
        cmd_export(args)
        return "continue"
    if command == "import":
        cmd_import(args)
        return "continue"
    if command == "batch":
        cmd_batch(args)
        return "continue"
    if command == "broadcast":
        cmd_broadcast(args)
        return "continue"
    if command == "use":
        cmd_use(args[0] if args else None)
        return "continue"
    if command == "status":
        cmd_status(json_mode=(args == ["--json"]))
        return "continue"
    if command == "rebuild":
        opts, _ = parse_options(args)
        apply_model_override(opts)
        cmd_rebuild(opts["profile"], opts["source"])
        return "continue"
    if command == "stop":
        cmd_stop(args[0] if args else None)
        return "continue"
    if command == "profiles":
        cmd_profiles()
        return "ok"
    if command == "help":
        sys.stdout.write(HELP_TEXT)
        return "ok"
    if command in ("quit", "exit"):
        return "quit"
    raise IsolisError("unknown command: %s (run: help)" % command)


def repl():
    print("isolis - isolated LangChain agents (type: help)")
    while True:
        try:
            line = safe_text(input("isolis> "))
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        text = line.strip()
        if not text:
            continue
        parts = text.split()
        command = parts[0].lower()
        args = parts[1:]
        try:
            result = dispatch(command, args)
        except IsolisError as exc:
            print("error: %s" % exc)
            continue
        except KeyboardInterrupt:
            print()
            return 0
        except Exception as exc:
            print("error: %s" % exc)
            continue
        if result == "quit":
            return 0
        if result == "fail":
            continue


def main(argv):
    configure_stdio()
    if len(argv) <= 1:
        return repl()
    command = argv[1].lower()
    args = argv[2:]
    if command in ("quit", "exit"):
        print("error: quit is only available in the interactive TUI")
        return 1
    try:
        result = dispatch(command, args)
    except IsolisError as exc:
        print("error: %s" % exc)
        return 1
    except KeyboardInterrupt:
        return 1
    except Exception as exc:
        print("error: %s" % exc)
        return 1
    if result == "fail":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
