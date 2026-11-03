"""Agent-side tests: tools, confinement, memory, tar safety, HTTP contract.

The agent module imports with or without langchain (guarded import), so every
test here runs on a plain interpreter: no Docker, no network, no LLM.
"""

import json
import os
import time

import pytest

from tests.helpers import LiveAgent, evil_tar_gz, load_agent, make_tar_gz


@pytest.fixture()
def agent(tmp_path):
    module = load_agent()
    module.WORKSPACE = str(tmp_path / "work")
    os.makedirs(module.WORKSPACE, exist_ok=True)
    return module


# ------------------------------------------------------------------ tools


def test_workspace_path_rejects_escape(agent):
    with pytest.raises(ValueError):
        agent._workspace_path("../outside.txt")


def test_workspace_path_allows_inside(agent):
    assert agent._workspace_path("sub/dir.txt").startswith(os.path.realpath(agent.WORKSPACE))


def test_write_and_read_roundtrip(agent):
    assert agent.write_file("a/b.txt", "hello") == "wrote 5 chars to a/b.txt"
    assert agent.read_file("a/b.txt") == "hello"


def test_write_file_rejects_escape(agent):
    assert agent.write_file("../evil.txt", "x").startswith("error:")


def test_list_files(agent):
    agent.write_file("one.txt", "1")
    agent.write_file("sub/two.txt", "2")
    listing = agent.list_files(".")
    assert "one.txt" in listing and "sub/" in listing


def test_run_command_echo(agent):
    out = agent.run_command("echo hi")
    assert "hi" in out and "[exit 0]" in out


def test_run_command_timeout(agent, monkeypatch):
    monkeypatch.setattr(agent, "SHELL_TIMEOUT", 1)
    assert "timed out" in agent.run_command("sleep 5")


def test_install_packages_refuses_in_strict(agent, monkeypatch):
    monkeypatch.setattr(agent, "INSTALL_ENABLED", False)
    assert "strict profile" in agent.install_packages("pip", "requests")


def test_install_android_sdk_refuses_in_strict(agent, monkeypatch):
    monkeypatch.setattr(agent, "INSTALL_ENABLED", False)
    assert "strict profile" in agent.install_android_sdk("platform-tools")


def test_browse_rejects_non_http(agent):
    assert agent.browse("ftp://example.com").startswith("error:")


def test_tool_counts_per_profile(agent):
    assert len(agent.select_tool_schemas(False)) == 5
    assert len(agent.select_tool_schemas(True)) == 7
    assert "install_android_sdk" in agent.tool_names(True)
    assert "browse" in agent.tool_names(False)


def test_call_tool_unknown(agent):
    assert "unknown tool" in agent.call_tool("nope", {})


# ----------------------------------------------------------------- memory


def _message(module, role, content):
    return getattr(module, role)(content=content)


def test_memory_trims_and_archives(agent, monkeypatch):
    # ISOLIS v1.2 D: MAX_TURNS counts pairs; the archive records one entry
    # per evicted MESSAGE. Six pairs, cap two pairs -> four pairs evicted
    # -> eight archived messages.
    monkeypatch.setattr(agent, "MAX_TURNS", 2)
    memory = agent.Memory()
    for index in range(6):
        memory.append(_message(agent, "HumanMessage", "q%d" % index))
        memory.append(_message(agent, "AIMessage", "a%d" % index))
    assert memory.live_turns() == 2
    assert len(memory.archive) == 8
    assert memory.archive[0]["reason"] == "evicted"
    assert all(entry["reason"] == "evicted" for entry in memory.archive)
    live, _, _, _ = memory.snapshot()
    assert [entry["role"] for entry in live] == [
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
        "AIMessage",
    ]


