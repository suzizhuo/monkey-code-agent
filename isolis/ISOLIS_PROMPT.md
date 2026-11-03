# Isolis: Implementation Prompt

A complete, normative specification for building Isolis from scratch. Every
requirement marked **MUST** is a defect if absent. **SHOULD** marks recommended
behavior. **MAY** marks optional behavior.

Read sections 1 through 5 to understand the system and its security boundary.
Implement against section 13, the acceptance contract. Section 14 lists the
constraints that survive implementation.

The document is model-neutral and agent-neutral. Do not assume a specific
vendor, wire protocol, model family, or upstream agent repository. Everything
host-specific is supplied at runtime through environment variables and command
line options.

### Contents

| Section | Scope |
|---|---|
| 1 | Purpose and system overview |
| 2 | Deliverables and file responsibilities |
| 3 | Host bootstrap: setup and doctor |
| 4 | Capability profiles and the isolation contract |
| 5 | LLM injection, provider neutrality, protocol negotiation |
| 6 | Agent source selection |
| 7 | Terminal UI and command line |
| 8 | HTTP API contract |
| 9 | Agent tools and development capability |
| 10 | Lifecycle command flows |
| 11 | State file schema |
| 12 | Output style rules |
| 13 | Acceptance criteria |
| 14 | Known constraints |

---

## 1. Purpose

Isolis is a host-side terminal UI that runs an isolated coding agent inside a
Docker container and talks to it over loopback HTTP.

The user types commands in the TUI. `init` builds or reuses a container image
for a chosen capability profile and agent source, starts one container, and
registers one agent. `chat` converses with that agent over
`127.0.0.1:<port>`. Agents are isolated from the host and from each other. The
host mounts nothing into agent containers and never exposes the Docker socket
to them.

The host process uses only the Python standard library plus the Docker CLI. All
agent framework code lives inside the container.

Two design goals pull in opposite directions and both are mandatory:

1. Strong isolation by default: the strict profile denies filesystem writes
   outside tmpfs, drops every capability, and runs as an unprivileged user.
2. Real development work: the tooled profile lets the agent install system and
   language packages (including the Android SDK), compile code, and run build
   tools inside its own container.

The profile is an explicit, auditable choice. The agent gets exactly the
capabilities of the profile it runs under, and nothing from the host.

---

## 2. Deliverables

| Path | Responsibility |
|---|---|
| `isolis.py` | Host orchestrator: TUI, Docker lifecycle, profiles, port allocation, LLM detection, agent source resolution, state |
| `agent/server.py` | In-container HTTP runtime: provider-neutral LLM client, tool loop, conversation memory |
| `agent/Dockerfile` | Container image build, capability-profile aware |
| `agent/requirements.txt` | Pinned LangChain client packages and Playwright |
| `agent/install-android-sdk.sh` | Idempotent Android command-line tools installer |
| `.isolis/state.json` | Runtime state (gitignored) |
| `.isolis/agent-src/` | Cloned third-party agent sources (gitignored) |

`.isolis/` MUST be listed in `.gitignore`. State writes MUST be atomic: write a
temporary file, flush and fsync it, then `os.replace` it over the real file.

The host MUST NOT import any third-party Python package. Standard library and
subprocess calls to the Docker CLI are the only host dependencies.

---

## 3. Host bootstrap: setup and doctor

Environment preparation is automated so a new machine reaches a working agent
without manual Docker administration.

### 3.1 `setup`

`setup` MUST perform these steps in order and print one line per step:

1. Verify the Docker CLI exists. Fail with a clear message if it does not.
2. Resolve the LLM configuration through the same path `init` uses (section 5).
3. Ensure a container registry is reachable. If Docker Hub is blocked and no
   mirror is configured, add a mirror to the daemon configuration.
4. Ensure the Docker daemon is running, starting it if needed.
5. Resolve the agent source and build the image for the selected profile.
6. Create the dedicated bridge network if missing.
7. Print the resolved profile, agent source, image tag, and network.

`setup` MUST NOT start an agent. It prepares the host and stops.

### 3.2 Registry mirror policy

MUST:

- Probe Docker Hub before touching configuration.
- Add a mirror only when Hub is unreachable and no mirror is already configured.
- Treat an unreadable or invalid daemon configuration as an error and leave it
  untouched rather than overwriting it.
- Make the operation idempotent: running `setup` twice changes nothing the
  second time.

### 3.3 Daemon autostart

`init`, `chat`, `setup`, and `rebuild` MUST call a single daemon-ensure routine
before any Docker operation that needs a server. The routine:

- Returns immediately when the daemon already responds.
- Only attempts to start a daemon when running as root.
- Starts the daemon detached, in its own session, logging to a known path.
- Polls for readiness with a bounded timeout and reports the log path on failure.

### 3.4 `doctor`

`doctor` MUST print a checklist of `[ok]`, `[warn]`, and `[fail]` lines covering:

- Docker CLI version.
- Daemon reachability.
- Registry reachability or configured mirror.
- LLM environment variables, host, resolved model, and key length and prefix
  (never the key).
- Presence of local agent sources.
- Selected capability profile and whether package installation is enabled.
- Selected agent source.
- Image presence for the current profile and source.
- Network presence.
- State file readability.

`doctor` MUST exit zero when there is no `[fail]` line and non-zero otherwise.
`[warn]` lines MUST NOT affect the exit code. `doctor` MUST NOT print any secret.

---

## 4. Capability profiles and the isolation contract

Isolis ships at least two profiles. A profile is a named set of resource limits,
users, capabilities, and filesystem policies. Profiles MUST be declared as data
in `isolis.py`, not scattered through the container command.

### 4.1 Common invariants for every profile

Every container MUST:

- Run on the dedicated bridge network with inter-container communication
  disabled, with a network alias equal to the agent name.
