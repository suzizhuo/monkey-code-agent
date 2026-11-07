"""Numbered workspace browser: list, read, extract."""

import os
import shutil
import time

from cage import CageError


def render_listing(path, entries):
    lines = []
    for index, entry in enumerate(entries or [], 1):
        name = entry.get("name") or ""
        if entry.get("dir") and not str(name).endswith("/"):
            name = str(name) + "/"
        kind = "dir" if entry.get("dir") else "file"
        lines.append(
            "%s\t%s\t%s\t%s\t%s"
            % (
                path or ".",
                index,
                kind,
                entry.get("size", 0),
                "%s\t%s" % (entry.get("mtime", ""), name),
            )
        )
    return "\n".join(lines)


def parse_selection(text, count):
    raw = (text or "").strip()
    if not raw.isdigit():
        raise CageError("not a number: %s" % raw)
    value = int(raw)
    if value < 1 or value > int(count or 0):
        raise CageError("out of range: %s" % raw)
    return value


def host_dest_allowed(dest, cwd):
    cwd_abs = os.path.realpath(cwd or ".")
    dest_abs = os.path.realpath(os.path.join(cwd_abs, dest or ""))
    return dest_abs == cwd_abs or dest_abs.startswith(cwd_abs + os.sep)


def list_workspace(handle, rel="."):
    handle = handle or {}
    if handle.get("mode") == "process" or handle.get("root"):
        root = handle.get("root")
        target = os.path.join(root, rel or ".")
        entries = []
        if not os.path.isdir(target):
            return entries
        for item in sorted(os.scandir(target), key=lambda e: e.name):
            stat = item.stat()
            entries.append(
                {
                    "name": item.name,
                    "dir": item.is_dir(),
                    "size": stat.st_size,
                    "mtime": time.strftime("%m-%d %H:%M", time.localtime(stat.st_mtime)),
                }
            )
        return entries
    return []


def read_workspace(handle, rel):
    handle = handle or {}
    root = handle.get("root") or "."
    target = os.path.join(root, rel or "")
    with open(target, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def extract(handle, rel, dest):
    handle = handle or {}
    dest = str(dest)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if handle.get("mode") == "process" or handle.get("root"):
        src = os.path.join(handle.get("root") or ".", rel or "")
        shutil.copy2(src, dest)
        return dest
    from cage.runtime import run_docker

    name = handle.get("name") or "default"
    run_docker(["cp", "cage-%s:/work/%s" % (name, rel or ""), dest], timeout=30, check=True)
    return dest
