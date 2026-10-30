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
  "history_turns": 0
}
```

The host MUST reject an agent whose `/info` does not confirm the expected access
route and injection before declaring it ready.

### 8.3 `POST /chat`

Request:

```json
{"message": "<user text>"}
```

Success:

```json
{"ok": true, "reply": "<assistant text>", "tools_used": []}
```

Errors:

- Empty or missing message: 400 with a plain error.
- Malformed JSON: 400 with a plain error.
- Unknown path: 404.
- Runtime failure: 500 with an error string and a trace.

### 8.4 Memory and system prompt

The runtime keeps a system message plus the most recent bounded number of
user and assistant turns. The reference bound is the last 24 pairs; older turns
are discarded from the front. The system prompt comes from the environment when
set and otherwise falls back to a built-in prompt describing the sandbox,
including the active profile and whether package installation is available.

The built-in prompt SHOULD tell the model that it runs inside an isolated
container, cannot see the host filesystem, sibling containers, or the Docker
socket, and should match the user's language while staying concise and factual.

The system prompt MUST be overridable so different deployments can change agent
behavior without rebuilding the image. The override variable is
`ISOLIS_SYSTEM_PROMPT`; when it is empty the built-in prompt is used.

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
MAX_TURNS         24    conversation turns retained per agent
MAX_TOOL_STEPS     8    model/tool round trips per chat request
SHELL_TIMEOUT    120    seconds per run_command call
INSTALL_TIMEOUT 1800    seconds per install call
MAX_OUTPUT     20000    characters returned by any single tool
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
19. Conversation memory stays bounded: after more than 24 user/assistant pairs
    the agent still answers and `/info` reports a bounded `history_turns`.
20. A single chat request stops after at most eight tool round trips and returns
    a reply instead of looping forever.
21. A tool call that produces more than 20000 characters returns a truncated
    result with an explicit truncation marker.

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
