"""Pure name, slug, image tag, and port allocation helpers."""

import hashlib
import re

from cage import CageError

NAME_MAX = 40
PORT_MIN = 18080
PORT_MAX = 18119


def sanitize_name(raw):
    if raw is None:
        raw = ""
    value = re.sub(r"[^A-Za-z0-9_-]", "-", str(raw))
    value = re.sub(r"-+", "-", value)
    value = value.strip("-_")
    value = value[:NAME_MAX].strip("-_")
    return value


def announce_name(raw, name):
    if raw is not None and name != raw:
        print("notice: agent name sanitized to '%s'" % name)


def source_slug(url):
    slug = re.sub(r"[^A-Za-z0-9_.-]", "-", (url or "").rstrip("/").split("/")[-1])
    return slug.replace(".git", "") or "agent-src"


def image_tag(profile, source, base_image):
    digest = hashlib.sha256(
        ("%s\n%s\n%s" % (profile, source, base_image)).encode("utf-8")
    ).hexdigest()[:10]
    return "cage-agent:%s-%s" % (profile, digest)


def allocate_port(recorded, published, listening, exclude):
    busy = set(recorded) | set(published) | set(listening) | set(exclude)
    for port in range(PORT_MIN, PORT_MAX + 1):
        if port not in busy:
            return port
    raise CageError(
        "no free port in %d-%d (stop an agent and retry)" % (PORT_MIN, PORT_MAX)
    )
