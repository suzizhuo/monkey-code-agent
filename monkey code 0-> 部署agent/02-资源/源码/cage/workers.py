"""Orchestrator/worker delegation: toolsets, spawn, clamp, watchdog."""

import hashlib
import json
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from cage.state import home

ORCHESTRATOR_TOOLS = ["delegate", "challenge_probe", "solve_challenge"]
WORKER_BASELINE = frozenset(
    {
        "run_command",
        "write_file",
        "read_file",
        "list_files",
        "browse",
        "install_packages",
        "challenge_probe",
        "solve_challenge",
        "git_publish",
    }
)
_RESULTS = {}
_SEM = None
_SEM_LIMIT = None


def orchestrator_tools():
    return list(ORCHESTRATOR_TOOLS)


def worker_allowed_tools(requested):
    out = []
    for name in requested or []:
        if name == "delegate":
            continue
        if name in WORKER_BASELINE:
            out.append(name)
    return out


def clamp_worker_result(result):
    raw = result if isinstance(result, dict) else {}
    summary = str(raw.get("summary") or "")
    if len(summary) > 800:
        summary = summary[:800]
    facts = raw.get("facts") or {}
    if isinstance(facts, dict):
        facts = dict(list(facts.items())[:20])
    else:
        facts = {}
    files = list(raw.get("files") or [])[:50]
    errors = list(raw.get("errors") or [])[:10]
    digest = raw.get("transcript_digest")
    if not digest:
        digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]
    return {
        "summary": summary,
        "facts": facts,
        "files": files,
        "errors": errors,
        "steps_used": raw.get("steps_used", 0),
        "transcript_digest": digest,
    }


def concurrency_limit():
    raw = os.environ.get("CAGE_WORKER_CONCURRENCY", "3").strip() or "3"
    try:
        value = int(raw)
    except ValueError:
        value = 3
    return max(1, value)


def _semaphore():
    global _SEM, _SEM_LIMIT
    limit = concurrency_limit()
    if _SEM is None or _SEM_LIMIT != limit:
        _SEM = threading.BoundedSemaphore(limit)
        _SEM_LIMIT = limit
    return _SEM


def watchdog_start(pid, seconds):
    try:
        secs = float(seconds)
    except (TypeError, ValueError):
        return None
    if secs <= 0 or not pid:
        return None

    def _kill():
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    timer = threading.Timer(secs, _kill)
    timer.daemon = True
    timer.start()
    return timer


def _worker_log_dir():
    path = home() / "logs" / "workers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def spawn(task, tools, max_steps, budget_tokens, mode):
    worker_id = "w-" + uuid.uuid4().hex[:10]
    allowed = worker_allowed_tools(tools)
    result = {
        "summary": "worker %s accepted: %s" % (worker_id, (task or "")[:80]),
        "facts": {},
        "files": [],
        "errors": [],
        "steps_used": 0,
        "transcript_digest": hashlib.sha256(worker_id.encode("utf-8")).hexdigest()[:16],
        "worker_id": worker_id,
        "task": task,
        "tools": allowed,
        "max_steps": max_steps,
        "budget_tokens": budget_tokens,
        "mode": mode,
    }
    sem = _semaphore()
    sem.acquire()
    try:
        _RESULTS[worker_id] = clamp_worker_result(result)
        log_path = _worker_log_dir() / ("%s.jsonl" % worker_id)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": worker_id, "task": task, "tools": allowed}) + "\n")
    finally:
        sem.release()
    return worker_id


def harvest_worker_result(worker_id):
    rec = _RESULTS.get(worker_id)
    if rec is None:
        return {
            "summary": "unknown worker",
            "facts": {},
            "files": [],
            "errors": ["unknown worker %s" % worker_id],
            "steps_used": 0,
            "transcript_digest": "",
        }
    return rec


def spawn_and_wait(task, tools=None, max_steps=8, budget_tokens=200000, mode="process"):
    worker_id = spawn(task, tools or [], max_steps, budget_tokens, mode)
    return harvest_worker_result(worker_id)
