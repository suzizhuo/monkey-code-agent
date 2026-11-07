import importlib

from cage import workers


def test_orchestrator_toolset_is_delegate_plus_challenge():
    assert workers.orchestrator_tools() == ["delegate", "challenge_probe", "solve_challenge"]


def test_worker_toolset_excludes_delegate_even_if_requested():
    tools = workers.worker_allowed_tools(
        ["run_command", "delegate", "install_packages", "solve_challenge"]
    )
    assert "delegate" not in tools and set(tools) == {
        "run_command",
        "install_packages",
        "solve_challenge",
    }


def test_clamp_worker_result():
    raw = {
        "summary": "S" * 5000,
        "facts": {str(i): i for i in range(99)},
        "files": ["f%d" % i for i in range(99)],
        "errors": ["e"] * 99,
        "steps_used": 8,
    }
    out = workers.clamp_worker_result(raw)
    assert len(out["summary"]) == 800 and len(out["facts"]) == 20
    assert len(out["files"]) == 50 and len(out["errors"]) == 10 and out["transcript_digest"]


def test_delegation_keeps_main_context_bounded(monkeypatch):
    monkeypatch.setenv("CAGE_AGENT_MODE", "orchestrator")
    import agent

    importlib.reload(agent)
    monkeypatch.setattr(agent, "CAGE_AGENT_MODE", "orchestrator")
    monkeypatch.setattr(agent, "AGENT_MODE", "orchestrator")

    def fake_delegate(task, tools=None, max_steps=8, budget_tokens=200000):
        return {
            "summary": "done:" + task[:20],
            "facts": {},
            "files": [],
            "errors": [],
            "steps_used": 2,
            "transcript_digest": "d",
        }

    monkeypatch.setattr(agent, "spawn_worker", fake_delegate)
    agent.HISTORY[:] = []
    agent.SUMMARIZE_CALLS[:] = []
    for i in range(10):
        agent.run_chat("任务%d" % i)
    from tests.helpers import agent_info_via_http

    info = agent_info_via_http()
    assert info["history_turns"] <= 10 and info["mode"] == "orchestrator"
    assert agent.SUMMARIZE_CALLS == []


def test_orchestrator_is_default_without_env(monkeypatch):
    monkeypatch.delenv("CAGE_AGENT_MODE", raising=False)
    import agent

    importlib.reload(agent)
    from tests.helpers import agent_info_via_http

    info = agent_info_via_http()
    assert info["mode"] == "orchestrator"
    assert set(info["tools"]) == {"delegate", "challenge_probe", "solve_challenge"}


def test_worker_concurrency_semaphore(monkeypatch):
    monkeypatch.setenv("CAGE_WORKER_CONCURRENCY", "2")
    assert workers.concurrency_limit() == 2