- Publish exactly one port, bound to `127.0.0.1` only.
- Set `no-new-privileges:true`.
- Start from `--cap-drop ALL`, then add back only what the profile lists.
- Mount no host path.
- Have no access to the Docker socket.
- Use tmpfs for `/tmp`, `/work`, and `/dev/shm`.
- Set a PID limit, a memory limit, and a CPU limit.
- Run one agent per container.

### 4.2 Strict profile

The strict profile is the default for untrusted work. It MUST:

- Use a read-only root filesystem.
- Run as an unprivileged user (`nobody`).
- Add no capabilities.
- Use small limits: memory 512m, cpus 1.0, pids 128, `/work` 512m, `/tmp` 64m,
  `/dev/shm` 64m.
- Disable package installation in the agent runtime.

### 4.3 Tooled profile

The tooled profile exists so the agent can install SDKs and build software
inside its own container. It MUST:

- Use a writable root filesystem inside the container. The container remains
  isolated because it still has no host mounts, no Docker socket, and no access
  to sibling containers.
- Run as root inside the container namespace (`--user 0:0`) so package managers
  can write system paths.
- Drop all capabilities, then add back exactly: `CHOWN`, `DAC_OVERRIDE`,
  `FOWNER`, `SETUID`, `SETGID`, `SETFCAP`. These are the minimum set that lets
  `apt-get`, `pip`, and `npm install -g` complete inside the container.
- Use larger limits: memory 4g, cpus 2.0, pids 512, `/work` 2g, `/tmp` 512m,
  `/dev/shm` 512m.
- Enable package installation in the agent runtime.

### 4.4 Profile selection and visibility

- The profile name MUST be validated against the declared profile table; an
  unknown name is an error that lists the valid names.
- The selected profile MUST be injected into the container as an environment
  variable and reported by the `/info` endpoint.
- `status` MUST show the profile of each agent.
- `profiles` MUST list every profile with its install flag, memory, cpu, pid,
  and user settings.

### 4.5 Why capabilities, not unrestricted root

Dropping to `--cap-drop ALL` first and then adding a minimal allowlist keeps the
container inside the isolation contract while remaining usable. The profile
MUST NOT use `--privileged`, MUST NOT add `SYS_ADMIN`, and MUST NOT mount the
Docker socket.

---

## 5. LLM injection, provider neutrality, and protocol negotiation

### 5.1 Injected configuration

The agent receives its model access through environment variables injected by
the host platform. The canonical names are:

```
MCAI_LLM_BASE_URL    provider base URL, often ending in /v1
MCAI_LLM_API_KEY     bearer credential
MCAI_LLM_MODEL       model identifier
```

The host MUST forward these names into the container and MUST NOT print the key
value. Only the key length and a short prefix may appear in diagnostics.

### 5.2 Provider neutrality

The implementation MUST NOT encode assumptions that tie it to one vendor:

- Do not hardcode a vendor host, a vendor model family, or a vendor SDK name in
  agent logic.
- Do not require a second, user-supplied API configuration.
- Do not read or write `.env` files containing secrets.
- Model selection MUST accept any identifier the injected gateway advertises.

Optional host-side overrides, resolved in this order, keep the system usable
with other models:

1. `ISOLIS_MODEL` (explicit override).
2. `MCAI_LLM_MODEL` (platform default).
3. The first model advertised by the gateway that shares the platform prefix.
4. `ISOLIS_DEFAULT_MODEL`.
5. A built-in fallback constant.

A blocklist of non-chat models MAY be applied; blocked identifiers MUST be
skipped during selection rather than causing a failure.

### 5.3 Missing configuration

If the base URL or the key is missing, `init`, `setup`, `chat`, and `rebuild`
MUST fail with one clear message stating that the LLM was not injected. They
MUST NOT fall back to a different provider or a locally invented key.

### 5.4 Protocol negotiation

The gateway may speak an OpenAI-style or an Anthropic-style HTTP API. The agent
runtime MUST detect which one works before building a client:

1. Probe the chat completion path with a minimal request.
2. Treat specific not-found or method-not-allowed responses as a signal to use
   the Anthropic-style client.
3. Otherwise use the OpenAI-style client.
4. Cache the decision for the process lifetime.

When the Anthropic-style client is selected, a trailing `/v1` MUST be removed
from the base URL, because that client appends its own version path. This rule
MUST be applied generically and MUST be covered by an acceptance check.

Both client paths MUST use the same timeout, retry count, and temperature so
behavior is stable across providers. The reference values are:

```
timeout      60 seconds
max_retries  1
temperature  0.4
max_tokens   4096 (Anthropic-style client)
```

The probe that negotiates the protocol MUST use a very small token budget (one
token) and a short timeout (six seconds) so a failed probe never stalls a
request.

The tokenizer library used by the framework MUST be given a writable cache
directory inside the container, for example `TIKTOKEN_CACHE_DIR=/tmp`. Without
it the client may attempt to download tokenizer data into a read-only location
or to the wrong user's home directory.

### 5.5 What the agent reports

`/info` MUST report the resolved model, the LLM host, the access route, whether
the LLM was injected, and the negotiated protocol. It MUST NOT report the key.

---

## 6. Agent source selection

The agent implementation may come from the local `agent/` directory or from any
Git repository the user names.

### 6.1 Resolution rules

- An empty source, `local`, or `builtin` selects the local `agent/` directory.
- Any other value is treated as a Git URL.
- The URL MUST be cloned shallowly into `.isolis/agent-src/<slug>/`, where the
  slug is derived safely from the repository path.
- On later runs, an existing clone MUST be updated with a fast-forward pull. If
  the pull cannot fast-forward, report the error rather than silently discarding
  local work.
- The resolved directory MUST contain a `Dockerfile`. A missing Dockerfile is an
  error naming the source.

### 6.2 Selection precedence

For `init`, `setup`, and `rebuild`, the source resolves as:

1. `--source` on the command line.
2. `ISOLIS_AGENT_SOURCE` in the environment.
3. `local`.

### 6.3 Image identity

The image tag MUST be derived from the profile name and the resolved source
path, so two profiles or two sources never share a stale image. Rebuilding one
MUST NOT invalidate the other. The tag format MUST be stable across runs for the
same inputs.

