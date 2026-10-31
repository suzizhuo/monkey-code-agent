from pathlib import Path

from cage import cli
from cage.state import PROFILES


def test_help_lists_core_commands(capsys):
    cli.dispatch("help", [])
    out = capsys.readouterr().out
    for name in [
        "setup",
        "doctor",
        "init",
        "chat",
        "use",
        "status",
        "rebuild",
        "stop",
        "profiles",
        "serve",
        "help",
    ]:
        assert name in out
    for name in ["work", "key", "publish"]:
        assert name in out


def test_profiles_two_tiers(capsys):
    cli.cmd_profiles()
    out = capsys.readouterr().out
    assert "tooled" in out and "strict" in out
    assert set(PROFILES) == {"tooled", "strict"}


def test_readme_covers_required_sections():
    text = Path("README.md").read_text(encoding="utf-8")
    for needle in [
        "三分钟上手",
        "自主档位",
        "子代理",
        "人机验证",
        "沙箱与审计",
        "已知限制",
        "已知差异",
        "CAGE_WATCHDOG_SECS",
    ]:
        assert needle in text
    assert "工作区在哪" in text or "work" in text
    assert "GitHub" in text
