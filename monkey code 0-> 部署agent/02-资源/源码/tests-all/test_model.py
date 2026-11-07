import pytest

from cage import CageError
from cage import model


def env_with(model="", default=""):
    return {
        "MCAI_LLM_MODEL": model,
        "CAGE_DEFAULT_MODEL": default,
    }


def test_detect_llm_missing_vars_one_line_error(monkeypatch):
    monkeypatch.delenv("MCAI_LLM_BASE_URL", raising=False)
    with pytest.raises(CageError) as e:
        model.detect_llm()
    assert "not injected" in str(e.value)


def test_pick_model_priority_and_blocklist():
    body = {"data": [{"id": "deepseek-chat"}, {"id": "gw-alpha"}, {"id": "gw-beta"}]}
    assert model.pick_model("CAGE_MODEL_VAL", body, set(), env_with(model="gw-beta")) == "CAGE_MODEL_VAL"
    assert model.pick_model("", body, set(), env_with()) == "gw-alpha"
    assert model.pick_model("", {"data": []}, set(), env_with(default="fallback-x")) == "fallback-x"
    with pytest.raises(CageError):
        model.pick_model("", {"data": []}, set(), env_with(default=""))
    assert model.pick_model("deepseek-chat", body, set(), env_with()) == "gw-alpha"
    assert model.parse_blocked("a, b ,,c") == {"a", "b", "c"}