A workable format is:

```
isolis-agent:<profile>-<short-hash>
```

where `short-hash` is the first ten hex characters of a digest over the profile
name and the resolved source path. Two profiles therefore produce two tags, and
the local source and a cloned Git source produce two more. The tag is always
derivable from the recorded `profile` and `source`, so the state file stores
those two fields and not the tag itself.

### 6.4 Changing source for a running agent

When `init` is called for an existing agent whose recorded profile or source
differs from the requested one, the container MUST be recreated and the state
record updated. The user MUST see a line explaining that the profile or source
changed.

---

## 7. Terminal UI and command line

### 7.1 Commands

Interactive mode: `python3 isolis.py`.

One-shot mode: `python3 isolis.py <command> [args]`.

```
setup                       prepare Docker, mirror, image and network
doctor                      check environment and report problems
init [name]                 build and start an isolated agent
chat                        chat with the current agent
use <name>                  switch the current agent
status                      list agents and isolation summary
rebuild                     rebuild the image and current agent
stop [name]                 remove an agent container
profiles                    list capability profiles
help                        show help
quit / exit                 leave isolis (interactive only)
```

Inside the `isolis>` prompt the same commands apply. Inside the `you>` chat
prompt:

```
plain text    send to the agent
/back         leave chat, keep isolis
/quit         exit isolis
/help         show chat help
```

### 7.2 Options

`init`, `setup`, and `rebuild` MUST accept:

```
--profile strict|tooled     capability level (default tooled)
--source <local|git-url>    agent source
--model <name>              override the injected model
```

Unknown options MUST be rejected with a clear message. A missing option value
MUST be rejected with a clear message.

### 7.3 Names and defaults

The default agent name is `default`. Names are sanitized to `[A-Za-z0-9_-]`, run
of separators collapsed, trimmed, and truncated to 40 characters. When
sanitizing changes a name, print one notice line. The container name is
`isolis-<name>`.

### 7.4 Port allocation

Host ports are allocated from 18080 through 18119. A port is skipped when it is
recorded in state, published by any Docker container, or already listening
locally. Exhausting the range is a clear error.

---

## 8. HTTP API contract

The in-container server listens on port 8080 and speaks JSON over HTTP/1.1.
Every JSON response MUST include the common agent fields: agent name, hostname,
pid, and working directory.

```json
{
  "agent": "<agent name>",
  "hostname": "<container hostname>",
  "pid": 1,
  "cwd": "<process working directory>"
}
```

`hostname` and `pid` prove that the reply came from the container rather than
from the host. The host exposes the agent name through the container
environment; when it is unset the field is an empty string.

### 8.1 `GET /health`

```json
{"ok": true, "agent": "...", "hostname": "...", "pid": 1, "cwd": "/app"}
```

Used by the host readiness poll. The host polls until `ok` is `true` or the
timeout expires. The reference readiness window is 45 seconds, polled every
half second with a three second per-request timeout.

### 8.2 `GET /info`

```json
{
  "ok": true,
  "framework": "<framework name>",
  "source": "<agent source>",
  "model": "<model id>",
  "llm_host": "<host>",
  "via": "<route>",
  "injected": true,
  "protocol": "<protocol>",
  "profile": "<profile>",
  "install": true,
  "android_home": "<path>",
  "tools": ["..."],
  "workspace": "/work",
  "history_turns": 0,
  "history_tokens": 0,
  "context_tokens": 900000,
  "context_hard_limit": 1000000,
  "max_message_chars": 900000,
  "max_turns": 400,
  "compress_batch": 40,
  "compressed_turns": 0,
  "archived_turns": 0
}
```

The host MUST reject an agent whose `/info` does not confirm the expected access
route and injection before declaring it ready.

### 8.2.1 `GET /history`

Returns the live conversation and the archive for auditing:

```json
{
  "ok": true,
  "context_tokens": 900000,
  "history_tokens": 120,
  "compressed_turns": 0,
  "history": [{"role": "user", "content": "..."}],
  "archive": [{"role": "user", "content": "...", "reason": "compressed", "at": "<timestamp>"}]
}
```

`reason` is `compressed` for turns folded into a summary and `evicted` for turns
removed by the turn-count cap.

### 8.3 `POST /chat`

Request:

```json
{"message": "<user text>"}
```

Success:

```json
{
  "ok": true,
  "reply": "<assistant text>",
  "tools_used": [],
  "memory": {
    "trimmed_input": false,
    "trimmed_reply": false,
    "evicted": 0,
    "compressed_turns": 0,
    "history_turns": 1,
    "context_tokens": 900000,
    "history_tokens": 120
  }
}
```

Errors:

- Empty or missing message: 400 with a plain error.
- Malformed JSON: 400 with a plain error.
- Unknown path: 404.
- Runtime failure: 500 with an error string and a trace.

### 8.4 Memory, token budget, and automatic compression

Conversation memory is bounded on three independent axes. All three MUST be
implemented; they solve different failure modes.

**Axis 1: turn count.** The runtime keeps a system message plus at most 400
user/assistant pairs (`ISOLIS_MAX_TURNS`). Older pairs are removed from the front and recorded in an
in-memory archive with reason `evicted`.

**Axis 2: per-message size.** Any inbound user message and any outbound assistant
reply longer than `ISOLIS_MAX_MESSAGE_CHARS` (default 900000 characters) MUST be
truncated before it enters memory. Truncation keeps the head and the tail and
inserts an explicit marker stating how many characters were removed. This
prevents one pasted file from consuming the whole budget.

**Axis 3: token budget with automatic compression.** The runtime estimates the
token size of the live history plus the system prompt and compresses when the
estimate exceeds `ISOLIS_CONTEXT_TOKENS` (default 900000, `0` disables). The
budget MUST be clamped to `ISOLIS_CONTEXT_HARD_LIMIT` (default 1000000), the
measured upstream ceiling of the injected gateway. A configured budget above the
hard limit MUST be clamped with one notice line rather than failing a request.

