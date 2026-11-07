"""Host-side Isolis orchestrator unit tests (no Docker, no network)."""

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_ISOLIS = _ROOT / "isolis.py"


def _load():
    spec = importlib.util.spec_from_file_location("isolis_host", _ISOLIS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def isolis():
    return _load()


def test_name_sanitization(isolis):
    assert isolis.sanitize_name(" my agent!! ") == "my-agent"
    assert isolis.sanitize_name("a---b") == "a-b"
    assert isolis.sanitize_name("a___b") == "a___b"
    assert len(isolis.sanitize_name("x" * 100)) == 40
    assert isolis.sanitize_name("") == ""


def test_profiles_match_spec(isolis):
    assert set(isolis.PROFILES) == {"strict", "tooled"}
    strict = isolis.PROFILES["strict"]
    assert strict["read_only"] is True and strict["install"] is False
    assert strict["user"] == "nobody" and strict["caps"] == []
    assert strict["memory"] == "512m" and strict["cpus"] == "1.0"
    assert strict["pids"] == "128"
    assert strict["workspace"] == "512m" and strict["tmp"] == "64m"
    tooled = isolis.PROFILES["tooled"]
    assert tooled["read_only"] is False and tooled["install"] is True
    assert tooled["user"] == "0:0"
    assert tooled["caps"] == [
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETUID",
        "SETGID",
        "SETFCAP",
    ]
    assert tooled["memory"] == "4g" and tooled["cpus"] == "2.0"
    assert tooled["pids"] == "512"


def test_image_tag_distinct_per_profile_and_source(isolis):
    local_tooled = isolis.image_tag("tooled", "local")
    local_strict = isolis.image_tag("strict", "local")
    git_tooled = isolis.image_tag("tooled", "https://example.com/repo.git")
    assert local_tooled.startswith("isolis-agent:tooled-")
    assert len(local_tooled.split("-")[-1]) == 10
    assert len({local_tooled, local_strict, git_tooled}) == 3
    assert local_tooled == isolis.image_tag("tooled", "local")


def test_port_range_and_allocation(isolis, monkeypatch):
    assert (isolis.PORT_MIN, isolis.PORT_MAX) == (18080, 18119)
    monkeypatch.setattr(isolis, "port_in_use", lambda port: False)
    monkeypatch.setattr(isolis, "docker_published_ports", lambda: set())
    state = {"agents": {"a": {"port": 18080}, "b": {"port": 18081}}}
    assert isolis.allocate_port(state) == 18082
    monkeypatch.setattr(isolis, "port_in_use", lambda port: True)
    with pytest.raises(isolis.IsolisError):
        isolis.allocate_port({"agents": {}})


def test_blocked_model_skipped(isolis):
    assert isolis.is_banned_model("auto")
    assert isolis.is_banned_model("deepseek-chat")
    assert not isolis.is_banned_model("monkeycode-pro/gpt-5.6-terra")


def test_model_precedence(isolis, monkeypatch):
    monkeypatch.delenv("ISOLIS_DEFAULT_MODEL", raising=False)
    body = {"data": [{"id": "other/model"}, {"id": "monkeycode-pro/gpt-5.6-terra"}]}
    assert isolis.pick_model("explicit-model", body) == "explicit-model"
    assert isolis.pick_model("", body) == "monkeycode-pro/gpt-5.6-terra"
    assert isolis.pick_model("auto", body) == "monkeycode-pro/gpt-5.6-terra"


def test_state_defaults_and_roundtrip(isolis, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".isolis").mkdir()
    state = isolis.load_state()
    assert state == {"version": 1, "current": None, "agents": {}}
    state["agents"]["x"] = {
        "name": "x",
        "container": "isolis-x",
        "port": 18080,
        "hostname": "h",
        "model": "m",
        "profile": "strict",
        "source": "local",
        "created_at": "2026-01-01T00:00:00Z",
    }
    state["current"] = "x"
    isolis.save_state(state)
    assert isolis.load_state() == state
    (tmp_path / ".isolis" / "state.json").write_text("{ broken", encoding="utf-8")
    with pytest.raises(isolis.IsolisError):
        isolis.load_state()


def test_parse_options_rejects_unknown_and_missing(isolis):
    opts, positional = isolis.parse_options(
        ["name", "--profile", "strict", "--source", "local", "--model", "m"]
    )
    assert positional == ["name"]
    assert opts == {"profile": "strict", "source": "local", "model": "m"}
    with pytest.raises(isolis.IsolisError):
        isolis.parse_options(["--bogus"])
    with pytest.raises(isolis.IsolisError):
        isolis.parse_options(["--profile"])


def test_context_budget_clamped(isolis, monkeypatch, capsys):
    monkeypatch.setenv("ISOLIS_CONTEXT_TOKENS", "2000000")
    assert isolis.context_tokens() == 1000000
    assert "clamping" in capsys.readouterr().out


def test_missing_llm_reports_not_injected(isolis, monkeypatch):
    monkeypatch.delenv("MCAI_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("MCAI_LLM_API_KEY", raising=False)
    with pytest.raises(isolis.IsolisError) as exc:
        isolis.detect_llm()
    assert "not injected" in str(exc.value)


def _write_config(tmp_path, payload):
    (tmp_path / ".isolis").mkdir(exist_ok=True)
    import json
    (tmp_path / ".isolis" / "isolis.config.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_config_absent_uses_defaults(isolis, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = isolis.load_config()
    assert cfg["profiles"] == {} and cfg["prompt"] == ""
    key, profile = isolis.profile_config("tooled")
    assert key == "tooled" and profile["memory"] == "4g"


def test_config_profile_overrides_merge(isolis, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, {"profiles": {"tooled": {"memory": "8g", "pids": "1024"}}})
    _, profile = isolis.profile_config("tooled")
    assert profile["memory"] == "8g"
    assert profile["pids"] == "1024"
    assert profile["user"] == "0:0"
    assert profile["caps"] == isolis.PROFILES["tooled"]["caps"]


def test_config_can_add_new_profile(isolis, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, {"profiles": {"minimal": {"memory": "256m", "install": False, "read_only": True, "caps": [], "user": "nobody"}}})
    key, profile = isolis.profile_config("minimal")
    assert key == "minimal"
    assert profile["memory"] == "256m"
    assert profile["read_only"] is True
    assert profile["install"] is False


def test_config_rejects_bad_keys(isolis, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, {"profiles": {"tooled": {"memoryy": "8g"}}})
    with pytest.raises(isolis.IsolisError) as exc:
        isolis.load_config()
    assert "unknown keys" in str(exc.value)


def test_config_rejects_bad_types(isolis, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, {"profiles": {"tooled": {"caps": "ALL"}}})
    with pytest.raises(isolis.IsolisError) as exc:
        isolis.profile_config("tooled")
    assert "caps" in str(exc.value)


def test_config_prompt_and_prompt_file(isolis, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompt.md").write_text("from file", encoding="utf-8")
    _write_config(tmp_path, {"prompt_file": "prompt.md"})
    assert isolis.load_config()["prompt"] == "from file"
    _write_config(tmp_path, {"prompt": "  inline prompt  "})
    assert isolis.load_config()["prompt"] == "inline prompt"


def test_config_default_profile_and_model(isolis, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, {"default_profile": "strict", "model": "monkeycode-pro/x"})
    monkeypatch.delenv("ISOLIS_PROFILE", raising=False)
    monkeypatch.delenv("ISOLIS_MODEL", raising=False)
    assert isolis.profile_from_env() == "strict"
    assert isolis.load_config()["model"] == "monkeycode-pro/x"


def test_env_beats_config(isolis, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, {"default_profile": "strict"})
    monkeypatch.setenv("ISOLIS_PROFILE", "tooled")
    assert isolis.profile_from_env() == "tooled"


def test_config_broken_json_is_reported(isolis, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".isolis").mkdir()
    (tmp_path / ".isolis" / "isolis.config.json").write_text("{ nope", encoding="utf-8")
    with pytest.raises(isolis.IsolisError) as exc:
        isolis.load_config()
    assert "cannot read" in str(exc.value)


def test_start_container_forwards_prompt_and_limits(isolis, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, {
        "prompt": "custom body",
        "profiles": {"tooled": {"memory": "8g", "pids": "1024"}},
    })
    captured = {}

    def fake_run_docker(args, timeout=60, check=True):
        captured["args"] = list(args)
        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""
        return Proc()

    monkeypatch.setattr(isolis, "run_docker", fake_run_docker)
    isolis.start_container(
        "default", "isolis-default", 18080,
        {"model": "m"}, "tooled", "/workspace/agent", "local",
    )
    args = captured["args"]
    joined = " ".join(args)
    assert "ISOLIS_SYSTEM_PROMPT=custom body" in args
    assert "8g" in args and "1024" in args
    assert "--read-only" not in args
    assert "--user" in args and "0:0" in args
    assert "--cap-drop" in args and "ALL" in args
