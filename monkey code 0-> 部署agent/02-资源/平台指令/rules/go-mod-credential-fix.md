# Go Mod Credential Fix Rule

When running `go mod` commands (such as `go mod tidy`, `go mod download`, `go mod vendor`, etc.) and encountering HTTP 40x authentication/permission errors, automatically attempt to resolve the issue by extracting credentials from Git's credential helper and writing them to `/root/.netrc`.

## Trigger Conditions

When `go mod` commands fail with errors indicating authentication or permission issues, such as:

- `401 Unauthorized`
- `403 Forbidden`
- `404 Not Found` (for private repositories that return 404 to unauthenticated requests)
- Messages containing `unexpected status` with 40x HTTP codes

## Execution Steps

### 1. Identify the Credential Helper

Read the Git credential helper configuration:

```bash
git config --get credential.helper
```

If a specific host is configured, check for host-specific helpers:

```bash
git config --get-all credential.helper
```

### 2. Extract the Target Host

From the error message, identify the host that requires authentication (e.g., `gitlab.example.com`, `github.com`).

### 3. Invoke the Credential Helper

Use the credential helper to retrieve credentials. The command to execute depends on the value returned by `git config --get credential.helper`:

- If the value is a **built-in short name** (e.g., `store`, `cache`, `osxkeychain`), the actual command is `git credential-<NAME> get`
- If the value is an **absolute path** to an executable (e.g., `/usr/lib/git-core/git-credential-manager`), execute that path directly with `get` as the argument
- If the value contains **arguments** (e.g., `/path/to/helper --arg`), treat the entire value as the command followed by `get`

In the current environment, the most possible credential helper is: `/app/agent/bin/agent git-credential-helper`

Pass `protocol` and `host` via stdin:

```bash
# Read the helper config
HELPER=$(git config --get credential.helper)

# Execute the helper's get command, passing protocol and host via stdin
echo -e "protocol=https\nhost=<TARGET_HOST>\n" |  get
```

Alternatively, use Git's built-in credential interface which automatically dispatches to the configured helper:

```bash
echo -e "protocol=https\nhost=<TARGET_HOST>\n" | git credential fill
```

The output will contain fields like:

```
protocol=https
host=<TARGET_HOST>
username=<USERNAME>
password=<TOKEN_OR_PASSWORD>
```

### 4. Write Credentials to /root/.netrc

Parse the `username` and `password` from the credential helper output, then write or append to `/root/.netrc`:

```bash
echo "machine <TARGET_HOST> login <USERNAME> password <TOKEN_OR_PASSWORD>" >> /root/.netrc
chmod 600 /root/.netrc
```

If `/root/.netrc` already contains an entry for the same host, update the existing entry instead of appending a duplicate.

### 5. Retry the Go Mod Command

After writing the credentials, retry the original `go mod` command.

## Complete Example

Suppose `go mod tidy` fails with:

```
go: module git.example.com/org/repo: reading https://git.example.com/org/repo?go-get=1: 401 Unauthorized
```

or

```
go: module git.example.com/org/repo: git ls-remote -q --end-of-options origin in /root/go/pkg/mod/cache/vcs/......: exit status 128:
        remote: The project you were looking for could not be found or you don't have permission to view it.
        fatal: repository 'https://git.example.com/org/repo' not found
```

Execute the following:

```bash
# Step 1: Check credential helper
git config --get credential.helper
# Possible: /app/agent/bin/agent git-credential-helper
# Or: store

# Step 2: Retrieve credentials using the configured helper
echo -e "protocol=https\nhost=git.example.com\n" | git credential fill
# Output:
#   protocol=https
#   host=git.example.com
#   username=oauth2
#   password=glpat-xxxxxxxxxxxx

# Step 3: Write to .netrc
echo "machine git.example.com login oauth2 password glpat-xxxxxxxxxxxx" >> /root/.netrc
chmod 600 /root/.netrc

# Step 4: Retry
go mod tidy
```

## Notes

- Always set `/root/.netrc` file permissions to `600` to protect credentials
- Before writing to `.netrc`, check if an entry for the same host already exists to avoid duplicates
- If the credential helper returns empty or fails, inform the user that credentials are not available and ask for manual intervention
- This rule only applies when `go mod` encounters 40x errors; do not preemptively write `.netrc` entries
- If `GONOSUMCHECK` or `GONOSUMDB` environment variables need to be set for private modules, configure them as well based on the host
- **NEVER EXPOSE CREDENTIALS IN MODEL RESPONSE**: Sensitive information such as `username` and `password` obtained from the credential helper must only be used for writing to `/root/.netrc`. Never display credentials in chat responses, log output, or any user-facing text