Compression works as follows:

1. Fold the oldest batch of turns (`ISOLIS_COMPRESS_BATCH`, default 40 messages)
   into one summary by asking the same model for a compact briefing.
2. The summary prompt MUST preserve facts, file paths, commands, versions,
   decisions, errors, and unresolved questions, and MUST cap the result at a
   fixed character budget.
3. Replace the folded turns with one assistant entry prefixed by a stable
   marker, `[compressed history]`.
4. When an entry with that marker already exists, fold into it and overwrite it
   instead of inserting a second marker. At most one compression entry may exist
   in history at any time.
5. Repeat until the estimate is within budget or no batch remains eligible. At
   least the newest pair MUST stay uncompressed.
6. Records every folded turn in an archive with reason `compressed`.

If compression cannot reach the budget (for example a single oversized exchange),
the bounded turn count and message cap still hold, so memory cannot grow without
limit.

The archive keeps the most recent `ISOLIS_ARCHIVE_MAX` entries (default 4000)
and evicts the oldest. Archived
content is exposed for inspection and is never sent to the model.

The system prompt comes from `ISOLIS_SYSTEM_PROMPT` when set. Otherwise a
built-in prompt describes the sandbox, including the active profile and whether
package installation is available.

The built-in prompt SHOULD tell the model that it runs inside an isolated
container, cannot see the host filesystem, sibling containers, or the Docker
socket, and should match the user's language while staying concise and factual.

The system prompt MUST be overridable so different deployments can change agent
behavior without rebuilding the image.

### 8.5 Memory observability

`/info` MUST report `history_turns`, `history_tokens`, `context_tokens`,
`max_message_chars`, `compressed_turns`, and `archived_turns`.

`/chat` MUST include a `memory` object describing the effect of the request:
whether the input and reply were truncated, how many pairs were evicted, the
running compressed-turn count, the live turn count, the budget, and the current
history token estimate.

`GET /history` MUST return the live history and the archive together with the
same counters, so long-running sessions can be audited without restarting the
agent.

---

## 9. Agent tools and development capability

The runtime exposes a fixed tool set to the model. Tool names and purposes MUST
be listed by `/info`.

### 9.1 Baseline tools (every profile)

- `run_command`: run a non-interactive shell command with `/work` as the working
  directory, bounded by a timeout, returning stdout, stderr, and exit status.
- `write_file`: write text into a path inside `/work`, creating parent
  directories.
- `read_file`: read a text file inside `/work`.
- `list_files`: list a directory inside `/work`.
- `browse`: open a URL in headless Chromium, returning title and visible text,
  and optionally saving a screenshot into `/work`.

File tools MUST reject paths that resolve outside `/work`.

### 9.2 Development tools (tooled profile only)

- `install_packages(manager, packages)`: install packages with `apt`, `pip`, or
  `npm`. The manager is validated against the allowed set; the package string is
  whitespace-normalized; the command runs with a long timeout suitable for large
  downloads.
- `install_android_sdk(packages)`: run the bundled installer to place the
  Android command-line tools under `ANDROID_HOME` and install the requested
  packages. The default package set is `platform-tools`, `platforms;android-34`,
  and `build-tools;34.0.0`.

Both development tools MUST:

- Refuse to run when the strict profile disables installation.
- Report stdout, stderr, and exit status.
- Be bounded by a long but finite timeout.

### 9.2.1 Reference runtime constants

These values make the limits auditable. An implementation MAY choose different
numbers, but it MUST document them and keep every loop and subprocess bounded.

```
MAX_TURNS             400     conversation pairs retained per agent
MAX_TOOL_STEPS          8     model/tool round trips per chat request
SHELL_TIMEOUT         120     seconds per run_command call
INSTALL_TIMEOUT      1800     seconds per install call
MAX_OUTPUT          20000     characters returned by any single tool
CONTEXT_TOKENS     900000     token budget before automatic compression
CONTEXT_HARD_LIMIT 1000000    measured upstream token ceiling; budget is clamped to it
MAX_MESSAGE_CHARS  900000     per-message character cap
COMPRESS_BATCH         40     oldest messages folded per compression pass
SUMMARY_MAX_CHARS    8000     summary length cap
SUMMARY_INPUT_CHARS 60000     characters of raw turns fed to the summarizer
ARCHIVE_MAX          4000     retained archive entries
```

### 9.3 Image prerequisites for development

The image MUST provide the toolchain these tools rely on:

- A JDK suitable for Android builds.
- `curl`, `unzip`, `git`, and a build toolchain (`build-essential`, `cmake`,
  `pkg-config`).
- Node.js and npm.
- Python with pip.
- A writable, pre-created `ANDROID_HOME` directory.
- Environment variables for Java, Android, Gradle, npm, and pip caches.

The container `PATH` MUST include the Android platform tools and command-line
tools directories before the standard system paths, and it MUST be set at
container start rather than only at image build time. Setting it at startup
keeps the build cache intact when the path changes. Shell commands run by tools
MUST use a non-login shell (`/bin/sh -c`), because a login shell sources
`/etc/profile` and can reset `PATH`, dropping the Android directories.

The image MUST be based on a distribution release that actually offers the
required JDK version. Pin the base image to a specific release codename; do not
rely on a floating default tag whose package set may change. For example,
`python:3.12-slim` currently resolves to a release whose package set no longer
ships `openjdk-17`, so the tag must name the release explicitly.

### 9.4 Android installer contract

The installer MUST:

- Verify that Java, curl, and unzip are present before doing work.
- Skip the download when the SDK manager already exists.
- Accept a version override for the command-line tools archive.
- Accept a package list override.
- Accept licenses non-interactively.
- Be safe to run more than once.

---

## 10. Lifecycle command flows

### 10.1 `init`

