"""Isolis runtime contract tests (no network, no Docker, no real LLM)."""

import importlib.util
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.helpers import http_get, http_get_json, http_post

_ROOT = Path(__file__).resolve().parents[1]
_SERVER = _ROOT / "agent" / "server.py"


def _load(monkeypatch, env=None):
    defaults = {
        "ISOLIS_AGENT_NAME": "default",
        "MCAI_LLM_BASE_URL": "https://gw.example/v1",
        "MCAI_LLM_API_KEY": "k" * 12,
        "MCAI_LLM_MODEL": "m",
        "ISOLIS_PROFILE": "tooled",
        "ISOLIS_INSTALL": "1",
        "ISOLIS_CONTEXT_TOKENS": "900000",
        "ISOLIS_MAX_TURNS": "400",
        "ISOLIS_MAX_MESSAGE_CHARS": "900000",
    }
    for key in list(defaults):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("ISOLIS_SYSTEM_PROMPT", raising=False)
    for key, value in (env or defaults).items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("isolis_agent_%d" % id(env), _SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _serve(mod):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.IsolisHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _reset(mod):
    with mod.ISOLIS_HISTORY_LOCK:
        del mod.ISOLIS_HISTORY[:]
    with mod.ISOLIS_ARCHIVE_LOCK:
        del mod.ISOLIS_ARCHIVE[:]
    mod.ISOLIS_COMPRESSED_TURNS = 0


def test_tooled_profile_toolset(monkeypatch):
    mod = _load(monkeypatch)
    assert mod.isolis_tool_names() == [
        "run_command",
        "write_file",
        "read_file",
        "list_files",
        "browse",
        "install_packages",
        "install_android_sdk",
    ]
    assert mod.ISOLIS_INSTALL is True


def test_strict_profile_toolset(monkeypatch):
    mod = _load(monkeypatch, {"ISOLIS_AGENT_NAME": "default", "ISOLIS_INSTALL": "0"})
    assert mod.isolis_tool_names() == [
        "run_command",
        "write_file",
        "read_file",
        "list_files",
        "browse",
    ]
    assert mod.ISOLIS_INSTALL is False


def test_install_refused_in_strict(monkeypatch):
    mod = _load(monkeypatch, {"ISOLIS_AGENT_NAME": "default", "ISOLIS_INSTALL": "0"})
    assert mod.isolis_install_packages.invoke({"manager": "apt", "packages": "tree"}).startswith(
        "error:"
    )
    assert mod.isolis_install_android_sdk.invoke({"packages": ""}).startswith("error:")


def test_workspace_path_rejects_escape(tmp_path, monkeypatch):
    mod = _load(monkeypatch)
    with pytest.raises(ValueError):
        mod._isolis_workspace_path("../etc/passwd")


def test_truncate_keeps_head_tail_marker(monkeypatch):
    text = "A" * 30 + "B" * 30
    out, trimmed = _load(monkeypatch).isolis_truncate(text, 40)
    assert trimmed and out.startswith("A") and out.rstrip().endswith("B")
    assert "truncated" in out


def test_tool_output_truncation_marker(monkeypatch):
    mod = _load(monkeypatch)
    out = mod.isolis_truncate_output("x" * 25000)
    assert out.startswith("x" * 20000) and "truncated" in out


def test_protocol_reserved_domain_is_openai(monkeypatch):
    mod = _load(monkeypatch)
    assert mod.isolis_detect_protocol() == "openai"


def test_anthropic_base_strips_v1(monkeypatch):
    mod = _load(monkeypatch)
    assert mod.isolis_anthropic_base() == "https://gw.example"


def test_model_precedence_isolis_over_mcai(monkeypatch):
    mod = _load(monkeypatch, {
        "ISOLIS_AGENT_NAME": "default",
        "MCAI_LLM_MODEL": "base-model",
        "ISOLIS_MODEL": "override-model",
    })
    assert mod.isolis_model_name() == "override-model"


def test_health_and_info_shape(monkeypatch):
    mod = _load(monkeypatch)
    srv = _serve(mod)
    try:
        assert http_get(srv.server_address[1], "/health")["ok"] is True
        info = http_get(srv.server_address[1], "/info")
        assert info["via"] == "mc-direct" and info["injected"] is True
        assert info["framework"] == "langchain"
        assert info["profile"] == "tooled" and info["install"] is True
        assert info["workspace"] == "/work"
        assert info["android_home"] == "/opt/android-sdk"
        assert len(info["tools"]) == 7
        assert info["context_tokens"] == 900000
        assert info["context_hard_limit"] == 1000000
        assert info["pid"] > 0 and info["hostname"]
    finally:
        srv.shutdown()


def test_history_endpoint_counters(monkeypatch):
    mod = _load(monkeypatch)
    _reset(mod)
    with mod.ISOLIS_HISTORY_LOCK:
        mod.ISOLIS_HISTORY.extend([
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
    srv = _serve(mod)
    try:
        body = http_get(srv.server_address[1], "/history")
        assert body["ok"] is True
        assert body["history"][0]["content"] == "hello"
        assert body["compressed_turns"] == 0
        info = http_get(srv.server_address[1], "/info")
        assert info["history_turns"] == 1
        assert info["archived_turns"] == len(body["archive"])
    finally:
        srv.shutdown()


def test_chat_rejects_empty_and_bad_json(monkeypatch):
    mod = _load(monkeypatch)
    srv = _serve(mod)
    try:
        status, body, _ = http_post(srv.server_address[1], "/chat", {"message": "  "})
        assert status == 400 and body["ok"] is False
        status, body, _ = http_post(srv.server_address[1], "/chat", {})
        assert status == 400
    finally:
        srv.shutdown()


def test_chat_without_injection_fails(monkeypatch):
    mod = _load(monkeypatch, {"ISOLIS_AGENT_NAME": "default"})
    srv = _serve(mod)
    try:
        status, body, _ = http_post(srv.server_address[1], "/chat", {"message": "hi"})
        assert status == 500 and "not injected" in body["error"]
        info = http_get(srv.server_address[1], "/info")
        assert info["injected"] is False
    finally:
        srv.shutdown()


def test_unknown_path_404(monkeypatch):
    mod = _load(monkeypatch)
    srv = _serve(mod)
    try:
        status, body, _ = http_get_json(srv.server_address[1], "/nope")
        assert status == 404 and body["ok"] is False
    finally:
        srv.shutdown()


def test_clamp_context_budget(monkeypatch):
    mod = _load(monkeypatch, {"ISOLIS_AGENT_NAME": "default", "ISOLIS_CONTEXT_TOKENS": "2000000"})
    assert mod.ISOLIS_CONTEXT_TOKENS == 1000000


def test_evict_bounds_turns_and_archives(monkeypatch):
    mod = _load(monkeypatch, {"ISOLIS_AGENT_NAME": "default", "ISOLIS_MAX_TURNS": "2"})
    _reset(mod)
    history = []
    for i in range(6):
        history.append({"role": "user", "content": "u%d" % i})
        history.append({"role": "assistant", "content": "a%d" % i})
    removed = mod.isolis_evict(history)
    assert mod._isolis_user_count(history) == 2 and removed == 4
    assert mod.isolis_archive_len() == 8
    assert all(entry["reason"] == "evicted" for entry in mod.isolis_load_archive())


def test_compression_folds_into_single_marker(monkeypatch):
    mod = _load(monkeypatch, {
        "ISOLIS_AGENT_NAME": "default",
        "ISOLIS_CONTEXT_TOKENS": "1",
        "ISOLIS_COMPRESS_BATCH": "4",
    })
    _reset(mod)
    monkeypatch.setattr(mod, "isolis_summarize", lambda turns, prior="": "SUM:" + prior + ":ok")
    history = []
    for i in range(20):
        history.append({"role": "user", "content": "u%d" % i})
        history.append({"role": "assistant", "content": "a%d" % i})
    mod.isolis_compress(history)
    markers = [t for t in history if str(t["content"]).startswith(mod.ISOLIS_COMPRESS_MARKER)]
    assert len(markers) == 1
    assert mod.ISOLIS_COMPRESSED_TURNS > 0
    assert any(entry["reason"] == "compressed" for entry in mod.isolis_load_archive())
    assert history[-1]["content"] == "a19"


def test_compress_disabled_when_budget_zero(monkeypatch):
    mod = _load(monkeypatch, {"ISOLIS_AGENT_NAME": "default", "ISOLIS_CONTEXT_TOKENS": "0"})
    _reset(mod)
    history = []
    for i in range(10):
        history.append({"role": "user", "content": "u%d" % i})
        history.append({"role": "assistant", "content": "a%d" % i})
    assert mod.isolis_compress(history) == 0
    assert not any(
        str(t["content"]).startswith(mod.ISOLIS_COMPRESS_MARKER) for t in history
    )
    assert mod.isolis_archive_len() == 0


def test_summary_prompt_keeps_first_message_fact(monkeypatch):
    mod = _load(monkeypatch, {"ISOLIS_AGENT_NAME": "default"})
    turns = [
        {"role": "user", "content": "Remember the secret code ALPHA-42 lives in /work/answers.txt"},
        {"role": "assistant", "content": "Noted."},
    ]
    summary = mod._isolis_summary_input(turns)
    assert "ALPHA-42" in summary and "/work/answers.txt" in summary


def test_system_prompt_override(monkeypatch):
    mod = _load(monkeypatch, {
        "ISOLIS_AGENT_NAME": "default",
        "ISOLIS_SYSTEM_PROMPT": "custom prompt body",
    })
    assert mod.isolis_system_prompt() == "custom prompt body"
