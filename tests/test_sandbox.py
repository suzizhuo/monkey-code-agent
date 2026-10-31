import json
import os
import subprocess
import sys
import time

from cage import autonomy
from cage import cli
from cage import runtime
from cage import state
from cage import workers
from cage.state import PROFILES


def test_harvest_audit_lines_skips_bad_lines(tmp_path):
    path = tmp_path / "a.jsonl"
    lines = [
        json.dumps(autonomy.audit_record("t", "default", "run_command", {"cmd": "ls"}, 5, "ok", 3)),
        "not-json",
        "",
    ]
    assert autonomy.harvest_audit_lines(lines, path) == 1
    assert len(path.read_text().strip().splitlines()) == 1


def test_watchdog_kills_stuck_worker():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    workers.watchdog_start(proc.pid, 1)
    time.sleep(2.5)
    assert proc.poll() is not None


def test_process_mode_install_default_off(monkeypatch):
    spec = runtime.process_spawn_args("d", 18080, "tooled", {}, PROFILES["tooled"], process_install=False)
    assert spec["env"]["CAGE_INSTALL"] == "0"
    spec2 = runtime.process_spawn_args("d", 18080, "tooled", {}, PROFILES["tooled"], process_install=True)
    assert spec2["env"]["CAGE_INSTALL"] == "1"


def test_status_prints_watchdog_and_work_usage(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CAGE_HOME", str(tmp_path))
    monkeypatch.setenv("CAGE_WATCHDOG_SECS", "30")
    state.save_state({"version": 1, "current": None, "agents": {}})
    monkeypatch.setattr(cli, "detect_mode", lambda: "process")
    cli.cmd_status()
    out = capsys.readouterr().out.lower()
    assert "watchdog" in out and "work" in out
