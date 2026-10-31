"""Host serve API: ThreadingHTTPServer, CORS, static dir, agent passthrough."""

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cage import CageError
from cage import cli
from cage import state
from cage.model import http_json
from cage.naming import sanitize_name


def container_request(method, port, path, payload=None, timeout=30):
    return http_json(
        method,
        "http://127.0.0.1:%d%s" % (int(port), path),
        payload=payload,
        timeout=timeout,
    )


def _agent_item(name, entry):
    mode = entry.get("mode") or ""
    status = "unknown"
    try:
        from cage.runtime import runtime_for

        if mode:
            status = runtime_for(mode).status(name)
    except Exception:
        pass
    return {
        "name": name,
        "port": entry.get("port"),
        "mode": mode,
        "profile": entry.get("profile"),
        "model": entry.get("model"),
        "status": status,
    }


def _lookup_agent(st, name):
    agents = st.get("agents") or {}
    if name:
        key = sanitize_name(name) or name
        entry = agents.get(key)
        if entry is None:
            raise CageError("unknown agent: %s" % key)
        return key, entry
    current = st.get("current")
    if not current:
        raise CageError("no current agent (run: init)")
    entry = agents.get(current)
    if entry is None:
        raise CageError("unknown agent: %s" % current)
    return current, entry


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_bytes(self, status, content_type, data):
        if not isinstance(data, bytes):
            data = data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self._cors()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status, body):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, "application/json", payload)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CageError("invalid JSON body")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise CageError("JSON object required")
        return data

    def _serve_static(self, path):
        root = os.environ.get("CAGE_STATIC_DIR", "").strip()
        if not root:
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        base = Path(root).resolve()
        if not base.is_dir():
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        rel = path.lstrip("/") or "index.html"
        target = (base / rel).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") and "charset=" not in ctype:
            ctype = ctype + "; charset=utf-8"
        self._send_bytes(200, ctype, data)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        query = parse_qs(parsed.query)
        try:
            self._handle(method, path, query)
        except CageError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _handle(self, method, path, query):
        if path == "/api/health" and method == "GET":
            st = state.load_state()
            agents = [
                _agent_item(name, entry)
                for name, entry in (st.get("agents") or {}).items()
            ]
            self._send_json(
                200, {"ok": True, "agents": agents, "current": st.get("current")}
            )
            return
        if path == "/api/agents" and method == "GET":
            st = state.load_state()
            agents = [
                _agent_item(name, entry)
                for name, entry in (st.get("agents") or {}).items()
            ]
            self._send_json(200, {"ok": True, "agents": agents})
            return
        if path == "/api/agents" and method == "POST":
            body = self._read_json()
            cli.cmd_init(
                body.get("name"),
                profile=body.get("profile"),
                source=body.get("source"),
            )
            st = state.load_state()
            current = st.get("current")
            entry = (st.get("agents") or {}).get(current) or {}
            self._send_json(
                200,
                {
                    "ok": True,
                    "name": current,
                    "port": entry.get("port"),
                    "profile": entry.get("profile"),
                    "source": entry.get("source"),
                },
            )
            return
        if path.startswith("/api/agents/") and method in ("GET", "DELETE"):
            raw = path[len("/api/agents/") :].strip("/")
            if not raw or "/" in raw:
                self._send_json(404, {"ok": False, "error": "not found"})
                return
            if method == "DELETE":
                cli.cmd_stop(raw)
                self._send_json(200, {"ok": True})
                return
            st = state.load_state()
            name, entry = _lookup_agent(st, raw)
            info = None
            port = entry.get("port")
            if isinstance(port, int):
                try:
                    _status, info = container_request("GET", port, "/info")
                except CageError:
                    info = None
            item = _agent_item(name, entry)
            self._send_json(200, {"ok": True, "agent": item, "info": info})
            return
        if path == "/api/chat" and method == "POST":
            body = self._read_json()
            message = body.get("message")
            if not isinstance(message, str) or not message.strip():
                raise CageError("message is required")
            agent = body.get("agent")
            if agent:
                chosen = sanitize_name(str(agent)) or str(agent)
                with state.state_lock():
                    st = state.load_state()
                    st["current"] = chosen
                    state.save_state(st)
            _name, port = cli.ensure_chat_agent()
            reply = cli.chat_request(port, message)
            self._send_json(200, {"ok": True, "reply": reply})
            return
        if path == "/api/history" and method == "GET":
            agent = (query.get("agent") or [None])[0]
            st = state.load_state()
            _name, entry = _lookup_agent(st, agent)
            port = entry.get("port")
            if not isinstance(port, int):
                raise CageError("agent has no port")
            status, body = container_request("GET", port, "/history")
            if not isinstance(body, dict):
                body = {"history": []}
            self._send_json(status, body)
            return
        if method == "GET":
            self._serve_static(path)
            return
        self._send_json(404, {"ok": False, "error": "not found"})


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_server(port):
    return _Server(("127.0.0.1", int(port)), Handler)


def serve(port):
    srv = make_server(port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
