#!/usr/bin/env python3
"""Isolis: host TUI that runs isolated LangChain agents in Docker."""

import hashlib
import http.client
import json
import os
import re
import shutil
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
DEFAULT_MODEL = "monkeycode-pro/gpt-5.6-terra"
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
    try:
        proc = run_docker(["info", "--format", "{{.ServerVersion}}"], timeout=20, check=False)
    except IsolisError:
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


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
            "MCAI_LLM_API_KEY are required"
        )
    status, body = http_json("GET", models_url(base), timeout=20, token=key)
    if status in (401, 403):
        raise IsolisError(
            "MonkeyCode LLM rejected the injected key (HTTP %d); "
            "do not switch gateways" % status
        )
    if status not in (200, 404, 405):
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
        "ISOLIS_AGENT_NAME=" + name,
        "-e",
        "ISOLIS_PROFILE=" + key,
        "-e",
        "ISOLIS_INSTALL=" + ("1" if cfg["install"] else "0"),
        "-e",
        "ISOLIS_SOURCE=" + sanitize_source(source),
        "-e",
        "ISOLIS_CONTEXT_TOKENS=" + str(context_tokens()),
        "-e",
        "ISOLIS_CONTEXT_HARD_LIMIT=" + str(CONTEXT_HARD_LIMIT),
        "-e",
        "ISOLIS_MAX_MESSAGE_CHARS=" + str(max_message_chars()),
        "-e",
        "ISOLIS_MAX_TURNS=" + str(max_turns()),
        "-e",
        "ISOLIS_ARCHIVE_MAX=" + str(_env_int("ISOLIS_ARCHIVE_MAX", 4000)),
        "-e",
        "MCAI_LLM_BASE_URL",
        "-e",
        "MCAI_LLM_API_KEY",
        "-e",
        "MCAI_LLM_MODEL=" + llm["model"],
        "-e",
        "ISOLIS_FALLBACK_MODEL=" + (
            os.environ.get("ISOLIS_DEFAULT_MODEL", "").strip() or DEFAULT_MODEL
        ),
        tag,
    ]
    run_docker(args, timeout=300)


def verify_agent(port):
    info = get_info(port)
    if info is None:
        return None
    if info.get("via") != "mc-direct" or info.get("injected") is not True:
        return None
    return info


def entry_record(name, container, port, info, llm, profile, source, created_at):
    return {
        "name": name,
        "container": container,
        "port": port,
        "hostname": info.get("hostname", ""),
        "model": info.get("model", llm["model"]),
        "profile": profile,
        "source": source,
        "created_at": created_at,
    }


def cmd_init(name_arg=None, force_image=False, llm=None, profile=None, source=None):
    if llm is None:
        llm = detect_llm()
        print(
            "llm: %s via mc-direct (key ok len %d prefix %s)"
            % (llm["model"], llm["key_len"], llm["key_prefix"])
        )
    ensure_docker()

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

    tag = build_image(force_image, key, src, agent_dir)
    ensure_network()

    existing = inspect_container(container)
    if existing is not None:
        record = state["agents"].get(name) or {}
        port = container_port(container)
        if not isinstance(port, int):
            port = record.get("port")
        if not isinstance(port, int):
            state["agents"].pop(name, None)
            port = allocate_port(state)
        same = (record.get("profile") == key and record.get("source") == src)
        if existing and same:
            info = verify_agent(port)
            if info is not None:
                state["current"] = name
                state["agents"][name] = entry_record(
                    name, container, port, info, llm, key, src,
                    record.get("created_at", now_iso()),
                )
                save_state(state)
                print("agent '%s' reused on 127.0.0.1:%d" % (name, port))
                return
        if same and not existing:
            run_docker(["start", container], timeout=120, check=False)
            if wait_health(port, 45):
                info = verify_agent(port)
                if info is not None:
                    state["current"] = name
                    state["agents"][name] = entry_record(
                        name, container, port, info, llm, key, src,
                        record.get("created_at", now_iso()),
                    )
                    save_state(state)
                    print("agent '%s' started on 127.0.0.1:%d" % (name, port))
                    return
        elif not same:
            print("profile or source changed, recreating '%s'" % name)
        remove_container(container)
        state["agents"].pop(name, None)

    state["agents"].pop(name, None)
    port = allocate_port(state)
    print("starting agent '%s' on 127.0.0.1:%d ..." % (name, port))
    start_container(name, container, port, llm, key, agent_dir, src)

    if not wait_health(port, 45):
        line = last_log_line(container)
        remove_container(container)
        raise IsolisError(
            "agent '%s' failed health check%s"
            % (name, (": " + line) if line else "")
        )

    info = verify_agent(port)
    if info is None:
        line = last_log_line(container)
        remove_container(container)
        raise IsolisError(
            "agent '%s' is not reporting via=mc-direct%s"
            % (name, (": " + line) if line else "")
        )

    state["current"] = name
    state["agents"][name] = entry_record(
        name, container, port, info, llm, key, src, now_iso()
    )
    save_state(state)
    print("agent '%s' ready on 127.0.0.1:%d (image %s)" % (name, port, tag))


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


