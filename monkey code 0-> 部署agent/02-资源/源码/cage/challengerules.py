"""Rule-based challenge classification and URL/host policy."""

from urllib.parse import urlparse


def classify_page(title, body, fields):
    title = title or ""
    body = body or ""
    fields = [str(item) for item in (fields or [])]
    blob = " ".join([title, body, " ".join(fields)]).lower()
    fieldset = [item.lower() for item in fields]
    if "cloudflare" in blob or "attention required" in blob or "blocked" in blob:
        return "waf", "waf:cloudflare"
    if "429" in blob or "too many requests" in blob or "rate limit" in blob:
        return "rate_limit", "rate_limit:429"
    if (
        "recaptcha" in blob
        or "hcaptcha" in blob
        or "captcha" in blob
        or "captcha" in fieldset
    ):
        return "captcha", "captcha:field"
    if (
        "password" in fieldset
        or "username" in fieldset
        or "log in" in blob
        or "sign in" in blob
        or "login" in blob
    ):
        return "login", "login:form"
    return "none", "none:clean"


def policy_allows(host, allowlist_csv):
    raw = (allowlist_csv or "").strip()
    if not raw:
        return False
    allowed = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return (host or "").strip().lower() in allowed


def url_allows(url, policy, llm_host):
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.netloc.split("@")[-1].split(":")[0]
    llm_host = (llm_host or "").split(":")[0]
    if llm_host and (host == llm_host or host.endswith("." + llm_host)):
        return True
    text = (policy or "all").strip()
    if not text or text == "all":
        return True
    if text.startswith("allowlist:"):
        allowed = {
            part.strip().lower()
            for part in text.split(":", 1)[1].split(",")
            if part.strip()
        }
        return host.lower() in allowed
    return True
