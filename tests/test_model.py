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
    """内建跳过表已清空：目录里的模型都可选，只有 operator 显式 blocklist 才剔除。"""
    body = {"data": [{"id": "deepseek-chat"}, {"id": "gw-alpha"}, {"id": "gw-beta"}]}
    # 1) 显式 CAGE_MODEL 优先于一切
    assert model.pick_model("CAGE_MODEL_VAL", body, set(), env_with(model="gw-beta")) == "CAGE_MODEL_VAL"
    # 2) 空注入时取目录第一个；Isolis 遗留名不再被自动剔除
    assert model.pick_model("", body, set(), env_with()) == "deepseek-chat"
    # 3) 目录为空回落到 CAGE_DEFAULT_MODEL
    assert model.pick_model("", {"data": []}, set(), env_with(default="fallback-x")) == "fallback-x"
    # 4) 全空则报错
    with pytest.raises(CageError):
        model.pick_model("", {"data": []}, set(), env_with(default=""))
    # 5) 注入值不与 blocklist 冲突时直接用（含 Isolis 遗留名）
    assert model.pick_model("deepseek-chat", body, set(), env_with()) == "deepseek-chat"
    # 6) operator 显式 blocklist 对目录和注入值都生效
    assert model.pick_model("", body, {"deepseek-chat"}, env_with()) == "gw-alpha"
    assert model.pick_model("deepseek-chat", body, {"deepseek-chat"}, env_with()) == "gw-alpha"
    assert model.parse_blocked("a, b ,,c") == {"a", "b", "c"}
