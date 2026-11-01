import hashlib
import json

import pytest

from cage import autonomy


def test_decide_always_runs():
    for level in ("auto", "assisted", "manual", "", None):
        for tool in ("install_packages", "solve_challenge", "browse", "git_publish", "run_command"):
            assert autonomy.decide(tool, {}, level) == "run"


def test_effective_autonomy_never_downgrades():
    usage = {"day": "2026-10-31", "count": 9999}
    assert autonomy.effective_autonomy("auto", usage, 500, "2026-10-31") == "auto"
    assert autonomy.effective_autonomy("assisted", usage, 1, "2026-10-31") == "assisted"
    assert autonomy.effective_autonomy("", None, None, "") == "auto"


def test_consume_counts_but_never_blocks():
    state = {}
    for _ in range(5):
        autonomy.consume(state, "2026-10-31", 500)
    assert state["usage"]["count"] == 5
    assert autonomy.effective_autonomy("auto", state["usage"], 500, "2026-10-31") == "auto"


def test_audit_record_digests_sensitive_args():
    rec = autonomy.audit_record(
        "2026-10-31T00:00:00Z",
        "default",
        "install_packages",
        {"api_key": "sk-123456", "packages": "tree"},
        42,
        "ok",
        12,
    )
    dumped = json.dumps(rec)
    assert "sk-123456" not in dumped and rec["args"]["api_key"] == hashlib.sha256(b"sk-123456").hexdigest()[:8]
    assert rec["args"]["packages"] == "tree"


def test_no_gate_ever_holds_a_tool(monkeypatch):
    monkeypatch.setenv("CAGE_AGENT_MODE", "bare")
    import importlib

    import agent

    importlib.reload(agent)
    assert agent.auto_confirm() is True
    for tool in ("install_packages", "solve_challenge", "browse", "git_publish", "run_command"):
        assert agent._decide_tool(tool, {}) == "run"
    # The only failure left without a platform gateway is the environment
    # requirement itself, never a confirmation gate.
    with pytest.raises(RuntimeError):
        agent.run_chat("apt 装个 tree")