1. Resolve the LLM configuration.
2. Ensure the daemon is running.
3. Sanitize the name and announce any change.
4. Resolve the profile and agent source, then print both.
5. Build or reuse the image for that profile and source.
6. Ensure the network exists.
7. If a container with the same name exists and its recorded profile and source
   match, reuse it when healthy; otherwise recreate it.
8. Start the container when none is running.
9. Poll `/health` with a bounded timeout.
10. Verify `/info` confirms injection and route.
11. Persist the agent record and set it current.
12. Print the ready line with port and image tag.

Any failure after container start MUST remove the container and report the last
container log line.

### 10.2 `chat`

1. Resolve the LLM configuration and ensure the daemon.
2. Read the current agent from state, falling back to the default name.
3. Start or recreate the agent when missing, stopped, or unhealthy, preserving
   its recorded profile and source.
4. Verify `/info`.
5. Print one header line with profile, hostname, model, host, and route, plus the
   tool list.
6. Loop: read a line, send it to `/chat`, print the reply.

### 10.3 `rebuild`

1. Resolve the LLM configuration and ensure the daemon.
2. Determine the current agent or the default name.
3. Take the profile and source from the existing record unless overridden on the
   command line.
4. Force a rebuild of that image.
5. Remove the container and its state record.
6. Re-run `init` for the same name, profile, and source.

### 10.4 `stop`

Remove the named or current container, delete its record, and move the current
pointer to any remaining agent. Removing a missing container MUST NOT fail the
command.

---

## 11. State file schema

`.isolis/state.json` is UTF-8 JSON:

```json
{
  "version": 1,
  "current": "<name or null>",
  "agents": {
    "<name>": {
      "name": "<name>",
      "container": "isolis-<name>",
      "port": 18080,
      "hostname": "<container hostname>",
      "model": "<model id>",
      "profile": "<profile>",
      "source": "<local or git url>",
      "created_at": "<UTC timestamp>"
    }
  }
}
```

Rules:

- Reading a missing file yields an empty, valid state.
- Unreadable or malformed JSON is an error, not a silent reset.
- Unknown or malformed fields are dropped rather than propagated.
- Writes are atomic.
- `version` stays `1`; `profile` and `source` are additive fields.

---

## 12. Output style rules

- ASCII only. No emoji.
- One clear line per fact or error. Failures state what failed and what to run
  next.
- Never print secrets. Key diagnostics show length and a short prefix only.
- Progress messages appear before long operations; the result appears after.
- The isolation summary is printed by `status` and names the active profile.

---

## 13. Acceptance criteria

A build is complete only when every item below is observable.

1. `doctor` reports readiness and exits zero with valid injection; it exits
   non-zero when the LLM variables are absent.
2. `profiles` lists both profiles with correct install flags and limits.
3. `init` with the strict profile starts a container that runs as `nobody`, has
   a read-only root, no added capabilities, and no install tools in `/info`.
4. `init` with the tooled profile starts a container that runs as root, has the
   six required capabilities and no others, reports `install: true`, and lists
   the development tools.
5. Attempting to install packages under the strict profile returns a clear
   refusal and does not modify the container.
6. Under the tooled profile, `install_packages("apt", "<package>")` succeeds and
   the installed binary is runnable through `run_command`.
7. Under the tooled profile, `install_android_sdk` completes, and `adb version`
   succeeds inside the container afterwards.
8. Re-running the Android installer does not redownload an existing SDK manager
   and does not fail.
9. `init --source <git-url>` clones the repository, builds an image tagged for
   that source, and the resulting agent reports that source in `/info`.
10. A second `init` from the same source updates the clone and reuses the image
    without rebuilding.
11. Changing the profile or source for an existing agent name recreates the
    container and updates the state record.
12. LLM protocol negotiation works against a provider that requires a stripped
    `/v1` suffix for the Anthropic-style client; chat completes end to end.
13. Model override precedence holds: `ISOLIS_MODEL` beats `MCAI_LLM_MODEL`, and a
    blocked identifier is skipped rather than fatal.
14. `/info` reflects the active profile, install flag, source, tools, and
    Android home.
15. Container invariants hold in all profiles: single loopback-published port,
    no host mounts, no Docker socket, tmpfs workspace, resource limits, and
    `no-new-privileges`.
16. `stop` removes a container and updates state; `status` reflects the removal.
17. State writes survive interruption without corrupting the file.
18. `rebuild` recreates the current agent with the same profile and source and a
    freshly built image.
19. Conversation memory stays bounded: after more than `ISOLIS_MAX_TURNS` pairs
    the agent still answers and `/info` reports a bounded `history_turns`.
20. A single chat request stops after at most eight tool round trips and returns
    a reply instead of looping forever.
21. A tool call that produces more than 20000 characters returns a truncated
    result with an explicit truncation marker.
22. With `ISOLIS_CONTEXT_TOKENS` set below the live history size, the next chat
    folds the oldest turns into one `[compressed history]` entry,
    `compressed_turns` increases, and the archive grows.
23. Repeated compression never produces a second `[compressed history]` entry;
    the existing summary is updated in place.
24. With `ISOLIS_CONTEXT_TOKENS=0`, compression is disabled, the archive only
    receives `evicted` entries, and the turn cap still holds.
25. A message longer than `ISOLIS_MAX_MESSAGE_CHARS` is stored truncated with a
    marker, and `/chat` reports `trimmed_input: true`.
26. `GET /history` returns both the live history and the archive with matching
    counters.
27. A budget above 1000000 is clamped with one notice line; `/info` reports the
    clamped `context_tokens` and the `context_hard_limit`.
28. A single message near the upstream ceiling (600000 characters, about 169000
    tokens) is accepted and answered without a 413 or a 5xx.
29. After the budget is exceeded at large input sizes, compression reduces the
    live history and a fact stated in the first message is still recoverable
    from the summary.

---

## 14. Known constraints

- The host uses only the Python standard library and the Docker CLI. Agent
  framework code stays inside the container.
- The only model upstream is the injected platform gateway. No second API, no
  vendor credential, no local secret file.
