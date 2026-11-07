"""Autonomy levels, action budget, and digested audit records."""

import hashlib
import json
from pathlib import Path

DESTRUCTIVE_TOOLS = frozenset(
    {"install_packages", "solve_challenge", "browse", "git_publish"}
)
_SENSITIVE_SUBSTR = ("key", "token", "cookie", "secret", "password")


def decide(tool, args, autonomy):
    level = (autonomy or "auto").strip().lower() or "auto"
    name = (tool or "").strip()
    if level == "manual":
        return "confirm"
    if level == "assisted" and name in DESTRUCTIVE_TOOLS:
        return "confirm"
    return "run"


def effective_autonomy(autonomy, usage, budget, today):
    level = (autonomy or "auto").strip().lower() or "auto"
    usage = usage or {}
    day = usage.get("day")
    count = int(usage.get("count") or 0)
    if day != today:
        return level
    try:
        limit = int(budget)
    except (TypeError, ValueError):
        limit = 500
    if count >= limit:
        return "assisted"
    return level


def consume(state, today, budget):
    state = state if isinstance(state, dict) else {}
    usage = dict(state.get("usage") or {})
    if usage.get("day") != today:
        usage = {"day": today, "count": 0}
    usage["count"] = int(usage.get("count") or 0) + 1
    state["usage"] = usage
    try:
        limit = int(budget)
    except (TypeError, ValueError):
        limit = 500
    return usage["count"] > limit


def is_sensitive_arg(name):
    text = (name or "").lower()
    return any(token in text for token in _SENSITIVE_SUBSTR)


def _digest_value(value):
    if isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


def audit_record(ts, agent, tool, args, output_len, status, duration_ms):
    digested = {}
    for key, value in (args or {}).items():
        if is_sensitive_arg(str(key)):
            digested[key] = _digest_value(value)
        elif str(key) == "repo":
            digested[key] = str(value).replace("git@github.com:", "github.com/")
        else:
            digested[key] = value
    return {
        "ts": ts,
        "agent": agent,
        "tool": tool,
        "args": digested,
        "output_len": output_len,
        "status": status,
        "duration_ms": duration_ms,
    }


def harvest_audit_lines(lines, path):
    path = Path(path)
    written = 0
    chunks = []
    for line in lines or []:
        text = line.strip() if isinstance(line, str) else ""
        if not text:
            continue
        try:
            rec = json.loads(text)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if not all(key in rec for key in ("ts", "agent", "tool")):
            continue
        chunks.append(json.dumps(rec, ensure_ascii=False))
        written += 1
    if chunks:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(chunks) + "\n")
    return written
