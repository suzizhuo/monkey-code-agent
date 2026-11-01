"""Git publish helpers: SSH key bootstrap, remotes, and command plans."""

import os
import subprocess

from cage import CageError


def ensure_ssh_key(keydir):
    os.makedirs(keydir, exist_ok=True)
    priv = os.path.join(keydir, "id_ed25519")
    pub = priv + ".pub"
    if not os.path.isfile(priv) or not os.path.isfile(pub):
        proc = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", priv],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            raise CageError("ssh-keygen failed: %s" % ((proc.stderr or proc.stdout or "").strip()))
    return priv, pub


def _owner_repo(repo):
    text = (repo or "").strip()
    if "github.com:" in text:
        text = text.split("github.com:", 1)[1]
    elif "github.com/" in text:
        text = text.split("github.com/", 1)[1]
    text = text.lstrip("/")
    if text.endswith(".git"):
        return text
    return text + ".git" if text else text


def remote_for(auth, repo):
    auth = auth or ""
    if auth.startswith("pat:"):
        token = auth.split(":", 1)[1]
        return "https://x-access-token:%s@github.com/%s" % (token, _owner_repo(repo))
    return repo or ""


def git_env(auth):
    auth = auth or ""
    if auth.startswith("ssh:"):
        key = auth.split(":", 1)[1]
        return {
            "GIT_SSH_COMMAND": "ssh -i %s -o StrictHostKeyChecking=accept-new" % key
        }
    return {}


def publish_plan(workdir, repo, message, branch, auth):
    repo = (repo or "").strip() or os.environ.get("CAGE_PUBLISH_REPO", "").strip()
    branch = branch or "main"
    message = message or "update"
    remote = remote_for(auth, repo)
    return [
        ["git", "init"],
        ["git", "add", "."],
        ["git", "commit", "-m", message],
        ["git", "branch", "-M", branch],
        ["git", "remote", "add", "origin", remote],
        ["git", "push", "-u", "origin", branch],
    ]


def _error_kind(text):
    low = (text or "").lower()
    if any(token in low for token in ("auth", "permission", "denied", "403", "401")):
        return "auth"
    if any(token in low for token in ("rejected", "non-fast-forward")):
        return "rejected"
    if any(token in low for token in ("could not resolve", "network", "timed out", "timeout")):
        return "network"
    return "remote"


def run_publish(plan, env):
    merged = dict(os.environ)
    extra = dict(env or {})
    cwd = extra.pop("CAGE_WORKDIR", None)
    merged.update(extra)
    for cmd in plan or []:
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd or None,
                env=merged,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception:
            return {"ok": False, "sha": "", "files": 0, "error_kind": "network"}
        if proc.returncode != 0:
            return {
                "ok": False,
                "sha": "",
                "files": 0,
                "error_kind": _error_kind((proc.stderr or "") + (proc.stdout or "")),
            }
    sha = ""
    files = 0
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd or None,
            env=merged,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode == 0:
            sha = (proc.stdout or "").strip()
        counted = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            cwd=cwd or None,
            env=merged,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if counted.returncode == 0:
            files = len([line for line in (counted.stdout or "").splitlines() if line.strip()])
    except Exception:
        pass
    return {"ok": True, "sha": sha, "files": files, "error_kind": None}
