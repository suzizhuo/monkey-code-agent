"""Host-side tests: naming, state, ports, model pick, batch/broadcast,
export/import against a live in-process agent. No Docker, no network, no LLM.
"""

import json
import os

import pytest

from tests.helpers import LiveAgent, load_agent, load_host, make_tar_gz


@pytest.fixture()
def host(tmp_path, monkeypatch):
    module = load_host()
    state_dir = tmp_path / ".isolis"
    monkeypatch.setattr(module, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(module, "STATE_FILE", str(state_dir / "state.json"))
    monkeypatch.setattr(module, "docker_published_ports", lambda: set())
    return module


# ------------------------------------------------------------- naming/state


def test_sanitize_name_rules(host):
    assert host.sanitize_name("My Agent!!") == "My-Agent"
    assert host.sanitize_name("--x--") == "x"
    assert host.sanitize_name(None) == ""
    assert len(host.sanitize_name("a" * 60)) == host.NAME_MAX


def test_state_roundtrip_and_bad_json(host, tmp_path):
    host.save_state({"current": "a", "agents": {"a": {"port": 18080}}})
    assert host.load_state()["agents"]["a"]["port"] == 18080
    path = host.STATE_FILE
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{ not json")
    with pytest.raises(Exception):
        host.load_state()


def test_allocate_port_skips_recorded_and_busy(host, monkeypatch):
    monkeypatch.setattr(host, "port_in_use", lambda port: port == host.PORT_MIN + 1)
    state = {"agents": {"x": {"port": host.PORT_MIN}}}
    assert host.allocate_port(state) == host.PORT_MIN + 2


def test_profile_config(host):
    assert host.profile_config("tooled")[1]["install"] is True
    assert host.profile_config("strict")[1]["user"] == "nobody"
    with pytest.raises(Exception):
        host.profile_config("nope")


def test_image_tag_format(host):
    tag = host.image_tag("tooled", "local")
    assert tag.startswith("isolis-agent:tooled-")
    assert host.image_tag("strict", "local") != host.image_tag("tooled", "local")


def test_banned_models(host):
    assert host.is_banned_model("auto") is True
    assert host.is_banned_model("monkeycode-pro/deepseek-chat") is True
    assert host.is_banned_model("monkeycode-pro/minimax-m3") is False


def test_pick_model_order(host, monkeypatch):
    body = {"data": [{"id": "monkeycode-pro/first"}, {"id": "deepseek-chat"}]}
    monkeypatch.delenv("ISOLIS_DEFAULT_MODEL", raising=False)
    assert host.pick_model("injected/model", body) == "injected/model"
    assert host.pick_model("", body) == "monkeycode-pro/first"
    assert host.pick_model("auto", body) == "monkeycode-pro/first"
    assert host.pick_model("", {}) == host.DEFAULT_MODEL


def test_parse_options(host):
    opts, positional = host.parse_options(["--profile", "strict", "name"])
    assert opts["profile"] == "strict" and positional == ["name"]
    with pytest.raises(Exception):
        host.parse_options(["--nope"])
    with pytest.raises(Exception):
        host.parse_options(["--profile"])


# ------------------------------------------------------- batch/broadcast/etc


def _fake_state(host, ports):
    host.save_state(
        {
            "current": "alpha",
            "agents": {
                "alpha": {"port": ports["alpha"], "profile": "tooled"},
                "beta": {"port": ports["beta"], "profile": "strict"},
            },
        }
    )


def test_resolve_agent_port(host):
    with pytest.raises(Exception):
        host.resolve_agent_port()
    host.save_state({"current": "alpha", "agents": {"alpha": {"port": 18099}}})
    assert host.resolve_agent_port() == ("alpha", 18099)
    assert host.resolve_agent_port("alpha")[1] == 18099


def test_batch_runs_tasks_and_writes_jsonl(host, tmp_path, monkeypatch, capsys):
    host.save_state({"current": "alpha", "agents": {"alpha": {"port": 18099}}})
    monkeypatch.setattr(host, "chat_request", lambda port, message, **kw: "reply:" + message)
    tasks = tmp_path / "tasks.txt"
    tasks.write_text(
        "# comment\nbeta|second task\n\nplain task\n", encoding="utf-8"
    )
    out = tmp_path / "out"
    host.cmd_batch([str(tasks), "--out", str(out)])
    files = list(out.iterdir())
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["agent"] == "alpha"  # beta not in state -> falls back to current
    assert rows[1]["message"] == "plain task"
    assert all("reply" in row for row in rows)


def test_broadcast_reaches_every_agent(host, monkeypatch, capsys):
    host.save_state(
        {
            "current": "alpha",
            "agents": {
                "alpha": {"port": 18099, "profile": "tooled"},
                "beta": {"port": 18098, "profile": "strict"},
            },
        }
    )
    monkeypatch.setattr(host, "chat_request", lambda port, message, **kw: "r@%d" % port)
    host.cmd_broadcast(["hello", "world"])
    out = capsys.readouterr().out
    assert "== alpha" in out and "== beta" in out
    assert "r@18099" in out and "r@18098" in out
    assert "broadcast: 2/2 agents answered" in out


def test_status_json(host, capsys):
    host.cmd_status(json_mode=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["agents"] == []


def test_save_transcript(host):
    host.save_transcript("alpha", [("q1", "a1"), ("q2", "a2")])
    folder = host.STATE_DIR
    files = [name for name in os.listdir(os.path.join(folder, "transcripts"))]
    assert len(files) == 1
    text = open(os.path.join(folder, "transcripts", files[0]), encoding="utf-8").read()
    assert "## you" in text and "a2" in text


# ------------------------------------------------------- export/import live


def test_export_import_roundtrip_with_live_agent(host, tmp_path):
    module = load_agent()
    module.WORKSPACE = str(tmp_path / "work")
    os.makedirs(module.WORKSPACE, exist_ok=True)
    module.write_file("deliverable.txt", "from-the-sandbox")
    with LiveAgent(module) as port:
        host.save_state({"current": "alpha", "agents": {"alpha": {"port": port}}})
        target = tmp_path / "pull.tgz"
        host.cmd_export(["alpha", str(target)])
        assert target.exists() and target.stat().st_size > 0
        # import the pulled archive back into a second workspace
        second = tmp_path / "work2"
        module.WORKSPACE = str(second)
        host.cmd_import([str(target), "alpha"])
        assert (second / "deliverable.txt").read_text() == "from-the-sandbox"
