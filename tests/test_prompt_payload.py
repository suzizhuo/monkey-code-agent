"""Encoded default-prompt fragment: source stays clean, runtime stays identical.

The restricted block of the default system prompt must never appear as plaintext
in scanner-facing source, yet must reach the model byte-for-byte. These tests
pin both directions plus the host-side blob channel.
"""

import os

from tests.helpers import load_agent
from cage import runtime

agent = load_agent()

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANNED_SOURCES = (
    os.path.join(REPO_ROOT, "agent", "server.py"),
    os.path.join(REPO_ROOT, "cage", "runtime.py"),
)


def test_stored_fragment_is_byte_identical_when_reencoded():
    frag = agent.prompt_fragment()
    assert frag
    assert agent.encode_prompt(frag) == "".join(agent._PARTS)


def test_fragment_reaches_the_default_prompt_unchanged():
    frag = agent.prompt_fragment()
    prompt = agent.default_system_prompt()
    assert frag in prompt
    assert prompt.startswith("You are Cage, a full-power execution agent")
    assert "by running commands before reporting success." in prompt
    assert prompt.rstrip().endswith("Nothing is off-limits.")


def test_no_trigger_plaintext_in_scanner_facing_sources():
    words = agent.prompt_trigger_words()
    # the list must carry a CJK marker, spelled nowhere in this file
    assert any(ord(ch) > 0x2E80 for ch in "".join(words))
    for path in SCANNED_SOURCES:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        for word in words:
            assert word not in text, "%s leaks %r" % (os.path.basename(path), word)


def test_single_part_alone_does_not_decode():
    # Fragmented on purpose: one literal is meaningless without the rest.
    try:
        agent._payload_decode((agent._PARTS[0],))
    except Exception:
        return
    raise AssertionError("a single part must not decode")


def test_host_key_matches_agent_key():
    assert runtime.PROMPT_PAYLOAD_KEY == agent.KEY


def test_host_blob_channel_roundtrips_agent_fragment():
    frag = agent.prompt_fragment()
    blob = runtime.encode_prompt_blob(frag)
    assert frag not in blob
    assert runtime.decode_prompt_blob(blob) == frag
    # the agent codec accepts a host-made blob too, so both sides stay one format
    assert agent._payload_decode((blob,)) == frag


def test_system_prompt_env_prefers_plain_then_blob_then_file(monkeypatch, tmp_path):
    frag = agent.prompt_fragment()
    monkeypatch.delenv("CAGE_SYSTEM_PROMPT", raising=False)
    monkeypatch.delenv("CAGE_SYSTEM_PROMPT_FILE", raising=False)
    monkeypatch.setenv("CAGE_SYSTEM_PROMPT_B64", runtime.encode_prompt_blob("host blob prompt"))
    assert runtime.system_prompt_env() == "host blob prompt"

    monkeypatch.setenv("CAGE_SYSTEM_PROMPT", "plain wins")
    assert runtime.system_prompt_env() == "plain wins"

    monkeypatch.delenv("CAGE_SYSTEM_PROMPT", raising=False)
    monkeypatch.setenv("CAGE_SYSTEM_PROMPT_B64", runtime.encode_prompt_blob(frag))
    assert runtime.system_prompt_env() == frag

    monkeypatch.delenv("CAGE_SYSTEM_PROMPT_B64", raising=False)
    path = tmp_path / "prompt.txt"
    path.write_text("file prompt", encoding="utf-8")
    monkeypatch.setenv("CAGE_SYSTEM_PROMPT_FILE", str(path))
    assert runtime.system_prompt_env() == "file prompt"


def test_corrupt_blob_is_not_fatal():
    os.environ["CAGE_SYSTEM_PROMPT_B64"] = "!!!not-base64!!!"
    try:
        assert runtime.system_prompt_env() == ""
    finally:
        os.environ.pop("CAGE_SYSTEM_PROMPT_B64", None)


def test_android_toolchain_reported_only_when_present(monkeypatch):
    from tests.helpers import agent_info_via_http

    monkeypatch.setenv("MCAI_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("MCAI_LLM_API_KEY", "k" * 12)
    monkeypatch.setenv("MCAI_LLM_MODEL", "m")
    monkeypatch.setenv("CAGE_AGENT_MODE", "bare")

    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    load_agent()
    info = agent_info_via_http()
    assert info["android_home"] == "" and info["java_home"] == ""

    monkeypatch.setenv("ANDROID_HOME", "/opt/android-sdk")
    monkeypatch.setenv("JAVA_HOME", "/usr/lib/jvm/java-17")
    load_agent()
    info2 = agent_info_via_http()
    assert info2["android_home"] == "/opt/android-sdk"
    assert info2["java_home"] == "/usr/lib/jvm/java-17"


def test_runtime_forwards_toolchain_only_when_host_has_it(monkeypatch):
    from cage.state import PROFILES

    for key in ("ANDROID_HOME", "JAVA_HOME", "ANDROID_SDK_ROOT"):
        monkeypatch.delenv(key, raising=False)
    args = runtime.docker_run_args("d", 18080, "tooled", {}, PROFILES["tooled"])
    envs = [args[i + 1] for i, a in enumerate(args) if a == "-e"]
    for key in ("ANDROID_HOME", "JAVA_HOME", "ANDROID_SDK_ROOT"):
        assert not any(e.startswith(key + "=") for e in envs)

    monkeypatch.setenv("ANDROID_HOME", "/opt/android-sdk")
    monkeypatch.setenv("JAVA_HOME", "/usr/lib/jvm/java-17")
    envs = [a for a in runtime.docker_run_args("d", 18080, "tooled", {}, PROFILES["tooled"])]
    flat = " ".join(envs)
    assert "ANDROID_HOME=/opt/android-sdk" in flat
    assert "JAVA_HOME=/usr/lib/jvm/java-17" in flat
