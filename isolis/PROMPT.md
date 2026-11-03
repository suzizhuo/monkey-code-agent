# Isolis prompt v1.3 (compressed driver; repairs the failed 2026-11-02 run)

Authority: ISOLIS_PROMPT.md (incl. v1.1/v1.2/v1.3) outranks this file. On
conflict the full spec applies and this file gets fixed, never the reverse.
Defect ledger: spec v1.2 C (D1-D7) and v1.3 D8 - init died with "docker CLI
not found" on a Docker-less host and the whole run collapsed to NOT VERIFIED;
process mode (spec v1.3 H) is the fix.

## Files

isolis.py (host, stdlib + Docker CLI only), agent/server.py (provider-neutral
runtime), agent/Dockerfile per spec 9.3, agent/install-android-sdk.sh,
.isolis/state.json (gitignored, atomic write). Hard pins: requirements.txt
carries langchain-core, an OpenAI-capable client, an Anthropic-capable
client, playwright, tiktoken; tests/ runs python3 -m pytest tests/ -q with
no Docker/network/LLM.

## TUI

python3 isolis.py -> isolis>; one-shot same commands: setup doctor init [name]
chat use <name> status rebuild stop [name] export [name] [path] import
<file.tgz> [name] batch <tasks-file> [--out DIR] broadcast <message> profiles
help quit/exit. Interactive: blank lines ignored, one error line then loop, EOF/Ctrl-C exits
0. One-shot: 0 success, 1 on IsolisError/unknown; quit is illegal one-shot. status --json prints JSON only. you>: text -> POST /chat;
/export [path] /import <file> /save [path] /back /quit /help; slash commands
case-insensitive; chat errors print one line and continue; EOF/Ctrl-C returns
to parent. Name default default; sanitize [A-Za-z0-9_-], collapse separators,
max 40, notice on change; container isolis-<name>; ports 18080-18119 first
free (skip recorded/published/listening); exhaust is a clear error.

## Isolation (all MUST; spec section 4)

Per profile: dedicated bridge net icc off, alias = name; one port on
127.0.0.1; no host mount, no docker.sock, no docker exec; no-new-privileges;
cap-drop ALL then allowlist; tmpfs /tmp /work /dev/shm; pid/memory/cpu
limits; one agent per container; never --privileged, never SYS_ADMIN.
strict = ro root, nobody, no caps, install off, 5 tools, 512m/1cpu/pids128.
tooled (default) = rw root 0:0, caps exactly CHOWN DAC_OVERRIDE FOWNER SETUID
SETGID SETFCAP, install on, 7 tools, 4g/2cpu/pids512.

## LLM (spec section 5)

Platform-injected gateway only: MCAI_LLM_BASE_URL MCAI_LLM_API_KEY
MCAI_LLM_MODEL. Missing base or key: init/setup/chat/rebuild fail with one
line stating the LLM was not injected; no second gateway, no invented key, no
.env, no vendor URL/key prompts; never print the key (diagnostics: length and
prefix only). An unreachable model list degrades to the injected name with
one notice, never a failure.

Model order: ISOLIS_MODEL > MCAI_LLM_MODEL > first gateway-advertised model
sharing the platform prefix > ISOLIS_DEFAULT_MODEL > built-in constant. No
vendor model names anywhere, including the constant. Blocked ids are skipped,
never fatal.

Protocol negotiation (mandatory): probe {base}/chat/completions, one token,
six seconds; HTTP 404/405 selects the Anthropic-style client, anything else
the OpenAI-style client; cache per process; a 404/405 during chat flips the
cached decision and retries once. Anthropic-style: strip a trailing /v1
first; timeout 60, max_retries 1, temperature 0.4, max_tokens 4096.
OpenAI-style: ChatOpenAI(base_url, api_key, model, timeout=60,
max_retries=1, temperature=0.4); never append a second /v1. Requirements must
carry an Anthropic-capable client or a stdlib implementation of both wire
paths, each really runnable (D6). Acceptance 12 uses a local 404 stub.

## Env into the container (exact whitelist; D4/D5)

MCAI_LLM_BASE_URL MCAI_LLM_API_KEY MCAI_LLM_MODEL=<resolved>
ISOLIS_AGENT_NAME ISOLIS_PROFILE ISOLIS_INSTALL ISOLIS_MAX_TURNS
ISOLIS_MAX_MESSAGE_CHARS ISOLIS_CONTEXT_TOKENS ISOLIS_CONTEXT_HARD_LIMIT
ISOLIS_COMPRESS_BATCH ISOLIS_ARCHIVE_MAX ISOLIS_SOURCE, plus
ISOLIS_SYSTEM_PROMPT, ISOLIS_DEFAULT_MODEL and ISOLIS_MODEL when set. The
host resolves the model and passes it as MCAI_LLM_MODEL; /info reports it.
ISOLIS_FALLBACK_MODEL does not exist. Container -e and the
process mode share one container_env() mapping: both paths carry this list.

## HTTP (container :8080; spec section 8)

Common fields agent hostname pid cwd on every JSON reply. GET /health,
GET /info (spec 8.2/8.5 fields incl. protocol and memory counters), GET
/history (live + archive + counters), POST /chat {"message"} -> {ok, reply,
tools_used[], memory{...}}; empty/non-string message 400 message is empty;
bad JSON 400 invalid json; unknown 404; failure 500 error+trace. Ready only
when /info confirms injected and via. Poll 0.5s/3s/45s.

