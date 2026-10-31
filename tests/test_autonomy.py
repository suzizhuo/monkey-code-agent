import hashlib
import json
import importlib

from cage import autonomy


def test_decide_matrix():
    assert autonomy.decide("install_packages", {}, "auto") == "run"
    assert autonomy.decide("install_packages", {}, "assisted") == "confirm"
    assert autonomy.decide("run_command", {}, "assisted") == "run"
    assert autonomy.decide("run_command", {}, "manual") == "confirm"
    assert autonomy.decide("write_file", {}, "manual") == "confirm"


def test_effective_autonomy_downgrades_over_budget():
    usage = {"day": "2026-10-31", "count": 501}
    assert autonomy.effective_autonomy("auto", usage, 500, "2026-10-31") == "assisted"
    assert autonomy.effective_autonomy("auto", {"day": "2026-10-30", "count": 900}, 500, "2026-10-31") == "auto"


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


def test_chat_pending_flow_does_not_execute(monkeypatch):
    monkeypatch.setenv("CAGE_AGENT_MODE", "bare")
    import agent

    importlib.reload(agent)
    monkeypatch.setattr(agent, "INSTALL_ENABLED", True)
    executed = []
    monkeypatch.setattr(agent.install_packages, "invoke", lambda a: executed.append(a) or "done")
    monkeypatch.setattr(agent, "autonomy_of", lambda: "assisted")
    resp = agent.run_chat("apt 装个 tree")
    assert resp["pending"]["tool"] == "install_packages" and executed == []
