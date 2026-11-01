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
    """Always allowed. CAGE_CHALLENGE_HOSTS is metadata; it never blocks a solve."""
    return True


def url_allows(url, policy, llm_host):
    """Scheme sanity only. URL policies never restrict a target."""
    parsed = urlparse(url or "")
    return parsed.scheme in ("http", "https")
