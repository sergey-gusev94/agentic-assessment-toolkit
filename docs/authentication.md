# Authentication

Live solve and grading jobs need a credential for the agent named by the
experiment config. AAT resolves that credential before planning the run,
prefers subscription-backed access, and prevents unrelated shell variables
from silently changing the provider, account type, model, or agent behavior.

Check the resolution before a long run:

```bash
aat check-auth --config codex-grader-sol-high
aat check-auth --config claude-grader-sonnet5-high
```

The command prints the authentication method and selection source. It never
prints a credential or the path of a credential file. It does not touch the
data root or launch Harbor.

`--dry-run` and `--materialize-only` do not resolve authentication. A Harbor
command launched manually after materialization bypasses AAT's credential
injection, so the required environment variable must be set in that shell.

## Codex

Authenticate the host Codex CLI once:

```bash
codex login
```

AAT normally finds `${CODEX_HOME:-~/.codex}/auth.json`, validates it, and asks
Harbor to copy it into each Codex container. The cached file may represent a
ChatGPT subscription login or an API-key login saved by Codex.

AAT selects Codex authentication in this order:

1. `CODEX_AUTH_JSON_PATH` selects a specific auth file.
2. `CODEX_FORCE_AUTH_JSON=true` selects `~/.codex/auth.json`.
3. `CODEX_FORCE_AUTH_JSON=false` selects `OPENAI_API_KEY`.
4. Without an explicit selection, AAT uses the cached file under `CODEX_HOME`
   or `~/.codex` when it exists.
5. If no cached file exists, AAT uses a nonempty `OPENAI_API_KEY`.

The cached file wins over an API key that happens to be present in the shell.
This keeps an unrelated key from changing a subscription-backed run to
usage-based billing.

Select another auth file for one command:

```bash
CODEX_AUTH_JSON_PATH=/protected/path/auth.json \
  aat grade --all --config codex-grader-terra-high
```

Select usage-based API authentication even when a cached login exists:

```bash
CODEX_FORCE_AUTH_JSON=0 OPENAI_API_KEY=... \
  aat grade --all --config codex-grader-terra-high
```

An absent, unreadable, empty, or invalid selected credential fails before AAT
creates a job or task directory. The check is local. A provider can still
reject a revoked login, an expired key that cannot be refreshed, or an account
without access.

If `codex login status` succeeds but no `auth.json` exists, Codex may be using
the operating-system keyring. Set the following in the Codex `config.toml`,
then run `codex login` again:

```toml
cli_auth_credentials_store = "file"
```

Treat `auth.json` like a password. Keep it outside this repository and the data
root. AAT records only the method and selection source, never the credential or
its path.

`aat intake` invokes the host Codex CLI directly, so it uses the host CLI's own
cached login rather than Harbor injection.

## Claude Code

Subscription-backed Claude Code jobs use the token produced by `claude
setup-token`. This requires a Claude plan that includes Claude Code. Without
such a plan, use `ANTHROPIC_API_KEY` and accept usage-based billing.

Run the setup command interactively:

```bash
claude setup-token
```

The command draws an interface on standard output and prints the token after
sign-in. Do not redirect its output into a file because the file would contain
the interface as well as the token.

Store the printed token in the default file without putting it in shell
history:

```bash
install -m 600 /dev/null ~/.claude/aat-oauth-token
cat > ~/.claude/aat-oauth-token
```

Paste the token, press Enter, then press Ctrl-D. The file must contain the token
and nothing else. Keep it outside this repository and the data root.

The token is long-lived but static. AAT reads it and never refreshes it. Note
the expiry date printed by `claude setup-token`, then replace the file with a
new token when it expires. Logging the interactive Claude CLI out and back in
does not renew this token.

AAT deliberately does not read `~/.claude/.credentials.json`. Its access token
is short-lived and only the host Claude CLI refreshes it, so it is unsuitable
for an unattended Harbor run.

AAT selects Claude authentication in this order:

1. `CLAUDE_FORCE_OAUTH=true` explicitly selects a subscription token from the
   next available token source.
2. `CLAUDE_FORCE_OAUTH=false` explicitly selects `ANTHROPIC_API_KEY`.
3. `AAT_CLAUDE_TOKEN_FILE`, when set, names the token file.
4. Otherwise, a nonempty `CLAUDE_CODE_OAUTH_TOKEN` is used.
5. Otherwise, `~/.claude/aat-oauth-token` is used when it exists.
6. If none of those exists, `ANTHROPIC_API_KEY` is used.

An explicitly named token file that is missing, unreadable, empty, or contains
more than the token is an error. AAT does not fall through to another billing
route after an explicit selection fails.

Use another token file for one command:

```bash
AAT_CLAUDE_TOKEN_FILE=/protected/path/aat-oauth-token.other \
  aat grade --all --config claude-grader-sonnet5-high
```

To select usage-based API authentication deliberately:

```bash
CLAUDE_FORCE_OAUTH=0 ANTHROPIC_API_KEY=... \
  aat grade --all --config claude-grader-opus5-high
```

The subscription token wins over an API key that happens to be in the shell.
For a subscription-backed run, AAT removes the competing API-key variables and
sets Harbor's OAuth selection explicitly. For an API-key run, it removes the
subscription token instead.

AAT also removes Claude provider and behavior variables that are not part of
config identity, including provider URL, Bedrock selection, model fallback,
turn limits, effort fallback, and thinking-token limits. Settings intended to
change an experiment belong in a config's `agent_args`, where they are recorded
and hashed.

`ANTHROPIC_AUTH_TOKEN` is not an AAT credential source. AAT supports the Claude
subscription token carried by `CLAUDE_CODE_OAUTH_TOKEN` and the ordinary
`ANTHROPIC_API_KEY` path.

## Recorded information

For a live Codex or Claude Code job, `aat-run.json` records only:

- The method, such as `codex-auth-json`, `openai-api-key`,
  `claude-oauth-token`, or `anthropic-api-key`.
- The nonsecret selection source, such as `automatic-cache`,
  `automatic-token-file`, or the name of the selecting environment variable.

Credential values and credential-file paths are never recorded.