def cmd_status():
    state = load_state()
    agents = state["agents"]
    current = state["current"]
    if not agents:
        print("no agents (run: init)")
        print(isolation_summary(profile_from_env()))
        return
    for name in sorted(agents):
        entry = agents[name]
        container = entry.get("container") or ("isolis-" + name)
        running = inspect_container(container)
        if running is True:
            status = "running"
        elif running is False:
            status = "stopped"
        else:
            status = "missing"
        marker = "*" if name == current else " "
        print(
            "%s %-20s %-8s 127.0.0.1:%s  %-10s %s"
            % (
                marker,
                name,
                status,
                entry.get("port", "?"),
                entry.get("profile", DEFAULT_PROFILE),
                entry.get("model", "?"),
            )
        )
    current_entry = agents.get(current) or {}
    print(isolation_summary(current_entry.get("profile", profile_from_env())))
    print(
        "context budget: %d tokens of %d upstream max, turns %d, message cap %d chars"
        % (context_tokens(), CONTEXT_HARD_LIMIT, max_turns(), max_message_chars())
    )
    source = current_entry.get("source")
    if source:
        print("agent source: %s" % source)


def cmd_stop(name_arg=None):
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
    container = entry.get("container") or ("isolis-" + name)
    remove_container(container)
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
        print("  [fail] docker CLI not found (install Docker)")
        ok = False

    daemon = docker_available()
    if daemon:
        print("  [ok]   daemon reachable")
    else:
        print("  [warn] daemon not running (init will start it)")

    hub_ok, hub_status = reachable(HUB_PROBE)
    config = read_daemon_config()
    mirrors = (config or {}).get("registry-mirrors")
    if hub_ok:
        print("  [ok]   Docker Hub reachable (HTTP %s)" % hub_status)
    elif isinstance(mirrors, list) and mirrors:
        print("  [ok]   Docker Hub blocked, mirror configured: %s" % mirrors[0])
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
              "MCAI_LLM_BASE_URL and MCAI_LLM_API_KEY are required")
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
    if not shutil.which("docker"):
        raise IsolisError(
            "docker CLI not found (install Docker, then run: setup)"
        )
    llm = detect_llm()
    print("llm: %s via mc-direct (key ok len %d prefix %s)"
          % (llm["model"], llm["key_len"], llm["key_prefix"]))
    action, detail = ensure_registry_mirror()
    print("registry: %s (%s)" % (action, detail))
    state = ensure_docker(auto_start=True)
    if state == "started":
        print("daemon: started (log: %s)" % DOCKERD_LOG)
    else:
        print("daemon: already running")
    key, cfg = profile_config(profile or profile_from_env())
    src = sanitize_source(source or source_from_env())
    agent_dir = resolve_agent_dir(src)
    tag = build_image(False, key, src, agent_dir)
    ensure_network()
    print("profile: %s" % key)
    print("agent source: %s (%s)" % (src, agent_dir))
    print("image: %s" % tag)
    print("network: %s" % NETWORK)
    print("setup complete (run: init)")


def cmd_rebuild(profile=None, source=None):
    llm = detect_llm()
    ensure_docker()
    state = load_state()
    name = state.get("current")
    if not name:
        name = sanitize_name(DEFAULT_NAME) or DEFAULT_NAME
        print("no current agent, rebuilding '%s'" % name)
    record = state["agents"].get(name) or {}
    key = profile or record.get("profile") or profile_from_env()
    src = source or record.get("source") or source_from_env()
    agent_dir = resolve_agent_dir(src)
    build_image(True, key, src, agent_dir)
    ensure_network()
    container = "isolis-" + name
    remove_container(container)
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


def ensure_chat_agent():
    llm = detect_llm()
    ensure_docker()
    state = load_state()
    name = state.get("current") or DEFAULT_NAME
    name = sanitize_name(name) or DEFAULT_NAME
    entry = state["agents"].get(name)
    container = "isolis-" + name
    running = inspect_container(container)
    prof = (entry or {}).get("profile")
    src = (entry or {}).get("source")

    def reinit():
        cmd_init(name, llm=llm, profile=prof, source=src)

    if entry is None or running is None:
        reinit()
        state = load_state()
        entry = state["agents"].get(name)
        if entry is None:
            raise IsolisError("agent '%s' is unavailable (run: init %s)" % (name, name))
    elif not running:
        reinit()
        state = load_state()
        entry = state["agents"].get(name)
        if entry is None:
            raise IsolisError("agent '%s' is unavailable (run: init %s)" % (name, name))
    port = entry.get("port")
    if not isinstance(port, int):
        reinit()
        state = load_state()
        entry = state["agents"].get(name) or {}
        port = entry.get("port")
        if not isinstance(port, int):
            raise IsolisError("agent '%s' has no port (run: init %s)" % (name, name))
    if not wait_health(port, 10):
        print("agent '%s' is unhealthy, recreating ..." % name)
        remove_container(container)
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


def chat_loop(port):
    print("type /back to return, /quit to exit, /help for help")
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
            print("/back: return to the isolis prompt")
            print("/quit: exit")
            print("/help: show this help")
            continue
        try:
            reply = chat_request(port, text)
            print(safe_text(reply))
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
    if command == "use":
        cmd_use(args[0] if args else None)
        return "continue"
    if command == "status":
        cmd_status()
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
