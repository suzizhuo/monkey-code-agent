"""Cage home directory, flock, and atomic state.json I/O."""

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

from cage import CageError

SERVE_PORT = 18079
CONTAINER_PORT = 8080
NETWORK = "cage-net"
DEFAULT_NAME = "default"
DEFAULT_PROFILE = "tooled"
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


def home():
    override = os.environ.get("CAGE_HOME")
    if override:
        return Path(override)
    return Path.home() / ".cage"


def _state_path():
    return home() / "state.json"


def _empty_state():
    return {"version": 1, "current": None, "agents": {}, "usage": {}}


@contextmanager
def state_lock():
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "state.lock"
    handle = open(path, "w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def load_state():
    path = _state_path()
    if not path.exists():
        return _empty_state()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CageError("cannot read state file: %s" % path) from exc
    if not isinstance(data, dict):
        raise CageError("cannot read state file: %s" % path)
    agents = data.get("agents")
    current = data.get("current")
    if not isinstance(agents, dict):
        raise CageError("cannot read state file: %s" % path)
    if current is not None and not isinstance(current, str):
        raise CageError("cannot read state file: %s" % path)
    clean = {}
    for name, entry in agents.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise CageError("cannot read state file: %s" % path)
        clean[name] = entry
    out = {"version": 1, "current": current, "agents": clean}
    if isinstance(data.get("usage"), dict):
        out["usage"] = data.get("usage")
    return out


def save_state(state):
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    path = _state_path()
    payload = {
        "version": 1,
        "current": state.get("current"),
        "agents": state.get("agents") or {},
    }
    if isinstance(state.get("usage"), dict):
        payload["usage"] = state.get("usage")
    tmp = Path(str(path) + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise CageError("cannot write state file: %s" % path) from exc
