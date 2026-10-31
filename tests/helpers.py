"""Shared HTTP helpers and agent.server.py loader for tests."""

import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SERVER = _ROOT / "agent" / "server.py"


def load_agent():
    sys.modules.pop("agent", None)
    spec = importlib.util.spec_from_file_location("agent", _SERVER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent"] = mod
    spec.loader.exec_module(mod)
    return mod


load_agent()


def http_get(port, path):
    url = "http://127.0.0.1:%d%s" % (port, path)
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(port, path, payload):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {"error": raw}
        return exc.code, body, dict(exc.headers)


def agent_info_via_http(port=None):
    if port is None:
        import threading
        from http.server import ThreadingHTTPServer

        import agent as mod

        srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            return http_get(srv.server_address[1], "/info")
        finally:
            srv.shutdown()
    return http_get(port, "/info")
