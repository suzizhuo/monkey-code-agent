import pytest

from cage import CageError
from cage import cli
from cage import state


FAKE_LLM = {
    "base_url": "https://gw.example/v1",
    "api_key": "k",
    "model": "m",
    "host": "gw.example",
    "key_len": 1,
    "key_prefix": "***",
}

FAKE_INFO = {
    "ok": True,
    "via": "mc-direct",
    "injected": True,
    "hostname": "agent",
    "model": "m",
    "profile": "tooled",
    "tools": [],
    "llm_host": "gw.example",
}


class FakeRuntime:
    started = 0
    removed = 0

    def ensure_image(self, profile, source, agent_dir, base_image, force=False):
        return "cage-agent:tooled-test"

    def ensure_network(self):
        return None

    def start(self, name, port, profile, env):
        FakeRuntime.started += 1
        return "cage-" + name

    def status(self, name):
        return "running"

    def port_of(self, name):
        return 18080

    def stop(self, name):
        FakeRuntime.removed += 1

    def logs(self, name):
        return ""


def _seed_agent(profile="tooled", source="local", mode="docker"):
    state.save_state(
        {
            "version": 1,
            "current": "default",
            "agents": {
                "default": {
                    "name": "default",
                    "container": "cage-default",
                    "port": 18080,
                    "hostname": "agent",
                    "model": "m",
                    "profile": profile,
                    "source": source,
                    "mode": mode,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            },
        }
    )


def _stub_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("CAGE_HOME", str(tmp_path))
    FakeRuntime.started = 0
    FakeRuntime.removed = 0
    monkeypatch.setattr(cli, "detect_mode", lambda: "docker")
    monkeypatch.setattr(cli, "runtime_for", lambda mode=None: FakeRuntime())
    monkeypatch.setattr(cli, "detect_llm", lambda: FAKE_LLM)
    monkeypatch.setattr(cli, "wait_health", lambda *a, **k: True)
    monkeypatch.setattr(cli, "verify_agent", lambda port: dict(FAKE_INFO))
    monkeypatch.setattr(cli, "resolve_agent_dir", lambda source: str(tmp_path))


def test_init_reuses_matching_agent(monkeypatch, tmp_path):
    _stub_cli(monkeypatch, tmp_path)
    _seed_agent(profile="tooled", source="local", mode="docker")
    cli.cmd_init("default")
    assert FakeRuntime.started == 0 and state.load_state()["current"] == "default"


def test_init_recreates_when_profile_changed(monkeypatch, capsys, tmp_path):
    _stub_cli(monkeypatch, tmp_path)
    _seed_agent(profile="strict", source="local", mode="docker")
    cli.cmd_init("default")
    assert "profile or source changed" in capsys.readouterr().out
    assert FakeRuntime.removed == 1 and FakeRuntime.started == 1


def test_chat_request_parses_reply(monkeypatch):
    monkeypatch.setattr(cli, "http_json", lambda *a, **k: (200, {"ok": True, "reply": "好的"}))
    assert cli.chat_request(18080, "hi") == "好的"


def test_dispatch_rejects_unknown_option():
    with pytest.raises(CageError):
        cli.dispatch("init", ["--bogus", "x"])


def test_tui_command_surface_unchanged(capsys):
    for cmd in ["help"]:
        cli.dispatch(cmd, [])
    out = capsys.readouterr().out
    for name in ["setup", "doctor", "init", "chat", "use", "status", "rebuild", "stop", "profiles"]:
        assert name in out
    assert "serve" in out
    assert "isolis" not in out.lower()