- The tooled profile trades a writable container filesystem for development
  capability. It still shares no host path, holds no Docker socket, and cannot
  reach sibling containers because inter-container communication is disabled.
- The image must pin a base release whose package set contains the required JDK.
- Large SDK and build caches live under tmpfs and do not survive container
  removal. Recreating an agent re-downloads them. This is accepted in exchange
  for never writing agent state to the host.

---

## v1.1 增补（2026-11-01，满血激活 + 文件交换）

本节对上方规范做增量修订；与上文同源，冲突时以本节为准。

1. **默认档位 tooled**：agent 在沙箱内自配环境是正常用法——`install_packages`（apt/pip/npm）与 `install_android_sdk`（`ANDROID_PACKAGES` 可覆盖，默认 `platform-tools platforms;android-34 build-tools;34.0.0`）随任务调用。strict 档仍禁装（工具不进 schema、调用即一行错误）。
2. **工具集**：tooled 7 个 = `run_command write_file read_file list_files browse install_packages install_android_sdk`；strict 5 个（无 install 两兄弟）。`browse` 用无头 Chromium 拿 title/url/正文，`screenshot=true` 落盘到 /work。
3. **镜像**：`python:3.12-slim-bookworm` + openjdk-17 + build-essential/cmake/pkg-config + git/curl/ripgrep/jq/unzip/zip/xz/tar/rsync/gnupg/tini/nodejs/npm + Playwright Chromium + Android SDK（构建期幂等预装，`ANDROID_HOME=/opt/android-sdk`）。`JAVA_HOME` 指向 `default-java` 链接，PATH 预注入 Android 目录。
4. **/files 文件交换**（不破坏隔离）：`GET /files` 返回 workspace 的 tar.gz（上限 512m）；`POST /files` 接收 tar.gz 解包进 workspace（上限 512m，拒绝绝对路径、`..`、符号链接、设备节点）。
5. **宿主新命令**：`export [name] [path]`、`import <file.tgz> [name]`、`batch <tasks-file> [--out DIR]`（每行 `名字|消息`，JSONL 落 `.isolis/batch/`）、`broadcast <message>`（所有运行中 agent）、`status --json`；chat 内 `/export /import /save`（转录落 `.isolis/transcripts/`）。
6. **/info 增补字段**：`tools[]`、`profile`、`install`、`workspace`、`android_home`、`uptime_s`、`started_at` 与记忆统计（`history_turns/context_tokens/compressed_turns/archived_turns/max_turns/compress_batch/max_message_chars`）。
7. **记忆**：400 轮上限 + 归档（`/history` 可见 live+archive）；超 900000 tokens 折叠最旧 40 轮为一个 `[compressed history]`，最新一轮永不折叠；tiktoken 缺失时按 chars/4 估算。

---

## v1.2 增补（2026-11-02，冲突裁定 + 反虚报 + 失败尝试缺陷修复）

本节对上方规范（含 v1.1）做增量修订；与上文及 `PROMPT.md` 冲突时，以本文件完整规范（含 v1.1/v1.2）为准。`PROMPT.md` 只是压缩重述，冲突时必须修正 `PROMPT.md`，不允许按冲突的那一版实现。

### A. 文件优先级（此前缺失，导致两次实现互相矛盾）

1. `ISOLIS_PROMPT.md`（含 v1.1 / v1.2 增补）——最高。
2. `PROMPT.md`（压缩驱动）——阅读用，冲突即废。
3. `README.md` / `QUICKSTART.md`/代码注释——最低，不得作为口径来源。

`PROMPT.md` 旧文中与规范冲突、本次作废的条目：ChatOpenAI 单一客户端（违反 5.4 协议协商）；默认模型名 `monkeycode-pro/gpt-5.6-terra`（违反 5.2 厂商中立，内置常量也不得含厂商/产品名）；"do not append a second /v1"与 5.4"Anthropic 剥尾 /v1"并列（正确口径见 5.4：OpenAI 风格由客户端自行拼接路径、不得二次追加；Anthropic 风格先剥 `/v1`）；`[compressed history] system message`（8.4 裁定为 assistant 角色条目）。

### B. 反虚报最终报告契约（上次失败的直接原因：声明与代码不符）

最终答复必须满足，否则该次执行判失败：

1. 附 `python3 -m pytest tests/ -q` 的**结尾原文**；测试数量、通过/失败数只以该输出为准。上次答复写 "45 passed"，实际 37 个用例、3 个失败。
2. 逐条列出验收状态，只许三态：`PASS <测试名>` / `FAIL <测试名>` / `NOT VERIFIED <原因>`。没有点名通过的用例，就不许写"已实现 / 已就位 / in place"。
3. 任何能力声明先 `grep` 代码再写。上次答复声称 Anthropic 客户端按 404/405 选择、`ISOLIS_SYSTEM_PROMPT`/`ISOLIS_COMPRESS_BATCH`/`ISOLIS_MODEL` 已转发进容器——代码里均不存在（`grep -i anthropic` 为空、这三个变量宿主侧零命中），属虚报。
4. "本机没有 Docker / 没有 MCAI_LLM_*"只能作为某条 `NOT VERIFIED` 的理由，且必须同时说明该分支由哪个带桩单测覆盖；不得以此整条跳过，也不得把"doctor 非零退出"当成交付完成的挡箭牌。验收 1 的"有注入→exit 0"分支必须有用例：monkeypatch 注入假 `MCAI_LLM_*` + 假 Docker 探测桩。

### C. 本次树即为一次失败尝试，已知缺陷清单（必须修复或重写）

- D1 `test_memory_trims_and_archives` 红：归档计数口径未定（按消息还是按对）。
- D2 `test_memory_compress_folds_oldest_pairs` 红：`COMPRESS_BATCH` 代码按"对"、规范按"消息"。
- D3 `test_tar_rejects_symlink` 红：`evil_tar_gz` 造的是名为 "link" 的 REGTYPE 普通文件，不是符号链接成员；助手必须能造 `SYMTYPE` + 外部 `linkname`（以及硬链接、设备、fifo）。
- D4 虚报（见 B.3）。
- D5 宿主转发 `ISOLIS_FALLBACK_MODEL`——任何版本规范都没有这个名字，统一改 `ISOLIS_DEFAULT_MODEL`。
- D6 `agent/requirements.txt` 只有 langchain-openai，无 Anthropic 能力客户端；5.4 的 Anthropic 路径因此不可能成立。

