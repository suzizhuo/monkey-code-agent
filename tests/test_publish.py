import json

import pytest

from cage import CageError
from cage import autonomy
from cage import publish


def test_remote_for_and_git_env():
    assert publish.remote_for("pat:tok123", "git@github.com:me/x.git").startswith(
        "https://x-access-token:tok123@github.com/"
    )
    assert publish.remote_for("ssh:/k/id", "git@github.com:me/x.git") == "git@github.com:me/x.git"
    env = publish.git_env("ssh:/k/id")
    assert env["GIT_SSH_COMMAND"].startswith("ssh -i /k/id") and "accept-new" in env["GIT_SSH_COMMAND"]


def test_publish_plan_requires_repo():
    with pytest.raises(CageError):
        publish.publish_plan("/w", "", "m", "main", "pat:x")


def test_publish_plan_sequence_and_branch():
    plan = publish.publish_plan("/w", "git@github.com:me/x.git", "msg", "dev", "ssh:/k/id")
    cmds = [c[1] for c in plan if len(c) > 1]
    assert "add" in cmds and "commit" in cmds and "push" in cmds
    assert any("dev" in " ".join(c) for c in plan)


def test_ensure_ssh_key_idempotent(tmp_path):
    p1, pub1 = publish.ensure_ssh_key(str(tmp_path))
    before = open(pub1).read()
    p2, _ = publish.ensure_ssh_key(str(tmp_path))
    assert p1 == p2 and open(pub1).read() == before
    assert open(pub1).read().startswith("ssh-ed25519")


def test_publish_audit_redacts_token():
    rec = autonomy.audit_record(
        "t",
        "default",
        "git_publish",
        {"repo": "git@github.com:me/x.git", "token": "ghp_secret"},
        5,
        "ok",
        9,
    )
    assert "ghp_secret" not in json.dumps(rec) and "github.com/me/x.git" in json.dumps(rec)


def test_git_publish_tool_ok_and_auth_failure(monkeypatch):
    import agent

    monkeypatch.setenv("CAGE_PUBLISH_REPO", "git@github.com:me/x.git")
    monkeypatch.setattr(
        agent,
        "run_publish",
        lambda plan, env: {"ok": True, "sha": "abc123", "files": 3, "error_kind": None},
    )
    out = agent.git_publish.invoke({"repo": "git@github.com:me/x.git", "message": "m"})
    assert "abc123" in out
    monkeypatch.setattr(
        agent,
        "run_publish",
        lambda plan, env: {"ok": False, "sha": "", "files": 0, "error_kind": "auth"},
    )
    assert "auth" in agent.git_publish.invoke({"repo": "r", "message": "m"})
