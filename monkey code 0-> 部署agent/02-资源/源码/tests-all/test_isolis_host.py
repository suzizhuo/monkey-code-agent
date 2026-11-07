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
