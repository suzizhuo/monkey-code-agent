import os
import re

import pytest

from cage import challengerules as rules


@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("clean", "none"),
        ("captcha", "captcha"),
        ("login", "login"),
        ("waf", "waf"),
        ("ratelimit", "rate_limit"),
    ],
)
def test_classify_fixtures(fixture, expected):
    html = open("tests/fixtures/challenge_%s.html" % fixture, encoding="utf-8").read()
    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
    fields = re.findall(r'name="([^"]+)"', html)
    kind, evidence = rules.classify_page(title, re.sub(r"<[^>]+>", " ", html), fields)
    assert kind == expected and evidence


def test_policy_gates_solving():
    assert rules.policy_allows("a.com", "a.com,b.com") and not rules.policy_allows("c.com", "a.com")
    assert not rules.policy_allows("c.com", "")


def test_url_policy():
    assert rules.url_allows("https://a.com/x", "all", "gw.example")
    assert not rules.url_allows("ftp://a.com/x", "all", "gw.example")
    assert rules.url_allows("https://gw.example/v1/chat", "allowlist:other.com", "gw.example")
    assert not rules.url_allows("https://other.com/x", "allowlist:a.com", "gw.example")


def test_solver_hook_and_backend_chain(monkeypatch):
    monkeypatch.setenv("CAGE_SOLVER_CMD", "/bin/echo ab12")
    import agent

    solver = agent.resolve_solver(os.environ)
    assert solver("ignored.png").strip() == "ab12"
    monkeypatch.delenv("CAGE_SOLVER_CMD")
    monkeypatch.setattr(agent, "pytesseract", None)
    out = agent.resolve_solver(os.environ)("x.png")
    assert out.startswith("error: solver unavailable")


def test_solve_challenge_refuses_non_policy_host(monkeypatch):
    monkeypatch.setenv("CAGE_CHALLENGE_HOSTS", "")
    monkeypatch.setenv("CAGE_CHALLENGE_TOOLS", "1")
    import agent
    import importlib

    importlib.reload(agent)
    out = agent.solve_challenge.invoke({"url": "https://c.example/x", "kind": "captcha"})
    assert "host not in policy" in out
