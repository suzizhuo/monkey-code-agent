import json
import threading
import urllib.error
import urllib.request

from cage import cli
from cage import httpapi
from cage import naming
from cage import state


def _url(port, path):
    return "http://127.0.0.1:%d%s" % (port, path)


def _call(method, port, path, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        _url(port, path), data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read()
            status = response.status
            hdrs = dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        hdrs = dict(exc.headers)
    text = raw.decode("utf-8")
    try:
        body = json.loads(text) if text else {}
    except json.JSONDecodeError:
        body = {}
    return status, body, hdrs, text


def http_post(port, path, payload):
    status, body, headers, _ = _call("POST", port, path, payload)
    return status, body, headers


def http_get(port, path):
    status, body, headers, text = _call("GET", port, path)
    return status, body, headers, text


def _serve():
    srv = httpapi.make_server(0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


def test_api_chat_autostarts_agent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli, "ensure_chat_agent", lambda: (calls.append("ensure"), ("default", 18080))[1]
    )
    monkeypatch.setattr(cli, "chat_request", lambda p, m, t=120: "回复:" + m)
    srv = _serve()
    try:
        s, b, headers = http_post(srv.server_address[1], "/api/chat", {"message": "你好"})
        assert s == 200 and b["ok"] and b["reply"] == "回复:你好" and calls == ["ensure"]
        assert headers.get("Access-Control-Allow-Origin") == "*"  # CORS 常开（裁定 #10）
    finally:
        srv.shutdown()


def test_api_agents_and_history_passthrough(monkeypatch):
    st = {
        "version": 1,
        "current": "default",
        "agents": {
            "default": {
                "name": "default",
                "port": 18080,
                "mode": "process",
                "profile": "tooled",
                "model": "gw-alpha",
            }
        },
    }
    monkeypatch.setattr(state, "load_state", lambda: st)
    monkeypatch.setattr(
        httpapi,
        "container_request",
        lambda method, port, path, payload=None, timeout=30: (
            200,
            {"history": [{"role": "user", "content": "hi"}]},
        ),
    )
    srv = _serve()
    try:
        port = srv.server_address[1]
        s, b, headers, _ = http_get(port, "/api/agents")
        assert s == 200 and b["ok"]
        assert any(item.get("name") == "default" for item in b["agents"])
        assert headers.get("Access-Control-Allow-Origin") == "*"
        s, b, headers, _ = http_get(port, "/api/history?agent=default")
        assert s == 200 and b["history"] == [{"role": "user", "content": "hi"}]
    finally:
        srv.shutdown()


def test_serve_port_excluded_from_agent_pool():
    port = naming.allocate_port(set(), set(), set(), {state.SERVE_PORT})
    assert port != state.SERVE_PORT and port == 18080  # 池首位不被 serve 占用


def test_static_dir_serves_index(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<h1>cage</h1>", encoding="utf-8")
    monkeypatch.setenv("CAGE_STATIC_DIR", str(tmp_path))
    srv = _serve()
    try:
        s, _body, headers, text = http_get(srv.server_address[1], "/")
        assert s == 200
        assert "text/html" in (headers.get("Content-Type") or "")
        assert "<h1>cage</h1>" in text
    finally:
        srv.shutdown()
