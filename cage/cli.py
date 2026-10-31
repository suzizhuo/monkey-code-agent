"""Host TUI: Isolis command surface with CAGE_* names and dual-mode runtime."""

import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import venv
from datetime import datetime, timezone

from cage import CageError
from cage.model import detect_llm, http_json
from cage.naming import allocate_port, announce_name, sanitize_name
from cage.runtime import (
    AGENT_DIR,
    CONTEXT_HARD_LIMIT,
    HUB_PROBE,
    context_tokens,
    detect_mode,
    max_message_chars,
    max_turns,
    resolve_agent_dir,
    runtime_for,
    sanitize_source,
)
from cage.state import (
    DEFAULT_NAME,
    DEFAULT_PROFILE,
    NETWORK,
    PROFILES,
    SERVE_PORT,
    home,
    load_state,
    save_state,
    state_lock,
)


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


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def profile_config(name):
    key = (name or DEFAULT_PROFILE).strip().lower()
    if key not in PROFILES:
        raise CageError(
            "unknown profile: %s (choose: %s)" % (name, ", ".join(sorted(PROFILES)))
        )
    return key, PROFILES[key]


def profile_from_env():
    return os.environ.get("CAGE_PROFILE", "").strip() or DEFAULT_PROFILE


def source_from_env():
    return os.environ.get("CAGE_AGENT_SOURCE", "").strip() or "local"