def test_memory_compress_folds_oldest_pairs(agent, monkeypatch):
    # ISOLIS v1.2 D: COMPRESS_BATCH counts MESSAGES per pass in whole pairs;
    # compressed_turns counts folded MESSAGES; one marker entry only; the
    # newest pair is never folded; passes repeat until no pair is eligible.
    monkeypatch.setattr(agent, "CONTEXT_TOKENS", 1)
    monkeypatch.setattr(agent, "COMPRESS_BATCH", 2)

    class FakeLLM(object):
        def invoke(self, messages):
            class Response(object):
                content = "FACTS: kept"

            return Response()

    memory = agent.Memory()
    for index in range(4):
        memory.append(_message(agent, "HumanMessage", "q%d" % index))
        memory.append(_message(agent, "AIMessage", "a%d" % index))
    assert memory.maybe_compress(FakeLLM()) is True
    live, archive, compressed, summary = memory.snapshot()
    assert summary.startswith(agent.SUMMARY_MARKER)
    assert "FACTS" in summary
    assert compressed == 6
    assert len(live) == 3
    assert len(archive) == 6
    assert all(entry["reason"] == "compressed" for entry in archive[:6])
    marker_entries = [
        entry for entry in live if str(entry.get("content", "")).startswith(
            agent.SUMMARY_MARKER
        )
    ]
    assert len(marker_entries) == 1
    assert [entry["content"] for entry in live if entry["role"] == "HumanMessage"] == [
        "q3"
    ]
    assert [entry["content"] for entry in live if entry["role"] == "AIMessage"] == [
        "a3"
    ]


def test_memory_compress_off_when_zero(agent, monkeypatch):
    monkeypatch.setattr(agent, "CONTEXT_TOKENS", 0)
    memory = agent.Memory()
    for index in range(2):
        memory.append(_message(agent, "HumanMessage", "q%d" % index))
        memory.append(_message(agent, "AIMessage", "a%d" % index))
    assert memory.maybe_compress(None) is False


# --------------------------------------------------------------------- tar


def test_tar_roundtrip(agent, tmp_path):
    agent.write_file("one.txt", "1")
    blob = agent.build_workspace_tar()
    other = tmp_path / "other"
    assert agent.extract_workspace_tar(str(other), blob) >= 1
    assert (other / "one.txt").read_text() == "1"


def test_tar_rejects_traversal(agent):
    with pytest.raises(ValueError):
        agent.extract_workspace_tar(agent.WORKSPACE, evil_tar_gz(["../evil.txt"]))


def test_tar_rejects_symlink(agent):
    # ISOLIS v1.2 E: the probe member must be a real SYMTYPE entry with an
    # external linkname, not a regular file merely named "link".
    with pytest.raises(ValueError):
        agent.extract_workspace_tar(
            agent.WORKSPACE, evil_tar_gz(["link"], link=True)
        )


# -------------------------------------------------------------------- http


def test_health_and_info(agent):
    with LiveAgent(agent) as port:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=5) as r:
            assert json.loads(r.read().decode())["ok"] is True
        with urllib.request.urlopen("http://127.0.0.1:%d/info" % port, timeout=5) as r:
            info = json.loads(r.read().decode())
        assert info["framework"] in ("langchain", "stdlib")
        assert info["injected"] in (True, False)
        assert info["tools"] == agent.tool_names()
        assert info["workspace"] == agent.WORKSPACE


def test_chat_without_injection_is_500(agent, monkeypatch):
    monkeypatch.setattr(agent, "HAS_LANGCHAIN", True)
    monkeypatch.setattr(agent, "INJECTED", False)
    with LiveAgent(agent) as port:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            "http://127.0.0.1:%d/chat" % port,
            data=json.dumps({"message": "hi"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        assert caught.value.code == 500
        assert "injected" in caught.value.read().decode()


def test_chat_bad_json_is_400(agent):
    with LiveAgent(agent) as port:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            "http://127.0.0.1:%d/chat" % port, data=b"not json", method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        assert caught.value.code == 400


def test_unknown_path_is_404(agent):
    with LiveAgent(agent) as port:
        import urllib.error
        import urllib.request

        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen("http://127.0.0.1:%d/nope" % port, timeout=5)
        assert caught.value.code == 404


def test_files_roundtrip_over_http(agent):
    agent.write_file("deliverable.txt", "result")
    with LiveAgent(agent) as port:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:%d/files" % port, timeout=10) as r:
            blob = r.read()
        other = os.path.join(agent.WORKSPACE, "incoming")
        assert agent.extract_workspace_tar(other, blob) >= 1
        assert open(os.path.join(other, "deliverable.txt")).read() == "result"
        request = urllib.request.Request(
            "http://127.0.0.1:%d/files" % port,
            data=make_tar_gz(agent.WORKSPACE),
            method="POST",
            headers={"Content-Type": "application/gzip"},
        )
        with urllib.request.urlopen(request, timeout=10) as r:
            payload = json.loads(r.read().decode())
        assert payload["ok"] is True and payload["files"] >= 1