## Memory (canonical model; D1/D2; spec 8.4 + v1.2 D + v1.3 K)

- A turn is one user+assistant pair. history_turns counts live pairs.
  ISOLIS_MAX_TURNS (400) caps pairs; overflowing PAIRS leave from the front.
- The archive records one entry per MESSAGE {role, content, reason, at},
  reason evicted or compressed; archived_turns counts archived messages
  (newest ISOLIS_ARCHIVE_MAX 4000 kept); archive content never reaches the
  model.
- ISOLIS_COMPRESS_BATCH (40) counts MESSAGES folded per pass, in whole pairs
  (odd rounds down); compressed_turns counts folded MESSAGES cumulatively.
- Trigger: live + system + marker estimate exceeds ISOLIS_CONTEXT_TOKENS
  (900000; 0 disables; clamped to ISOLIS_CONTEXT_HARD_LIMIT 1000000 with one
  notice line). Estimate: tiktoken when available, else ceil(chars/4).
- Fold product: one SummaryMessage (assistant role, content starts with
  [compressed history]) at the front of live history, at most one ever; a
  later pass merges into it in place; the newest pair is never folded;
  passes repeat until within budget or no eligible pair; the marker is never
  evicted. Facts, paths, commands, versions, decisions, errors, open
  questions preserved; cap 8000 chars from 60000 input chars.
- Per-message cap ISOLIS_MAX_MESSAGE_CHARS (900000): head+tail with an
  explicit removed-count marker; /chat reports trimmed_input and
  trimmed_reply. Tool loop stops after 8 round trips; tool output over 20000
  chars is truncated with a marker.
- System prompt: ISOLIS_SYSTEM_PROMPT when set, else built-in (isolated
  container; no host filesystem, siblings, or Docker socket; match the user's
  language; concise; states whether install is allowed).

## /files (no mounts, no socket; D3, v1.2 E + v1.3 L)

GET /files returns the workspace as tar.gz (cap 512m). POST /files accepts
tar.gz into the workspace (cap 512m). Pre-scan members before anything
touches disk; ValueError on: absolute path, any .. segment, issym(),
islnk(), ischr(), isblk(), isdev(), isfifo(). Then extract with filter="data"
where the running Python supports it, otherwise an equivalent manual pass
(regular files and directories only; final resolved path stays inside the
workspace). Never skip a check because of a Python version. Host: export
[name] [path] / import <file.tgz> [name]; chat /export /import.

## Flows (spec section 10, condensed + process mode v1.3 H)

init/chat/rebuild/stop/setup/doctor follow spec section 10. Past-failure
deltas: post-start failure removes the container and prints the last log
line; a profile/source change recreates with one notice; doctor's
injected -> exit 0 branch needs a stubbed unit test, not an environment
excuse.

detect_mode(): no docker CLI, or the daemon does not answer docker info, =>
process. In process mode init prints one runtime: process mode line, skips
image build and network creation, and spawns agent/server.py detached
(start_new_session, stdout/stderr to .isolis/logs/<name>.log) on
127.0.0.1:<allocated port> (never 0.0.0.0); workspace
.isolis/workspaces/<name>, HOME and TIKTOKEN_CACHE_DIR under .isolis/run/,
pid+port meta atomically at .isolis/run/<name>.json. stop kills the process
group (TERM then KILL, bounded); chat/status/rebuild route by the recorded
mode; state records the additive field mode. doctor reports [warn] (never
[fail]) for a missing CLI or daemon and exits zero when the LLM is
injected; setup prints one line and continues in process mode. A Docker-less
host is a degraded runtime, never a skipped acceptance; status says the
isolation line describes the configured profile, not a live container.

Source: empty/local/builtin -> agent/; else a Git URL, shallow clone to
.isolis/agent-src/<slug>/, fast-forward pull on later runs (non-ff reported),
Dockerfile required; precedence --source > ISOLIS_AGENT_SOURCE > local.
State per spec 11 (+ mode).

## Acceptance mapping

Every item of spec section 13 (29) plus the v1.1 items (default tooled, 7/5
tools, image contents, /files, export/import/batch/broadcast/status --json,
chat slash commands, /info extras, memory archive) maps to a named test or an
explicit NOT VERIFIED line. Process-mode items map to
tests/test_process_mode.py (detect_mode, container_env whitelist, process
start/health/stop, init without Docker, doctor exit codes, local 404/200
protocol stubs).

## Final report contract (an overclaim is a failed run)

1. Paste the verbatim tail of python3 -m pytest tests/ -q; test counts and
   pass/fail numbers come only from that output.
2. Per acceptance item exactly one status: PASS <test name> /
   FAIL <test name> / NOT VERIFIED <reason>. Nothing may be called
   implemented or in place without a named passing test.
3. grep the code before claiming a capability (Anthropic client, env
   whitelist entries, endpoints).
4. "No Docker or MCAI_LLM_* on this machine" only justifies a NOT VERIFIED
   line, and must name the stub test covering that branch; never a silent
   skip. A missing Docker CLI or daemon never justifies skipping an item.
5. The failed attempt's own answer is not evidence; reproduce its bugs with
   new tests first.

## Style

ASCII only, no emoji. One clear error line naming the next step. The only
model upstream is the injected gateway. tooled trades a writable container
filesystem for development power; it never gains a host mount, the Docker
socket, or sibling reach.
