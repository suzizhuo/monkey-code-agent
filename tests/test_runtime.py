import os
import subprocess
import sys

from cage import runtime
from cage.state import PROFILES


def test_docker_run_args_isolation_invariants():
    cfg = PROFILES["tooled"]
    args = runtime.docker_run_args("default", 18080, "tooled", {"MCAI_LLM_API_KEY": "k"}, cfg)
    joined = " ".join(args)
    assert "--network cage-net" in joined and "--network-alias default" in joined
    assert "-p 127.0.0.1:18080:8080" in joined
    for t in ("/tmp", "/work", "/dev/shm"):
        assert "--tmpfs" in args and any(t in a for a in args)
    assert "no-new-privileges:true" in joined
    caps = [args[i + 1] for i, a in enumerate(args) if a == "--cap-add"]
    assert sorted(caps) == sorted(["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "SETFCAP"])
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" not in args and "--privileged" not in joined
    assert not any(a.startswith("-v") for a in args) and "docker.sock" not in joined


def test_docker_run_args_strict_profile_is_readonly_nobody():
    args = runtime.docker_run_args("s", 18081, "strict", {}, PROFILES["strict"])
    assert "--read-only" in args and args[args.index("--user") + 1] == "nobody"
    assert "--cap-add" not in args


def test_docker_run_args_forwards_model_and_limits_env_only(monkeypatch):
    # 宿主可能自带 JDK/Android；隔离后才能断言「瘦身环境零工具链键」
    for key in ("ANDROID_HOME", "JAVA_HOME", "ANDROID_SDK_ROOT"):
        monkeypatch.delenv(key, raising=False)
    args = runtime.docker_run_args("d", 18080, "tooled", {"MCAI_LLM_API_KEY": "k"}, PROFILES["tooled"])
    envs = [args[i + 1] for i, a in enumerate(args) if a == "-e"]
    assert "MCAI_LLM_API_KEY" in envs and "CAGE_CONTEXT_TOKENS=900000" in envs
    # Android/JDK 键只在宿主真提供时才转发；默认瘦身环境一个都不出现
    for key in ("ANDROID_HOME", "JAVA_HOME", "ANDROID_SDK_ROOT"):
        assert not any(e.startswith(key + "=") for e in envs)


def test_process_spawn_args_binds_loopback_and_detaches():
    spec = runtime.process_spawn_args("default", 18080, "tooled", {}, PROFILES["tooled"])
    assert spec["env"]["CAGE_BIND"] == "127.0.0.1"
    assert spec["logpath"].endswith(".cage/logs/default.log") and spec["detached"] is True
    assert spec["cwd"].endswith(".cage/work/default")


def test_process_runtime_stop_kills_process_group(monkeypatch, tmp_path):
    # 验收 #11：进程模式 stop 真能杀掉（先例线程方案做不到的）——用真子进程验证进程组可杀
    monkeypatch.setenv("CAGE_HOME", str(tmp_path))
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    proc_runtime = runtime.ProcessRuntime()
    proc_runtime._kill_group(proc.pid)
    proc.wait(timeout=5)
    assert proc.poll() is not None and pgid > 0
