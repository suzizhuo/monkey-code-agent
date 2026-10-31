import threading

import pytest
from http.server import ThreadingHTTPServer

from tests.helpers import http_get, http_post, load_agent
import agent


def _reset_archive():
    with agent.ARCHIVE_LOCK:
        del agent.ARCHIVE[:]


def test_workspace_path_rejects_escape(tmp_path):
    with pytest.raises(ValueError):
        agent._workspace_path("../etc/passwd", str(tmp_path))
    (tmp_path / "link").symlink_to("/etc")
    with pytest.raises(ValueError):
        agent._workspace_path("link/passwd", str(tmp_path))


def test_truncate_message_keeps_head_and_tail_with_marker():
    text = "A" * 30 + "B" * 30
    out, trimmed = agent.truncate_message(text, 40)
    assert trimmed and out.startswith("A") and out.rstrip().endswith("B")
    assert "truncated" in out


def test_count_tokens_fallback_without_encoder(monkeypatch):
    monkeypatch.setattr(agent, "ENCODER", None)
    assert agent.count_tokens("abcdefgh") == 2


def test_safe_text_recovers_gbk_bad_bytes():
    raw = "中文".encode("gbk").decode("utf-8", "surrogateescape")
    text = "pre-" + raw + "-post"
    out = agent.safe_text(text)
    assert out.startswith("pre-") and out.endswith("-post") and "中文" in out


def test_overflow_evicts_originals_and_builds_index_without_llm(monkeypatch):
    monkeypatch.setattr(agent, "CAGE_MAX_TURNS", 3)
    monkeypatch.setattr(agent, "CAGE_SUMMARIZE", False)
    monkeypatch.setattr(agent, "CAGE_INDEX_MAX_CHARS", 500)
    monkeypatch.setattr(agent, "summarize_turns", lambda *a, **k: pytest.fail("summarizer must not run"))
    _reset_archive()
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "turn-%03d" % i}
               for i in range(20)]
    result = agent.reduce_history(history)
    assert result["evicted"] >= 14 and agent.archive_len() >= 14
    assert "turn-006" in result["index"] and len(result["index"]) <= 500


def test_archive_keeps_originals_byte_for_byte(monkeypatch):
    monkeypatch.setattr(agent, "CAGE_MAX_TURNS", 2)
    _reset_archive()
    history = [{"role": "user", "content": "第一句：路径 /work/a.py，版本 1.2.3"},
               {"role": "assistant", "content": "收到"},
               {"role": "user", "content": "第二句"},
               {"role": "assistant", "content": "好"},
               {"role": "user", "content": "第三句"},
               {"role": "assistant", "content": "行"}]
    agent.reduce_history(history)
    archived = agent.load_archive()
    assert archived[0]["content"] == "第一句：路径 /work/a.py，版本 1.2.3"


def test_summarize_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(agent, "CAGE_SUMMARIZE", True)
    monkeypatch.setattr(agent, "CAGE_CONTEXT_TOKENS", 10)
    monkeypatch.setattr(agent, "count_tokens", lambda t: 10 ** 6)
    _reset_archive()
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x"} for i in range(8)]
    agent.compress_history(history, summarize=lambda turns, prior="": "L")
    assert any(str(t["content"]).startswith("[summary-lossy]") for t in history)
    assert len(agent.SUMMARIZE_CALLS) == 1


def test_evict_history_keeps_pairs(monkeypatch):
    monkeypatch.setattr(agent, "ARCHIVE_MAX", 4000)
    _reset_archive()
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(10)]
    agent.evict_history(history, 3)
    assert sum(1 for t in history if t["role"] == "user") == 3
    assert history[0]["content"] == "4"


def test_health_info_and_tools_count(monkeypatch):
    monkeypatch.setenv("MCAI_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("MCAI_LLM_API_KEY", "k" * 12)
    monkeypatch.setenv("MCAI_LLM_MODEL", "m")
    mod = load_agent()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert http_get(port, "/health")["ok"] is True
        info = http_get(port, "/info")
        assert info["via"] == "mc-direct" and info["injected"] is True
        assert "android_home" not in info
        assert len(info["tools"]) == 6
        assert info["protocol"] in ("openai", "anthropic")
    finally:
        srv.shutdown()


def test_info_strict_tools_count_and_install_flag(monkeypatch):
    monkeypatch.setenv("MCAI_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("MCAI_LLM_API_KEY", "k" * 12)
    monkeypatch.setenv("MCAI_LLM_MODEL", "m")
    monkeypatch.setenv("CAGE_INSTALL", "0")
    mod = load_agent()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        info = http_get(srv.server_address[1], "/info")
        assert len(info["tools"]) == 5 and info["install"] is False
    finally:
        srv.shutdown()


def test_install_packages_refused_in_strict():
    mod = load_agent()
    saved = mod.INSTALL_ENABLED
    mod.INSTALL_ENABLED = False
    try:
        assert mod.install_packages.invoke({"manager": "apt", "packages": "tree"}).startswith("error:")
    finally:
        mod.INSTALL_ENABLED = saved


def test_chat_without_injection_returns_clear_error(monkeypatch):
    monkeypatch.delenv("MCAI_LLM_API_KEY", raising=False)
    monkeypatch.delenv("MCAI_LLM_BASE_URL", raising=False)
    mod = load_agent()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, body, _headers = http_post(srv.server_address[1], "/chat", {"message": "hi"})
        assert status == 500 and "not injected" in body["error"]
    finally:
        srv.shutdown()


def test_dockerfile_has_no_android_or_jdk_and_pins_base():
    text = open("agent/Dockerfile", encoding="utf-8").read()
    assert "python:3.12-slim" in text
    for banned in ("android", "ANDROID_HOME", "openjdk", "JAVA_HOME", "GRADLE"):
        assert banned not in text
    assert "tiktoken" not in text.lower() or "TIKTOKEN_CACHE_DIR=/tmp" in text


def test_requirements_pins_four_packages():
    lines = [l.strip() for l in open("agent/requirements.txt", encoding="utf-8") if l.strip()]
    assert len(lines) == 4
    assert all("<1.0.0" in l for l in lines)
