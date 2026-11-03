"""Process-mode and protocol-negotiation tests.

The failed 2026-11-02 run died at "docker CLI not found" during init, so the
whole surface collapsed to NOT VERIFIED. These tests pin the degraded path:
with no Docker CLI and no daemon, init starts agent/server.py as a host
process on 127.0.0.1, doctor stays ready when the LLM is injected, and the
wire protocol is negotiated against a local stub (no public network).
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.helpers import load_agent, load_host


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    module = load_agent()
    monkeypatch.setattr(module, "WORKSPACE", str(tmp_path / "work"))
    os.makedirs(module.WORKSPACE, exist_ok=True)
    return module


@pytest.fixture()
def host(tmp_path, monkeypatch):
    module = load_host()
    state_dir = tmp_path / ".isolis"
    monkeypatch.setattr(module, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(module, "STATE_FILE", str(state_dir / "state.json"))
    monkeypatch.setattr(module, "PROCESS_DIR", str(state_dir / "run"))
    monkeypatch.setattr(module, "PROCESS_LOG_DIR", str(state_dir / "logs"))
    monkeypatch.setattr(module, "PROCESS_WORK_DIR", str(state_dir / "workspaces"))
    monkeypatch.setattr(module, "docker_published_ports", lambda: set())
    return module


@pytest.fixture()
def no_docker(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)


@pytest.fixture()
def fake_llm(monkeypatch):
    llm = {
        "base_url": "http://stub.invalid/v1",
        "api_key": "stub-key-0123456789abcdef",
        "model": "isolis-chat-default",
        "host": "stub.invalid",
        "key_len": 24,
        "key_prefix": "stu...",
    }
    monkeypatch.setenv("MCAI_LLM_BASE_URL", llm["base_url"])
    monkeypatch.setenv("MCAI_LLM_API_KEY", llm["api_key"])
    monkeypatch.setenv("MCAI_LLM_MODEL", llm["model"])
    return llm


def _patch_detect_llm(monkeypatch, host, llm):
    monkeypatch.setattr(host, "detect_llm", lambda: llm)


# ------------------------------------------------------------- mode detection


def test_detect_mode_process_without_docker_cli(host, no_docker):
    assert host.detect_mode() == "process"


def test_detect_mode_process_when_daemon_unreachable(host, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")

    def boom(args, timeout=60, check=True):
        raise host.IsolisError("cannot run docker: daemon down")

    monkeypatch.setattr(host, "run_docker", boom)
    assert host.detect_mode() == "process"


def test_detect_mode_docker_when_daemon_answers(host, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")

    class Proc(object):
        returncode = 0
        stdout = "27.0.3\n"

    monkeypatch.setattr(
        host, "run_docker", lambda args, timeout=60, check=True: Proc()
    )
    assert host.detect_mode() == "docker"


# ------------------------------------------------------------------- env map


def test_container_env_whitelist_matches_spec_g(host, fake_llm, monkeypatch):
    monkeypatch.delenv("ISOLIS_SYSTEM_PROMPT", raising=False)
    monkeypatch.delenv("ISOLIS_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("ISOLIS_MODEL", raising=False)
    env = host.container_env(
        "alpha", 18080, fake_llm, "tooled", host.PROFILES["tooled"], "local"
    )
    for name in (
        "MCAI_LLM_BASE_URL",
        "MCAI_LLM_API_KEY",
        "MCAI_LLM_MODEL",
        "ISOLIS_AGENT_NAME",
        "ISOLIS_PROFILE",
        "ISOLIS_INSTALL",
        "ISOLIS_MAX_TURNS",
        "ISOLIS_MAX_MESSAGE_CHARS",
        "ISOLIS_CONTEXT_TOKENS",
        "ISOLIS_CONTEXT_HARD_LIMIT",
        "ISOLIS_COMPRESS_BATCH",
        "ISOLIS_ARCHIVE_MAX",
    ):
        assert name in env, name
    assert env["MCAI_LLM_MODEL"] == fake_llm["model"]
    assert env["ISOLIS_INSTALL"] == "1"
    assert "ISOLIS_FALLBACK_MODEL" not in env  # D5: the name does not exist


def test_container_env_forwards_optional_overrides(host, fake_llm, monkeypatch):
    monkeypatch.setenv("ISOLIS_SYSTEM_PROMPT", "be terse")
    monkeypatch.setenv("ISOLIS_DEFAULT_MODEL", "isolis-chat-default")
    monkeypatch.setenv("ISOLIS_MODEL", "override-model")
    env = host.container_env(
        "alpha", 18080, fake_llm, "strict", host.PROFILES["strict"], "local"
    )
    assert env["ISOLIS_SYSTEM_PROMPT"] == "be terse"
    assert env["ISOLIS_DEFAULT_MODEL"] == "isolis-chat-default"
    assert env["ISOLIS_MODEL"] == "override-model"
    assert env["ISOLIS_INSTALL"] == "0"


def test_default_model_constant_is_vendor_neutral(host):
    assert "/" not in host.DEFAULT_MODEL
    assert host.DEFAULT_MODEL == "isolis-chat-default"


# --------------------------------------------------------- process lifecycle


def _http_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_health(port, seconds=25):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            body = _http_json("http://127.0.0.1:%d/health" % port)
            if body.get("ok") is True:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def test_process_runtime_start_health_stop(host, no_docker, fake_llm, monkeypatch):
    monkeypatch.setattr(host, "allocate_port", lambda state: 18099)
    rt = host.runtime_for(host.detect_mode())
    assert rt.mode == "process"

    rt.start("proc-test", 18099, fake_llm, "tooled", "local")
    try:
        assert rt.status("proc-test") == "running"
        assert rt.port_of("proc-test") == 18099
        assert _wait_health(18099)
        info = _http_json("http://127.0.0.1:%d/info" % 18099)
        assert info["via"] == "mc-direct"
        assert info["injected"] is True
        assert info["model"] == "isolis-chat-default"
        assert info["profile"] == "tooled"
        assert info["install"] is True
        assert len(info["tools"]) == 7
        assert info["protocol"] in ("openai", "anthropic", "unset")
    finally:
        rt.stop("proc-test")
    assert rt.status("proc-test") == "missing"


def test_init_runs_without_docker_and_records_process_mode(
    host, no_docker, fake_llm, monkeypatch, capsys
):
    _patch_detect_llm(monkeypatch, host, fake_llm)
    host.cmd_init("alpha")
    out = capsys.readouterr().out
    assert "runtime: process mode" in out
    state = host.load_state()
    entry = state["agents"]["alpha"]
    assert entry["mode"] == "process"
    assert entry["profile"] == "tooled"
    assert isinstance(entry["port"], int)
    port = entry["port"]
    assert 18080 <= port <= 18119
    assert _wait_health(port)
    info = _http_json("http://127.0.0.1:%d/info" % port)
    assert info["agent"] == "alpha"
    assert state["current"] == "alpha"

    # reuse keeps one process and one record
    host.cmd_init("alpha")
    state2 = host.load_state()
    assert state2["agents"]["alpha"]["port"] == port
    assert _wait_health(port)

    host.cmd_stop("alpha")
    assert "alpha" not in host.load_state()["agents"]


def test_ensure_chat_agent_reattaches_to_running_process(
    host, no_docker, fake_llm, monkeypatch, capsys
):
    _patch_detect_llm(monkeypatch, host, fake_llm)
    host.cmd_init("beta")
    name, port = host.ensure_chat_agent()
    assert name == "beta"
    out = capsys.readouterr().out
    assert "chat: beta" in out
    assert _wait_health(port)
    host.cmd_stop("beta")


def test_doctor_ready_without_docker_when_llm_injected(
    host, no_docker, fake_llm, monkeypatch, capsys
):
    _patch_detect_llm(monkeypatch, host, fake_llm)
    monkeypatch.setattr(host, "reachable", lambda url, timeout=8: (False, None))
    assert host.cmd_doctor() is True
    out = capsys.readouterr().out
    assert "process mode will be used" in out
    assert "[fail]" not in out


def test_doctor_fails_without_llm_injection(host, no_docker, monkeypatch, capsys):
    monkeypatch.delenv("MCAI_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("MCAI_LLM_API_KEY", raising=False)
    monkeypatch.setattr(host, "reachable", lambda url, timeout=8: (False, None))
    assert host.cmd_doctor() is False
    assert "[fail]" in capsys.readouterr().out


# ---------------------------------------------- protocol negotiation (stub)


class _AnthropicStub(BaseHTTPRequestHandler):
    """Local stand-in for a gateway that only speaks the Anthropic wire."""

    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if path.endswith("/chat/completions"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.endswith("/v1/messages"):
            body = json.dumps(
                {
                    "id": "msg_stub",
                    "content": [{"type": "text", "text": "pong from anthropic"}],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


class _OpenAIStub(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = json.dumps(
            {
                "id": "chatcmpl-stub",
                "choices": [
                    {"message": {"role": "assistant", "content": "pong from openai"}}
                ],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server, thread


def test_negotiation_selects_anthropic_on_404_and_chats(
    agent, monkeypatch, tmp_path
):
    server, thread = _serve(_AnthropicStub)
    try:
        port = server.server_address[1]
        monkeypatch.setattr(agent, "BASE_URL", "http://127.0.0.1:%d/v1" % port)
        monkeypatch.setattr(agent, "API_KEY", "stub-key")
        monkeypatch.setattr(agent, "MODEL", "isolis-chat-default")
        monkeypatch.setattr(agent, "INJECTED", True)
        monkeypatch.setattr(agent, "_PROTO", None)
        assert agent.detect_protocol() == "anthropic"
        assert agent.anthropic_base_url() == "http://127.0.0.1:%d" % port
        llm = agent.get_llm()
        bound = llm.bind_tools(agent.select_tool_schemas())
        response = bound.invoke(agent.MEMORY.window())
        assert agent.content_to_text(getattr(response, "content", "")) == (
            "pong from anthropic"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        monkeypatch.setattr(agent, "_PROTO", None)


def test_negotiation_selects_openai_and_chats(agent, monkeypatch):
    server, thread = _serve(_OpenAIStub)
    try:
        port = server.server_address[1]
        monkeypatch.setattr(agent, "BASE_URL", "http://127.0.0.1:%d/v1" % port)
        monkeypatch.setattr(agent, "API_KEY", "stub-key")
        monkeypatch.setattr(agent, "MODEL", "isolis-chat-default")
        monkeypatch.setattr(agent, "INJECTED", True)
        monkeypatch.setattr(agent, "_PROTO", None)
        assert agent.detect_protocol() == "openai"
        llm = agent.get_llm()
        bound = llm.bind_tools(agent.select_tool_schemas())
        response = bound.invoke(agent.MEMORY.window())
        assert agent.content_to_text(getattr(response, "content", "")) == (
            "pong from openai"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        monkeypatch.setattr(agent, "_PROTO", None)


def test_process_spawn_env_binds_loopback_only(host, no_docker, fake_llm, monkeypatch):
    # the child listens on 127.0.0.1 with the allocated host port, never 0.0.0.0
    captured = {}

    class FakePopen(object):
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["env"] = kwargs.get("env") or {}
            self.pid = 424242

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    rt = host.ProcessRuntime()
    rt.start("bind-check", 18098, fake_llm, "tooled", "local")
    assert captured["argv"][-1].endswith("server.py")
    assert captured["env"]["ISOLIS_BIND"] == "127.0.0.1"
    assert captured["env"]["ISOLIS_PORT"] == "18098"
    assert captured["env"]["ISOLIS_WORKSPACE"].endswith(
        os.path.join("workspaces", "bind-check")
    )
    assert os.path.isdir(captured["env"]["ISOLIS_WORKSPACE"])
