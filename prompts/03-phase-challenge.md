# 阶段 03：人机验证（Task 7）

状态：**已完成**。必读设计 §16、计划 Task 7。

## 目标

`challenge_probe` 把页面分成 `captcha|login|waf|rate_limit|none`。`solve_challenge` 只对 `CAGE_CHALLENGE_HOSTS` 命中的 host 走本地 OCR / `CAGE_SOLVER_CMD`；未命中返回 `host not in policy`。不集成付费打码。

## 文件

创建：`cage/challengerules.py`、`tests/test_challenge.py`、`tests/fixtures/challenge_{clean,captcha,login,waf,ratelimit}.html`。

修改：`agent/server.py` 的 `challenge_probe` / `solve_challenge` / `resolve_solver`。

## 分类规则（优先级）

1. waf：cloudflare / attention required / blocked
2. rate_limit：429 / too many requests / rate limit
3. captcha：recaptcha / hcaptcha / captcha 字段
4. login：username/password 字段或 log in / sign in
5. none

`classify_page(title, body, fields) -> (kind, evidence)`，evidence 必须非空。

`policy_allows(host, allowlist_csv)`：空串 False。

`url_allows(url, policy, llm_host)`：仅 http/https；LLM 网关 host 恒放行；`allowlist:a.com,b.com` 时只放名单。

`resolve_solver(env)`：`CAGE_SOLVER_CMD`（stdin=PNG 路径）→ `pytesseract` → `error: solver unavailable`。

`solve_challenge`：先 `url_allows` 再 `policy_allows`，都过才截图 `/work/_challenge.png` 调 solver，返回文本 + `escalated`。

夹具必须含 `<title>`。captcha 页含 `g-recaptcha` 与 `name="captcha"`。login 含 username/password。waf 含 Cloudflare Attention Required。ratelimit 含 429 Too Many Requests。

## 测试原文要点

```python
kind, evidence = rules.classify_page(title, re.sub(r"<[^>]+>", " ", html), fields)
assert kind == expected and evidence
assert not rules.policy_allows("c.com", "")
assert rules.url_allows("https://gw.example/v1/chat", "allowlist:other.com", "gw.example")
solver = agent.resolve_solver(os.environ)  # CAGE_SOLVER_CMD=/bin/echo ab12
assert solver("ignored.png").strip() == "ab12"
out = agent.solve_challenge.invoke({"url": "https://c.example/x", "kind": "captcha"})
assert "host not in policy" in out
```

红：`cannot import name 'challengerules'`。绿后：

`feat(challenge): rule-based page classification, policy-gated solver chain, escalation`

落地：`9404ab3`。验收 22–23。核验：`python3 -m pytest tests/test_challenge.py -q`
