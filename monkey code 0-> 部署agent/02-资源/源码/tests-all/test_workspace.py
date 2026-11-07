import threading
from http.server import ThreadingHTTPServer

import pytest

from cage import CageError
from cage import workspace
from tests.helpers import http_get_json


def test_render_and_parse_selection():
    out = workspace.render_listing(
        "src",
        [
            {"name": "a.py", "dir": False, "size": 12, "mtime": "10-31 12:00"},
            {"name": "sub", "dir": True, "size": 4096, "mtime": "10-31 12:01"},
        ],
    )
    assert out.splitlines()[0].split()[1] == "1" and "a.py" in out
    assert "/sub" in out or "sub/" in out
    assert workspace.parse_selection("2", 2) == 2
    for bad in ("0", "9", "abc"):
        with pytest.raises(CageError):
            workspace.parse_selection(bad, 2)


def test_host_dest_allowed():
    assert workspace.host_dest_allowed("out.txt", "/home/u") and workspace.host_dest_allowed(
        "d/out.txt", "/home/u"
    )
    assert not workspace.host_dest_allowed("../x", "/home/u")
    assert not workspace.host_dest_allowed("/etc/passwd", "/home/u")


def test_files_endpoint_list_and_read(monkeypatch, tmp_path):
    import agent

    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(agent, "WORKSPACE", str(tmp_path))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), agent.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s, b, _ = http_get_json(srv.server_address[1], "/files?path=.")
        assert s == 200 and b["entries"][0]["name"] == "f.txt"
        s, b, _ = http_get_json(srv.server_address[1], "/files?path=../etc/passwd")
        assert s == 400
    finally:
        srv.shutdown()


def test_extract_roundtrip_process_mode(tmp_path):
    src = tmp_path / "w"
    src.mkdir()
    (src / "a.txt").write_text("payload", encoding="utf-8")
    dest = tmp_path / "out"
    workspace.extract({"mode": "process", "root": str(src)}, "a.txt", str(dest / "a.txt"))
    assert (dest / "a.txt").read_text(encoding="utf-8") == "payload"