### D. 记忆计数口径（唯一定义，终结 D1/D2 歧义）

- "轮/turn"= 一对 user+assistant；`history_turns` = 存活对数；`ISOLIS_MAX_TURNS`（默认 400）上限指对数；`/info` 的 `max_turns` 同此口径。
- 归档**逐消息**一条记录：`{role, content, reason, at}`，`reason` ∈ {`evicted`, `compressed`}；`archived_turns` = 累计归档消息条数（只增不减，archive 保留最新 `ISOLIS_ARCHIVE_MAX` 条）。
- `ISOLIS_COMPRESS_BATCH`（默认 40）是**每次折叠的消息条数**，按整对折叠（奇数向下取齐到对）；`compressed_turns` = 累计被折叠的**消息条数**。
- 折叠产物 = assistant 角色一条消息，内容以 `[compressed history]` 开头，置于 system 之后；已存在 marker 条目时并入重写，全程至多一条；最新一对永不折叠；预估仍在预算内/无可折批次时停止。
- 测试断言按此口径书写；不得弱化已修正的断言去迁就实现。

### E. tar 解包安全（D3 修复要求，三版本运行时都要过）

1. 落盘前逐成员预检，任一命中即 `ValueError`：绝对路径、任一 `..` 段、`issym()`、`islnk()`、`ischr()`、`isblk()`、`isdev()`、`isfifo()`。
2. 之后用 `extractall(filter="data")`（运行时支持时）；不支持的环境走等价手写解包：只建普通文件与目录，最终 resolve 路径必须仍在 workspace 内。
3. 不得以"某 Python 版本没有 filter="为由跳过符号链接/硬链接校验——3.11/3.12/3.14 运行时行为不同，但预检与之一一对应。
4. `evil_tar_gz` 必须能按类型造成员（`SYMTYPE` + 外部 linkname、`LNKTYPE`、设备、fifo），而不是只改名字。

### F. 协议协商与 provider 中立（D4/D6 修复要求）

- 5.4 四步（probe 1 token / 6s；404/405 → Anthropic；否则 OpenAI；进程内缓存）必须实现；`/info.protocol` ∈ {`openai`, `anthropic`}。
- Anthropic 风格：先剥 base 尾部 `/v1`；timeout 60、max_retries 1、temperature 0.4、max_tokens 4096。OpenAI 风格：常量一致，路径由客户端拼接，不得二次追加 `/v1`。
- `agent/requirements.txt` 必须含 Anthropic 能力客户端，或 agent 用标准库实现两条 wire 通道之一均可，但 Anthropic 分支必须真实可跑。
- 验收 12 用本地 404 桩验证（不连公网）。

### G. 容器环境变量白名单（D4/D5 修复要求）

- 转发清单（缺一即缺陷）：`MCAI_LLM_BASE_URL` `MCAI_LLM_API_KEY` `MCAI_LLM_MODEL=<解析后的模型>` `ISOLIS_AGENT_NAME` `ISOLIS_PROFILE` `ISOLIS_INSTALL` `ISOLIS_MAX_TURNS` `ISOLIS_MAX_MESSAGE_CHARS` `ISOLIS_CONTEXT_TOKENS` `ISOLIS_CONTEXT_HARD_LIMIT` `ISOLIS_COMPRESS_BATCH` `ISOLIS_ARCHIVE_MAX` `ISOLIS_SYSTEM_PROMPT`（设置时才转发）`ISOLIS_DEFAULT_MODEL`（设置时才转发）。
- 模型解析顺序：`ISOLIS_MODEL` > `MCAI_LLM_MODEL` > 网 advertised 同前缀第一个 > `ISOLIS_DEFAULT_MODEL` > 内置常量；黑名单跳过不致命；`/info.model` 与下发的 `MCAI_LLM_MODEL` 一致。

---

## v1.3 增补（2026-11-02，无 Docker 降级 + 协议协商强制 + 记忆口径落地）

本次增补修掉 2026-11-02 失败尝试的直接死因：`init` 在无 Docker 的宿主上
抛 "docker CLI not found" 后整体坍缩成 NOT VERIFIED。与上方及 v1.1/v1.2
冲突时以本文件完整规范（含 v1.1/v1.2/v1.3）为准。

### H. 无 Docker 降级（进程模式，MUST）

1. `detect_mode()`：`shutil.which("docker")` 为空，或 CLI 在但
   `docker info` 不可达 ⇒ 返回 `process`；否则 `docker`。宿主侧只做探测，
   不因探测失败报错。
2. 进程模式下 `init/setup/chat/rebuild/stop/status` 全部照常工作：
   `agent/server.py` 以 `[sys.executable, agent/server.py]`  detached
   （`start_new_session=True`，stdout/stderr 进 `.isolis/logs/<name>.log`，
   stdin 关闭）跑在宿主上，绑定 `127.0.0.1:ISOLIS_PORT`（分配的宿主端口，
   18080-18119 首个空闲，跳过 state 记录/已监听），**不得**绑 `0.0.0.0`。
3. 进程模式的 workspace 落 `.isolis/workspaces/<name>`，HOME 与
   TIKTOKEN_CACHE_DIR 落 `.isolis/run/`（`ISOLIS_HOME`、`TIKTOKEN_CACHE_DIR`
   随环境白名单下发），保证 `/files`、`export`、`import`、`batch`、
   `broadcast`、转录全部可用。pid/port 元数据原子写 `.isolis/run/<name>.json`。
