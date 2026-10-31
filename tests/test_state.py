import pytest

from cage import CageError
from cage import state


def test_state_roundtrip_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("CAGE_HOME", str(tmp_path))
    st = {"version": 1, "current": "default", "agents": {}}
    state.save_state(st)
    assert state.load_state() == st          # 原子写后可读回
    (tmp_path / "state.json").write_text("{ broken", encoding="utf-8")
    with pytest.raises(CageError):           # 坏 JSON 报错而非静默重置
        state.load_state()


def test_lock_file_created_and_released(tmp_path, monkeypatch):
    monkeypatch.setenv("CAGE_HOME", str(tmp_path))
    with state.state_lock():
        assert (tmp_path / "state.lock").exists()
    # 锁释放后可再次获取（同一进程二次进入）
    with state.state_lock():
        pass