def port_in_use(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def listening_ports():
    busy = set()
    from cage.naming import PORT_MAX, PORT_MIN

    for port in range(PORT_MIN, PORT_MAX + 1):
        if port_in_use(port):
            busy.add(port)
    return busy


def wait_health(port, seconds=45):
    deadline = time.time() + seconds
    url = "http://127.0.0.1:%d/health" % port
    while time.time() < deadline:
        try:
            status, body = http_json("GET", url, timeout=3)
            if status == 200 and body.get("ok") is True:
                return True
        except CageError:
            pass
        time.sleep(0.5)
    return False


def get_info(port, timeout=10):
    try:
        status, body = http_json(
            "GET", "http://127.0.0.1:%d/info" % port, timeout=timeout
        )
    except CageError:
        return None
    if status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
        return None
    return body


def verify_agent(port):
    info = get_info(port)
    if info is None:
        return None
    if info.get("via") != "mc-direct" or info.get("injected") is not True:
        return None
    return info


def entry_record(name, handle, port, info, llm, profile, source, mode, created_at):
    return {
        "name": name,
        "container": handle,
        "port": port,
        "hostname": info.get("hostname", ""),
        "model": info.get("model", llm.get("model") if llm else ""),
        "profile": profile,
        "source": source,
        "mode": mode,
        "created_at": created_at,
    }


def reachable(url, timeout=8):
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, response.status
    except urllib.error.HTTPError as exc:
        return True, exc.code
    except (urllib.error.URLError, ssl.SSLError, OSError):
        return False, None


def probe_registry():
    hub_ok, hub_status = reachable(HUB_PROBE)
    if hub_ok:
        return "ok", "Docker Hub reachable (HTTP %s)" % hub_status
    return "warn", "Docker Hub blocked; configure a mirror manually"


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


def _choose_port(st):
    recorded = set()
    for entry in (st.get("agents") or {}).values():
        port = entry.get("port")
        if isinstance(port, int):
            recorded.add(port)
    return allocate_port(recorded, set(), listening_ports(), {SERVE_PORT})


def cmd_init(name_arg=None, force_image=False, llm=None, profile=None, source=None):
    if llm is None:
        llm = detect_llm()
        print(
            "llm: %s via mc-direct (key ok len %d prefix %s)"
            % (llm["model"], llm["key_len"], llm["key_prefix"])
        )
    mode = detect_mode()
    rt = runtime_for(mode)

    raw = name_arg if name_arg is not None else DEFAULT_NAME
    name = sanitize_name(raw) or DEFAULT_NAME
    announce_name(raw, name)

    key, cfg = profile_config(profile or profile_from_env())
    src = sanitize_source(source or source_from_env())
    agent_dir = resolve_agent_dir(src)
    print(
        "profile: %s (install %s, memory %s, cpus %s)"
        % (key, "on" if cfg["install"] else "off", cfg["memory"], cfg["cpus"])
    )
    print("agent source: %s (%s)" % (src, agent_dir))
    print("mode: %s" % mode)

    with state_lock():
        st = load_state()

    base_image = os.environ.get("CAGE_BASE_IMAGE", "").strip() or "python:3.12-slim-bookworm"
    tag = rt.ensure_image(key, src, agent_dir, base_image, force=force_image)
    if hasattr(rt, "ensure_network"):
        rt.ensure_network()

    existing = rt.status(name)
    record = st["agents"].get(name) or {}
    port = rt.port_of(name)
    if not isinstance(port, int):
        port = record.get("port")
    same = record.get("profile") == key and record.get("source") == src
    recorded_mode = record.get("mode")
    mode_mismatch = bool(recorded_mode) and recorded_mode != mode

    if existing != "missing":
        if existing == "running" and same and not mode_mismatch and isinstance(port, int):
            info = verify_agent(port)
            if info is not None:
                with state_lock():
                    st = load_state()
                    st["current"] = name
                    st["agents"][name] = entry_record(
                        name,
                        record.get("container") or ("cage-" + name),
                        port,
                        info,
                        llm,
                        key,
                        src,
                        mode,
                        record.get("created_at", now_iso()),
                    )
                    save_state(st)
                print("agent '%s' reused on 127.0.0.1:%d" % (name, port))
                return
        if not same or mode_mismatch:
            print("profile or source changed, recreating '%s'" % name)
        rt.stop(name)
        with state_lock():
            st = load_state()
            st["agents"].pop(name, None)
            save_state(st)

    with state_lock():
        st = load_state()
        st["agents"].pop(name, None)
        port = _choose_port(st)
    print("starting agent '%s' on 127.0.0.1:%d ..." % (name, port))
    env = {
        "MCAI_LLM_MODEL": llm["model"],
        "CAGE_SOURCE": src,
        "CAGE_IMAGE": tag,
    }
    handle = rt.start(name, port, key, env)
    if not wait_health(port, 45):
        line = (rt.logs(name) or "").splitlines()[-1] if rt.logs(name) else ""
        rt.stop(name)
        raise CageError(
            "agent '%s' failed health check%s" % (name, (": " + line) if line else "")
        )
    info = verify_agent(port)
    if info is None:
        line = (rt.logs(name) or "").splitlines()[-1] if rt.logs(name) else ""
        rt.stop(name)
        raise CageError(
            "agent '%s' is not reporting via=mc-direct%s"
            % (name, (": " + line) if line else "")
        )
    with state_lock():
        st = load_state()
        st["current"] = name
        st["agents"][name] = entry_record(
            name, handle, port, info, llm, key, src, mode, now_iso()
        )
        save_state(st)
    print("agent '%s' ready on 127.0.0.1:%d (image %s)" % (name, port, tag))


def cmd_use(name_arg):
    raw = name_arg or ""
    name = sanitize_name(raw)
    if not name:
        raise CageError("usage: use <name>")
    announce_name(raw, name)
    with state_lock():
        st = load_state()
        if name not in st["agents"]:
            raise CageError("unknown agent: %s (run: status)" % name)
        st["current"] = name
        save_state(st)
    print("current agent: %s" % name)


def cmd_status():
    mode = detect_mode()
    st = load_state()
    agents = st["agents"]
    current = st["current"]
    if not agents:
        print("no agents (run: init)")
        print(isolation_summary(profile_from_env()))
        print("mode: %s" % mode)
        return
    for name in sorted(agents):
        entry = agents[name]
        recorded_mode = entry.get("mode") or mode
        status_rt = runtime_for(recorded_mode)
        status = status_rt.status(name)
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
    print("mode: %s" % mode)


def cmd_stop(name_arg=None):
    with state_lock():
        st = load_state()
        raw = name_arg
        if raw is None:
            name = st.get("current")
            if not name:
                raise CageError("no current agent (run: status)")
        else:
            name = sanitize_name(raw)
            if not name:
                raise CageError("usage: stop [name]")
            announce_name(raw, name)
        entry = st["agents"].get(name)
        if entry is None:
            raise CageError("unknown agent: %s (run: status)" % name)
        mode = entry.get("mode") or detect_mode()
        runtime_for(mode).stop(name)
        del st["agents"][name]
        if st.get("current") == name:
            remaining = sorted(st["agents"])
            st["current"] = remaining[0] if remaining else None
        save_state(st)
    print("agent '%s' stopped" % name)


def cmd_doctor():
    ok = True
    print("cage doctor")
    mode = detect_mode()
    print("  [ok]   mode: %s" % mode)

    cli = shutil.which("docker")
    if cli:
        try:
            proc = subprocess.run(
                [cli, "--version"], capture_output=True, text=True, timeout=15
            )
            if proc.returncode == 0:
                print("  [ok]   %s" % proc.stdout.strip())
            else:
                print("  [warn] docker CLI found but --version failed")
        except (OSError, subprocess.TimeoutExpired):
            print("  [warn] docker CLI found but --version failed")
    else:
        print("  [warn] docker CLI not found (process mode will be used)")

    if mode == "docker":
        print("  [ok]   daemon reachable")
    else:
        print("  [warn] docker daemon not running (init will use process mode)")

    hub_ok, hub_status = reachable(HUB_PROBE)
    if hub_ok:
        print("  [ok]   Docker Hub reachable (HTTP %s)" % hub_status)
    else:
        print("  [warn] Docker Hub blocked; configure a mirror manually")

    base = os.environ.get("MCAI_LLM_BASE_URL", "").strip()
    key = os.environ.get("MCAI_LLM_API_KEY", "").strip()
    if base and key:
        print("  [ok]   MCAI_LLM_BASE_URL and MCAI_LLM_API_KEY present")
        print("  [ok]   llm host: %s" % urllib.parse.urlsplit(base).netloc)
        try:
            detected = detect_llm()
            print("  [ok]   model: %s" % detected["model"])
            print(
                "  [ok]   key: ok len %d prefix %s"
                % (detected["key_len"], detected["key_prefix"])
            )
        except CageError as exc:
            print("  [fail] llm detection failed: %s" % exc)
            ok = False
    else:
        print(
            "  [fail] MonkeyCode LLM was not injected: "
            "MCAI_LLM_BASE_URL and MCAI_LLM_API_KEY are required"
        )
        ok = False

    if os.access(str(AGENT_DIR), os.R_OK):
        print("  [ok]   agent sources: %s" % AGENT_DIR)
    else:
        print("  [fail] agent sources missing: %s" % AGENT_DIR)
        ok = False

    venv_python = home() / "venv" / "bin" / "python"
    if venv_python.is_file():
        print("  [ok]   venv: %s" % venv_python)
    else:
        print("  [warn] venv missing (run: setup --venv)")

    if shutil.which("chromium") or shutil.which("chromium-browser"):
        print("  [ok]   Chromium present")
    else:
        print("  [warn] Chromium not on PATH (browse tool may fail until installed)")

    try:
        key, cfg = profile_config(profile_from_env())
        print(
            "  [ok]   profile: %s (install %s)"
            % (key, "on" if cfg["install"] else "off")
        )
    except CageError as exc:
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
    except CageError as exc:
        print("  [fail] %s" % exc)
        ok = False

    src = sanitize_source(source_from_env())
    if src == "local":
        print("  [ok]   agent source: local (%s)" % AGENT_DIR)
    else:
        print("  [ok]   agent source: %s (cloned on init)" % src)

    try:
        st = load_state()
        print(
            "  [ok]   state file readable (%d agents, current: %s)"
            % (len(st["agents"]), st["current"])
        )
    except CageError as exc:
        print("  [fail] %s" % exc)
        ok = False

    print("result: %s" % ("ready" if ok else "not ready (fix the [fail] lines)"))
    return ok


def _setup_venv():
    dest = home() / "venv"
    print("creating venv %s ..." % dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True).create(str(dest))
    pip = dest / "bin" / "pip"
    req = AGENT_DIR / "requirements.txt"
    if req.is_file() and pip.is_file():
        print("installing agent requirements ...")
        subprocess.run(
            [str(pip), "install", "-r", str(req)],
            check=True,
            timeout=1800,
        )


def cmd_setup(profile=None, source=None, make_venv=False):
    if make_venv:
        _setup_venv()
    if not shutil.which("docker"):
        print("docker CLI not found; setup will continue in process mode")
    llm = detect_llm()
    print(
        "llm: %s via mc-direct (key ok len %d prefix %s)"
        % (llm["model"], llm["key_len"], llm["key_prefix"])
    )
    action, detail = probe_registry()
    print("registry: %s (%s)" % (action, detail))
    mode = detect_mode()
    print("mode: %s" % mode)
    rt = runtime_for(mode)
    key, cfg = profile_config(profile or profile_from_env())
    src = sanitize_source(source or source_from_env())
    agent_dir = resolve_agent_dir(src)
    base_image = os.environ.get("CAGE_BASE_IMAGE", "").strip() or "python:3.12-slim-bookworm"
    tag = rt.ensure_image(key, src, agent_dir, base_image, force=False)
    if hasattr(rt, "ensure_network"):
        rt.ensure_network()
    print("profile: %s" % key)
    print("agent source: %s (%s)" % (src, agent_dir))
    print("image: %s" % tag)
    print("network: %s" % NETWORK)
    print("setup complete (run: init)")


def cmd_rebuild(profile=None, source=None):
    llm = detect_llm()
    mode = detect_mode()
    rt = runtime_for(mode)
    with state_lock():
        st = load_state()
        name = st.get("current")
        if not name:
            name = sanitize_name(DEFAULT_NAME) or DEFAULT_NAME
            print("no current agent, rebuilding '%s'" % name)
        record = st["agents"].get(name) or {}
        key = profile or record.get("profile") or profile_from_env()
        src = source or record.get("source") or source_from_env()
        recorded_mode = record.get("mode") or mode
        runtime_for(recorded_mode).stop(name)
        if name in st["agents"]:
            del st["agents"][name]
        if st.get("current") == name:
            st["current"] = None
        save_state(st)
    agent_dir = resolve_agent_dir(src)
    base_image = os.environ.get("CAGE_BASE_IMAGE", "").strip() or "python:3.12-slim-bookworm"
    rt.ensure_image(key, src, agent_dir, base_image, force=True)
    if hasattr(rt, "ensure_network"):
        rt.ensure_network()
    cmd_init(name, force_image=False, llm=llm, profile=key, source=src)
    print("rebuild complete: %s (%s, profile %s)" % (name, llm["model"], key))


def chat_request(port, message, timeout=120):
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
        raise CageError(str(error))
    reply = body.get("reply")
    if not isinstance(reply, str):
        raise CageError("agent returned an empty reply")
    return reply


def ensure_chat_agent():
    llm = detect_llm()
    st = load_state()
    name = st.get("current") or DEFAULT_NAME
    name = sanitize_name(name) or DEFAULT_NAME
    entry = st["agents"].get(name)
    mode = detect_mode()
    recorded_mode = (entry or {}).get("mode") or mode
    rt = runtime_for(recorded_mode)
    running = rt.status(name) if entry else "missing"
    prof = (entry or {}).get("profile")
    src = (entry or {}).get("source")

    def reinit():
        cmd_init(name, llm=llm, profile=prof, source=src)

    if entry is None or running == "missing":
        reinit()
        st = load_state()
        entry = st["agents"].get(name)
        if entry is None:
            raise CageError("agent '%s' is unavailable (run: init %s)" % (name, name))
    elif running != "running":
        reinit()
        st = load_state()
        entry = st["agents"].get(name)
        if entry is None:
            raise CageError("agent '%s' is unavailable (run: init %s)" % (name, name))
    port = entry.get("port")
    if not isinstance(port, int):
        reinit()
        st = load_state()
        entry = st["agents"].get(name) or {}
        port = entry.get("port")
        if not isinstance(port, int):
            raise CageError("agent '%s' has no port (run: init %s)" % (name, name))
    if not wait_health(port, 10):
        print("agent '%s' is unhealthy, recreating ..." % name)
        runtime_for(entry.get("mode") or mode).stop(name)
        with state_lock():
            st = load_state()
            st["agents"].pop(name, None)
            if st.get("current") == name:
                st["current"] = None
            save_state(st)
        reinit()
        st = load_state()
        entry = st["agents"].get(name) or {}
        port = entry.get("port")
        if not isinstance(port, int):
            raise CageError("agent '%s' could not be recreated" % name)
    info = get_info(port)
    if info is None:
        raise CageError("agent '%s' /info is unavailable" % name)
    if info.get("via") != "mc-direct":
        raise CageError(
            "agent '%s' is not using mc-direct (via=%s)" % (name, info.get("via"))
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
            print("/back: return to the cage prompt")
            print("/quit: exit")
            print("/help: show this help")
            continue
        try:
            reply = chat_request(port, text)
            print(safe_text(reply))
        except KeyboardInterrupt:
            print()
            return "back"
        except CageError as exc:
            print("error: %s" % exc)
        except Exception as err:
            print("error: %s" % safe_text(str(err)))


def cmd_chat():
    name, port = ensure_chat_agent()
    return chat_loop(port)


HELP_TEXT = """cage 命令:
  setup         准备镜像与网络（不写 daemon.json）
  doctor        检查环境并报告问题
  init [name]   构建并启动隔离 agent
  chat          与当前 agent 对话
  use <name>    切换当前 agent
  status        列出 agent 与隔离摘要
  rebuild       重建镜像与当前 agent
  stop [name]   停止并移除 agent
  profiles      列出能力档位
  serve         启动宿主 HTTP 接口
  help          显示本帮助
  quit/exit     离开 cage

options (init, setup, rebuild):
  --profile strict|tooled   sandbox capability level (default tooled)
  --source <local|git-url>  agent source (default local)
  --model <name>            override the injected model
  --venv                    create ~/.cage/venv and install agent deps (setup)

environment:
  CAGE_PROFILE, CAGE_AGENT_SOURCE, CAGE_MODEL, CAGE_DEFAULT_MODEL
  CAGE_CONTEXT_TOKENS    token budget before memory compression (0 = off)
  CAGE_MAX_MESSAGE_CHARS per-message character cap
"""


def parse_options(args):
    opts = {"profile": None, "source": None, "model": None, "venv": False, "risk": None}
    positional = []
    index = 0
    while index < len(args):
        token = args[index]
        if token in ("--profile", "--source", "--model", "--risk"):
            if index + 1 >= len(args):
                raise CageError("%s requires a value" % token)
            opts[token.lstrip("-")] = args[index + 1]
            index += 2
            continue
        if token == "--venv":
            opts["venv"] = True
            index += 1
            continue
        if token.startswith("--"):
            raise CageError("unknown option: %s" % token)
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
        os.environ["CAGE_MODEL"] = opts["model"]


def cmd_serve(args):
    from cage import httpapi

    port = SERVE_PORT
    env_port = os.environ.get("CAGE_SERVE_PORT", "").strip()
    if args:
        try:
            port = int(args[0])
        except ValueError:
            raise CageError("usage: serve [port]")
    elif env_port:
        try:
            port = int(env_port)
        except ValueError:
            raise CageError("CAGE_SERVE_PORT must be an integer")
    static_dir = os.environ.get("CAGE_STATIC_DIR", "").strip() or "off"
    print("serving on 127.0.0.1:%d (CORS on, static %s)" % (port, static_dir))
    return httpapi.serve(port)


def dispatch(command, args):
    if command == "setup":
        opts, _ = parse_options(args)
        apply_model_override(opts)
        cmd_setup(opts["profile"], opts["source"], make_venv=opts.get("venv"))
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
    if command == "serve":
        code = cmd_serve(args)
        return "ok" if code == 0 else "fail"
    if command == "help":
        sys.stdout.write(HELP_TEXT)
        return "ok"
    if command in ("quit", "exit"):
        return "quit"
    raise CageError("unknown command: %s (run: help)" % command)


def repl():
    print("cage - isolated LangChain agents (type: help)")
    while True:
        try:
            line = safe_text(input("cage> "))
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
        except CageError as exc:
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
    except CageError as exc:
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