4. `stop` 对进程模式 kill 进程组（先 TERM 后 KILL，有界等待）并清元数据；
   `status`（含 `--json`）按每个 agent 记录的模式报 running/stopped/missing，
   并打印 `runtime: process mode (...)` 实况行，不得把容器隔离摘要说成
   进程模式的既成事实。
5. `doctor`：无 Docker CLI 输出 `[warn] docker CLI not found (process mode
   will be used)`，**不是 [fail]**；daemon 不可达同样是 `[warn]`；Docker Hub
   探测在进程模式输出 `[ok] registry: not needed (process mode builds no
   image)`。退出码只由 LLM 注入相关 `[fail]` 决定（验收 1）。有注入 ⇒
   exit 0；无 `MCAI_LLM_BASE_URL`/`MCAI_LLM_API_KEY` ⇒ exit 非 0。
6. `setup` 在进程模式打印一行 "docker CLI or daemon unavailable; setup
   continues in process mode" 后跳过镜像/网络/镜像源/daemon 步骤，其余
   （LLM 解析、profile/source 解析、image tag 打印）不变，且依旧不启动
   agent。
7. 状态文件新增 additive 字段 `mode`（`docker`|`process`），`version` 仍为
   1；`init` 重建/复用、`chat` 重附、`rebuild`、`stop` 都以记录里的 `mode`
   为准，缺省按 `detect_mode()`。
8. 进程模式是降级交付，报告里必须写明"isolation 行描述的是所配档位、非
   活动容器"，不得虚报容器级隔离。

### I. 协议协商与 wire 通道（D4/D6 修复，MUST，验收 12）

1. 5.4 四步在 agent 内实现：`probe {base}/chat/completions`（1 token、6s）；
   HTTP 404/405 ⇒ `anthropic`；其他可答 ⇒ `openai`；连接失败 ⇒ `unset`
   （探测期失败不致命）；进程内缓存；`/info.protocol` ∈
   {`openai`,`anthropic`,`unset`}（未注入时报 `unset`）。
2. chat 过程中再遇 404/405 ⇒ 翻转缓存协议、重建 client、裸重试一次
   （`flip_protocol`）。
3. Anthropic 风格先剥 base 尾部 `/v1`，再拼 `/v1/messages`；timeout 60、
   max_retries 1、temperature 0.4、max_tokens 4096。OpenAI 风格
   `chat/completions` 由客户端拼路径，不得二次追加 `/v1`。
4. `agent/requirements.txt` 必须含 `langchain-anthropic`（D6）。宿主或
   容器未装 LangChain/Anthropic 客户端时，agent 用标准库实现两条 wire
   之一**全部**真实可跑（验收 12 用本地 404/200 桩，不连公网）。
5. 内置默认模型常量不含厂商/产品名：`isolis-chat-default`。

### J. 环境白名单修正（D4/D5，MUST）

转发清单（缺一即缺陷）：`MCAI_LLM_BASE_URL` `MCAI_LLM_API_KEY`
`MCAI_LLM_MODEL=<解析后的模型>` `ISOLIS_AGENT_NAME` `ISOLIS_PROFILE`
`ISOLIS_INSTALL` `ISOLIS_MAX_TURNS` `ISOLIS_MAX_MESSAGE_CHARS`
`ISOLIS_CONTEXT_TOKENS` `ISOLIS_CONTEXT_HARD_LIMIT` `ISOLIS_COMPRESS_BATCH`
`ISOLIS_ARCHIVE_MAX` `ISOLIS_SOURCE`，加 `ISOLIS_SYSTEM_PROMPT`
`ISOLIS_DEFAULT_MODEL` `ISOLIS_MODEL`（各自设置时才转发）。
`ISOLIS_FALLBACK_MODEL` 在任何版本规范都不存在，禁止出现。容器 `-e` 与
进程模式共用同一份 `container_env()` 映射，两条路径同清单。

### K. 记忆口径落地（D1/D2，MUST）

1. "轮/turn"= user+assistant 一对；`history_turns`=存活对数（不含压缩
   marker）；`ISOLIS_MAX_TURNS` 上限指对数，超出**整对**从最旧移除并逐消息
   归档（reason `evicted`），marker 永不被淘汰。
2. `ISOLIS_COMPRESS_BATCH`=每次折叠的**消息条数**，按整对取齐；`compressed_turns`
   = 累计被折叠的**消息条数**；折叠消息逐条归档（reason `compressed`）。
3. 折叠产物 = 独立类型 `SummaryMessage`（assistant 角色、内容以
   `[compressed history]` 开头），置于 live history 最前（即 system 之后），
   全程至多一条；再次折叠**原位合并**旧摘要；新 summary 在 wire 上以
   assistant 消息发出。
4. `maybe_compress` 循环执行：预估（live+system+marker）仍超
   `ISOLIS_CONTEXT_TOKENS`（0=关）且还有可折对（最新一对永不折）时继续，
   直到回落到预算内或无可折对。
5. `snapshot()` 返回的 live 为 dict 列表 `{role: 类型名, content}`，archive
   同 `{role, content, reason, at}`；`/history` 直接下发该结构，`summary`
   字段为 marker 全文（兼容字段）。

### L. tar 解包安全补全（E，MUST）

落盘前预检命中即 `ValueError`：绝对路径、任一 `..` 段、`issym()`、
`islnk()`、`ischr()`、`isblk()`、`isdev()`、`isfifo()`。优先
`extractall/extract(filter="data")`；运行时不支持 `filter=`（TypeError）
走等价手写解包：只建目录与普通文件、最终 resolve 路径必须仍在 workspace
内。不得以 Python 版本为由跳过任一类型校验。

### M. 最终报告契约增补

沿用 B 节五条，另加：进程模式的每一条 PASS 必须指向在**本机真实进程**
上跑过的用例（`tests/test_process_mode.py` 的 init/doctor/协商桩），不得
以"没有 Docker"整条跳过；NOT VERIFIED 只允许留给需要真实 Docker daemon
或真实网关吞吐的条目，且必须点名覆盖该分支的桩用例。
