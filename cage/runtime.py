"""Docker and process runtimes, plus pure run/spawn argument builders."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from cage import CageError
from cage.naming import image_tag, source_slug
from cage.state import CONTAINER_PORT, DEFAULT_PROFILE, NETWORK, PROFILES, home

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "agent"
CONTEXT_HARD_LIMIT = 1000000
CONTEXT_DEFAULT = 900000
MESSAGE_CHARS_DEFAULT = 900000
MAX_TURNS_DEFAULT = 400
HUB_PROBE = "https://registry-1.docker.io/v2/"


def _env_int(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise CageError("%s must be an integer (got %r)" % (name, raw))
    if value < 0:
        raise CageError("%s must be zero or positive" % name)
    return value


def context_tokens():
    value = _env_int("CAGE_CONTEXT_TOKENS", CONTEXT_DEFAULT)
    if value > CONTEXT_HARD_LIMIT:
        print(
            "notice: CAGE_CONTEXT_TOKENS %d exceeds the upstream limit %d; "
            "clamping to %d" % (value, CONTEXT_HARD_LIMIT, CONTEXT_HARD_LIMIT)
        )
        return CONTEXT_HARD_LIMIT
    return value


def max_message_chars():
    return _env_int("CAGE_MAX_MESSAGE_CHARS", MESSAGE_CHARS_DEFAULT)


def max_turns():
    return _env_int("CAGE_MAX_TURNS", MAX_TURNS_DEFAULT)


def docker_bin():
    path = shutil.which("docker")
    if not path:
        raise CageError("docker CLI not found (install Docker and retry)")
    return path


def run_docker(args, timeout=60, check=True):
    cmd = [docker_bin()] + list(args)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CageError("docker command timed out: docker " + " ".join(args))
    except OSError as exc:
        raise CageError("cannot run docker: %s" % exc)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        detail = detail.splitlines()[-1] if detail else "exit %d" % proc.returncode
        raise CageError("docker %s failed: %s" % (args[0], detail))
    return proc


def detect_mode():
    if not shutil.which("docker"):
        return "process"
    try:
        proc = run_docker(
            ["info", "--format", "{{.ServerVersion}}"], timeout=20, check=False
        )
    except CageError:
        return "process"
    if proc.returncode == 0 and proc.stdout.strip():
        return "docker"
    return "process"


def runtime_for(mode):
    if mode == "docker":
        return DockerRuntime()
    return ProcessRuntime()


def sanitize_source(source):
    value = (source or "").strip()
    if not value or value in ("local", "builtin"):
        return "local"
    return value


def resolve_agent_dir(source):
    source = sanitize_source(source)
    if source == "local":
        return str(AGENT_DIR)
    target = home() / "agent-src" / source_slug(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if (target / ".git").is_dir():
        print("updating agent source %s ..." % source)
        proc = subprocess.run(
            ["git", "-C", str(target), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise CageError(
                "cannot update agent source %s: %s"
                % (source, detail.splitlines()[-1] if detail else "git pull failed")
            )
    else:
        print("cloning agent source %s ..." % source)
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", source, str(target)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise CageError(
                "cannot clone agent source %s: %s"
                % (source, detail.splitlines()[-1] if detail else "git failed")
            )
    if not (target / "Dockerfile").is_file():
        raise CageError("agent source %s has no Dockerfile" % source)
    return str(target)


def docker_run_args(name, port, profile, env, cfg):
    env = env or {}
    args = [
        "run",
        "-d",
        "--name",
        "cage-" + name,
        "--network",
        NETWORK,
        "--network-alias",
        name,
        "--tmpfs",
        "/tmp:rw,exec,nosuid,size=" + str(cfg["tmp"]),
        "--tmpfs",
        "/work:rw,exec,nosuid,mode=1777,size=" + str(cfg["workspace"]),
        "--tmpfs",
        "/dev/shm:rw,nosuid,nodev,size=" + str(cfg["shm"]),
        "--pids-limit",
        str(cfg["pids"]),
        "--memory",
        str(cfg["memory"]),
        "--cpus",
        str(cfg["cpus"]),
        "--security-opt",
        "no-new-privileges:true",
        "--cap-drop",
        "ALL",
    ]
    if cfg.get("read_only"):
        args += ["--read-only"]
    for cap in cfg.get("caps") or []:
        args += ["--cap-add", cap]
    args += [
        "-p",
        "127.0.0.1:%d:%d" % (int(port), CONTAINER_PORT),
        "--user",
        str(cfg.get("user") or "0:0"),
        "-e",
        "HOME=/tmp",
        "-e",
        "LANG=C.UTF-8",
        "-e",
        "LC_ALL=C.UTF-8",
        "-e",
        "PYTHONIOENCODING=utf-8",
        "-e",
        "CAGE_AGENT_NAME=" + name,
        "-e",
        "CAGE_PROFILE=" + profile,
        "-e",
        "CAGE_INSTALL=" + ("1" if cfg.get("install") else "0"),
        "-e",
        "CAGE_SOURCE=" + sanitize_source(env.get("CAGE_SOURCE") or "local"),
        "-e",
        "CAGE_CONTEXT_TOKENS=" + str(context_tokens()),
        "-e",
        "CAGE_CONTEXT_HARD_LIMIT=" + str(CONTEXT_HARD_LIMIT),
        "-e",
        "CAGE_MAX_MESSAGE_CHARS=" + str(max_message_chars()),
        "-e",
        "CAGE_MAX_TURNS=" + str(max_turns()),
        "-e",
        "CAGE_ARCHIVE_MAX=" + str(_env_int("CAGE_ARCHIVE_MAX", 4000)),
        "-e",
        "MCAI_LLM_BASE_URL",
        "-e",
        "MCAI_LLM_API_KEY",
    ]
    model = (env.get("MCAI_LLM_MODEL") or os.environ.get("MCAI_LLM_MODEL") or "").strip()
    if model:
        args += ["-e", "MCAI_LLM_MODEL=" + model]
    fallback = os.environ.get("CAGE_DEFAULT_MODEL", "").strip()
    if fallback:
        args += ["-e", "CAGE_FALLBACK_MODEL=" + fallback]
    args.append(env.get("CAGE_IMAGE") or "cage-agent")
    return args


def process_spawn_args(name, port, profile, env, cfg):
    root = home()
    merged = dict(os.environ)
    if env:
        merged.update(env)
    merged["CAGE_BIND"] = "127.0.0.1"
    merged["CAGE_PORT"] = str(port)
    merged["CAGE_AGENT_NAME"] = name
    merged["CAGE_PROFILE"] = profile
    merged["CAGE_INSTALL"] = "1" if cfg.get("install") else "0"
    merged["CAGE_CONTEXT_TOKENS"] = str(context_tokens())
    merged["CAGE_CONTEXT_HARD_LIMIT"] = str(CONTEXT_HARD_LIMIT)
    merged["CAGE_MAX_MESSAGE_CHARS"] = str(max_message_chars())
    merged["CAGE_MAX_TURNS"] = str(max_turns())
    server = str(AGENT_DIR / "server.py")
    merged["CAGE_AGENT_SERVER"] = server
    launcher = (
        "import importlib.util, os, sys\n"
        "from http.server import ThreadingHTTPServer\n"
        "path = os.environ.get('CAGE_AGENT_SERVER')\n"
        "spec = importlib.util.spec_from_file_location('agent', path)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['agent'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "bind = os.environ.get('CAGE_BIND', '127.0.0.1')\n"
        "port = int(os.environ.get('CAGE_PORT', '8080'))\n"
        "httpd = ThreadingHTTPServer((bind, port), mod.Handler)\n"
        "httpd.serve_forever()\n"
    )
    return {
        "argv": [sys.executable, "-c", launcher],
        "cwd": str(root / "work" / name),
        "logpath": str(root / "logs" / (name + ".log")),
        "env": merged,
        "detached": True,
    }


class DockerRuntime:
    def ensure_image(self, profile, source, agent_dir, base_image, force=False):
        tag = image_tag(profile, source, base_image or "")
        if not force:
            proc = run_docker(["image", "inspect", tag], timeout=20, check=False)
            if proc.returncode == 0:
                return tag
        print("building image %s ..." % tag)
        run_docker(["build", "-t", tag, agent_dir], timeout=5400)
        return tag

    def ensure_network(self):
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

    def start(self, name, port, profile, env):
        cfg = PROFILES.get(profile) or PROFILES[DEFAULT_PROFILE]
        env = dict(env or {})
        args = docker_run_args(name, port, profile, env, cfg)
        run_docker(args, timeout=300)
        return "cage-" + name

    def status(self, name):
        proc = run_docker(
            ["inspect", "--format", "{{.State.Running}}", "cage-" + name],
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            return "missing"
        return "running" if proc.stdout.strip() == "true" else "stopped"

    def port_of(self, name):
        proc = run_docker(
            [
                "inspect",
                "--format",
                '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}',
                "cage-" + name,
            ],
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            return None
        try:
            return int(proc.stdout.strip())
        except ValueError:
            return None

    def stop(self, name):
        run_docker(["rm", "-f", "cage-" + name], timeout=60, check=False)

    def logs(self, name):
        proc = run_docker(["logs", "--tail", "40", "cage-" + name], timeout=20, check=False)
        text = ((proc.stderr or "") + (proc.stdout or "")).strip()
        return text


class ProcessRuntime:
    def ensure_image(self, profile, source, agent_dir, base_image, force=False):
        return image_tag(profile, source, base_image or "process")

    def start(self, name, port, profile, env):
        cfg = PROFILES.get(profile) or PROFILES[DEFAULT_PROFILE]
        spec = process_spawn_args(name, port, profile, env, cfg)
        Path(spec["cwd"]).mkdir(parents=True, exist_ok=True)
        log_path = Path(spec["logpath"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "ab")
        try:
            proc = subprocess.Popen(
                spec["argv"],
                cwd=spec["cwd"],
                env=spec["env"],
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            handle.close()
        self._write_meta(name, {"pid": proc.pid, "port": int(port)})
        return str(proc.pid)

    def status(self, name):
        meta = self._read_meta(name)
        if not meta or not meta.get("pid"):
            return "missing"
        try:
            os.kill(int(meta["pid"]), 0)
        except OSError:
            return "stopped"
        return "running"

    def port_of(self, name):
        meta = self._read_meta(name)
        if not meta:
            return None
        port = meta.get("port")
        try:
            return int(port)
        except (TypeError, ValueError):
            return None

    def stop(self, name):
        meta = self._read_meta(name)
        if meta and meta.get("pid"):
            self._kill_group(int(meta["pid"]))
        self._clear_meta(name)

    def logs(self, name):
        path = home() / "logs" / (name + ".log")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        lines = text.strip().splitlines()
        return "\n".join(lines[-40:])

    def _meta_path(self, name):
        return home() / "run" / (name + ".json")

    def _read_meta(self, name):
        path = self._meta_path(name)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_meta(self, name, payload):
        path = self._meta_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _clear_meta(self, name):
        path = self._meta_path(name)
        try:
            path.unlink()
        except OSError:
            pass

    def _kill_group(self, pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                return
        deadline = time.time() + 2
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.05)
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                return
