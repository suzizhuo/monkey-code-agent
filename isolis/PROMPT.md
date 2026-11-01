# Isolis prompt v1.1 (compressed, no custom API)

Isolis: host TUI that runs isolated LangChain agents in Docker and chats over `127.0.0.1:<port>` HTTP. Host: stdlib + Docker CLI only. LangChain lives in the container. MUST failures are defects. No custom/vendor API. Default profile `tooled`: the agent configures its own environment (SDKs, JDKs, toolchains, browsers) inside the sandbox; the isolation boundary never moves.

## Goals

User types in the TUI → `init` builds one isolated container + agent → `chat` uses localhost HTTP → agents isolated from host and each other; no host mounts, no Docker socket.

## Files

- `isolis.py` host orchestrator
- `agent/server.py` in-container LangChain HTTP runtime (tools + bounded memory)
- `agent/Dockerfile` `python:3.12-slim-bookworm`, JDK17/build/cmake/node, Playwright Chromium, Android SDK preinstalled, tini, `EXPOSE 8080`, `CMD python /app/server.py`; `PYTHONUNBUFFERED=1` `PIP_NO_CACHE_DIR=1` `TIKTOKEN_CACHE_DIR=/tmp` `HOME=/tmp`
- `agent/install-android-sdk.sh` idempotent Android SDK installer (`ANDROID_PACKAGES`, `CMDLINE_VERSION` overrides)
- `agent/requirements.txt` langchain-core / langchain-openai / playwright / tiktoken
- `.isolis/state.json` current name/container/port; gitignore `.isolis/`; atomic replace on write
- `tests/` host + agent unit tests, no Docker/network/LLM; `python3 -m pytest tests/ -q`

## TUI

`python3 isolis.py` → `isolis>`. Same subcommands work one-shot then exit.

`init [name]` `chat` `use <name>` `status` `rebuild` `stop [name]` `export [name] [path]` `import <file.tgz> [name]` `batch <tasks-file> [--out DIR]` `broadcast <message>` `profiles` `help` `quit`/`exit`

Interactive: ignore blank lines; one error, keep looping; EOF/Ctrl-C at prompt exits 0. One-shot: 0 on success, 1 on IsolisError/unknown; one-shot `quit` is illegal. `status --json` prints machine-readable state.

`you>`: text → POST /chat; `/export [path]` pull workspace tar.gz; `/import <file>` push tar.gz; `/save [path]` save transcript markdown; `/back` to isolis; `/quit` exit; `/help`. Slash commands case-insensitive. Chat errors print one line and continue. EOF/Ctrl-C returns to parent.

Default name `default`. Sanitize to `[A-Za-z0-9_-]`, max 40 (illegal → `-`, collapse, strip `-_`). If changed, print a notice. Container `isolis-<name>`. Ports 18080–18119, first free (skip recorded + in-use); exhaust → error.

## Isolation (all MUST)

Image `isolis-agent:<profile>-<source-hash>`; network `isolis-net`, alias = agent name; no `-v`/socket; tmpfs `/tmp /work /dev/shm`; `no-new-privileges`; `cap-drop ALL` then allowlist; mem/cpu/pid limits; one port bound `127.0.0.1`; one agent per container. No `docker exec` as a control plane. `enable_icc=false`. Never `--privileged`, SYS_ADMIN, Docker socket.

- strict: read-only root, `nobody`, no caps, install off; 512m/1cpu/pids128, /work 512m; tools = run_command, write_file, read_file, list_files, browse (5).
- tooled (default): rw root `0:0`; caps `CHOWN DAC_OVERRIDE FOWNER SETUID SETGID SETFCAP`; install on; 4g/2cpu/pids512, /work 2g; tools add install_packages + install_android_sdk (7). `browse` = headless Chromium, optional screenshot into /work.

## LLM: MonkeyCode only, self-detect

Access point = `MCAI_LLM_BASE_URL` (often `/v1`). Before start/`init`/`rebuild`/`chat`, detect once and stop. Read `MCAI_LLM_BASE_URL` `MCAI_LLM_API_KEY` `MCAI_LLM_MODEL`; model = `ISOLIS_MODEL` > injected > default `monkeycode-pro/gpt-5.6-terra`; skip `auto`/`step-5-preview`/`deepseek-chat`. GET `{BASE}/models` Bearer: 200/404/405 = up; 401/403 = fail, never switch gateways. No curl (stdlib only). Never print the full key. `/info` MUST include `model`, `llm_host`, `via=mc-direct`, `injected`, `tools`, `profile`, `install`, `workspace`, memory stats. Missing BASE/KEY: one line `MonkeyCode LLM was not injected...`, fail `init`/`rebuild`/`chat`; `/health` still ok; `/chat` 500.

Into the container: `-e MCAI_LLM_BASE_URL` `-e MCAI_LLM_API_KEY` `-e MCAI_LLM_MODEL=<resolved>`; `ChatOpenAI(base_url=BASE, api_key=KEY, model=MODEL, timeout=60, max_retries=1, temperature=0.4)`; `bind_tools` with OpenAI-format schemas; do not append a second `/v1`.

## HTTP (container 8080)

Common fields `agent hostname pid cwd`. GET /health `{ok:true}`. GET /info (above + `framework langchain source android_home uptime_s started_at history_turns context_tokens max_turns compress_batch archived_turns`). GET /history live + archive + counters. POST /chat `{"message"}` → 200 `{ok,reply,tools_used[],memory{...}}`; empty/non-string 400 `message is empty`; bad JSON 400 `invalid json`; unknown 404; failure 500 error+trace. Tool loop ≤ 8 steps; reply after tools.

Memory: system + last `ISOLIS_MAX_TURNS` (400) pairs; older archive `evicted`, never sent; live+system > `ISOLIS_CONTEXT_TOKENS` (900000, 0=off) folds oldest 40 pairs into one `[compressed history]` system message (newest pair never folded; folded → archive `compressed`); tiktoken when present else chars/4; per-message cap 900000 with middle truncation.

Files (no mounts, no socket): GET /files → tar.gz of workspace (cap 512m). POST /files → tar.gz extracted into workspace, cap 512m, reject absolute paths, `..`, symlinks and devices.

## Flows

init: docker up → detect → build/reuse tag → ensure isolis-net → rm -f same name → docker run (isolation + MCAI + Android PATH) → /health within 45s → /info injected and via=mc-direct → save state. Fail after start: remove container, last log line. Reuse healthy same-name; recreate on unhealthy.

chat: ensure running; print /info one line; loop POST /chat timeout 120s. use/status/rebuild/stop per state; `stop` removes container and record. export/import/batch/broadcast resolve agents from state over loopback HTTP; batch writes JSONL `.isolis/batch/`; transcripts `.isolis/transcripts/`; exports `.isolis/exports/`.

state `{version:1,current,agents:{name:{name,container,port,hostname,model,profile,source,created_at}}}`.

## Style

ASCII only, no emoji; short commands; one clear error plus next step; isolation is not optional; power inside the sandbox is.
