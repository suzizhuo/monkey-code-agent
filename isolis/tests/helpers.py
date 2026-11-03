"""Test helpers: module loaders plus a live in-process agent server.

No Docker, no network, no platform LLM. The host and the agent are loaded
from the tree under test by path; the agent server binds 127.0.0.1 on an
ephemeral port inside the test process.
"""

import importlib.util
import os
import sys
import threading
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_SERVER = os.path.join(ROOT, "agent", "server.py")
HOST = os.path.join(ROOT, "isolis.py")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_host():
    if "isolis_host" in sys.modules:
        del sys.modules["isolis_host"]
    return load_module("isolis_host", HOST)


def load_agent():
    if "isolis_agent" in sys.modules:
        del sys.modules["isolis_agent"]
    return load_module("isolis_agent", AGENT_SERVER)


class LiveAgent(object):
    """Serve IsolisHandler on 127.0.0.1:<ephemeral> until closed."""

    def __init__(self, agent):
        self.agent = agent
        self.server = None
        self.thread = None

    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.agent.IsolisHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        return self.server.server_address[1]

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        return False


def make_tar_gz(directory):
    """tar.gz bytes of every entry in directory (arcnames are basenames)."""
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in sorted(os.listdir(directory)):
            tar.add(os.path.join(directory, name), arcname=name)
    return buffer.getvalue()


def evil_tar_gz(names, link=False):
    """tar.gz bytes with raw member names (traversal/link probes).

    With link=True every member is a real SYMTYPE entry pointing at
    /etc/passwd; the default builds regular files whose names are the probe.
    """
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in names:
            if link:
                info = tarfile.TarInfo(name=name)
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                info.size = 0
                tar.addfile(info)
            else:
                data = b"owned\n"
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()
