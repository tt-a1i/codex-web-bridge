# ChatGPT Pro MCP Setup

Use this runbook when a first-time user wants ChatGPT Pro to connect to a local
workspace through the `connector/` MCP server. It is especially useful when the
local agent cannot operate a browser: the agent can still prepare the local MCP
server and give the user a short ChatGPT web checklist.

Last manual smoke test in this repo: 2026-06-19. ChatGPT labels move over time;
if the UI text differs, follow the current OpenAI docs for Developer mode and
connecting from ChatGPT.

Official references:

- https://developers.openai.com/apps-sdk/deploy/connect-chatgpt
- https://developers.openai.com/apps-sdk/quickstart
- https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt

## Mental Model

- The local agent starts a readonly MCP server.
- ChatGPT Pro is the MCP host. It connects to the local server through an HTTPS
  endpoint and calls tools such as `open_workspace`, `read`, `search`, `list`,
  `git_status`, and `git_diff`.
- The local agent does not need browser-control capability after it has started
  the local server and tunnel. A human can do the one-time ChatGPT web setup.
- Installing only `skills/codex-web-bridge` does not install the root-level
  `connector/` package. The user needs a checkout or package that includes
  `connector/`.

## Preconditions

- The user has a ChatGPT account/workspace where Developer mode and custom MCP
  apps are available.
- The local checkout includes both `skills/codex-web-bridge/` and `connector/`.
- `allowed_roots` points at a specific project directory, not `~`, `/`, or a
  broad profile directory.
- The connector stays `readonly` unless the user explicitly opts into a higher
  trust level. Write, edit, shell, and worktree tools are not implemented in the
  first connector.
- ChatGPT needs an HTTPS MCP endpoint. Prefer OpenAI Secure MCP Tunnel when
  available. For a temporary smoke test, ngrok or Cloudflare Tunnel can expose
  the loopback server, but the tunnel must be short-lived.

## Agent-Side Setup

From the repo root:

```bash
python3 -m unittest discover -s connector/tests -t .
cp connector/connector.example.json connector/connector.local.json
```

Edit `connector/connector.local.json`:

```json
{
  "allowed_roots": ["/absolute/path/to/the/project"],
  "trust_level": "readonly",
  "host": "127.0.0.1",
  "port": 8765,
  "owner_token": null
}
```

For a self-managed MCP client, set a strong `owner_token` and send it as
`Authorization: Bearer <token>`. For a first-time ChatGPT no-auth smoke test,
`owner_token: null` is the simplest path because ChatGPT's "No authentication"
connector setup does not send the local Bearer token. Treat that as temporary:
keep `readonly`, allow only one specific repo, and stop the tunnel immediately
after testing.

Start the local server:

```bash
python3 -m connector.server --config connector/connector.local.json
```

Expose it through an HTTPS tunnel. Example with Cloudflare Tunnel:

```bash
cloudflared tunnel --url http://127.0.0.1:8765
```

The ChatGPT endpoint is the tunnel URL plus `/mcp`, for example:

```text
https://example.trycloudflare.com/mcp
```

Optional local sanity check:

```bash
curl -sS \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' \
  http://127.0.0.1:8765/mcp
```

Expected result:

```json
{"jsonrpc":"2.0","id":1,"result":{}}
```

## ChatGPT Web Setup

The user does this part in ChatGPT web:

1. Open ChatGPT settings.
2. Enable Developer mode under `Apps & Connectors` / `Apps` /
   `Connectors` -> `Advanced settings`, depending on the current UI label.
3. Go to `Connectors` or `Apps & Connectors` and choose `Create`.
4. Name the connector, for example `Codex Pro Workspace`.
5. Use the HTTPS endpoint ending in `/mcp`.
6. For a temporary smoke test, choose `No authentication`. For persistent use,
   use an auth-capable deployment or the official secure tunnel path.
7. Save or connect the app.
8. Open a new chat and enable/select the connector from the tools/apps picker.

## Golden Prompt

Use a small prompt that proves the host can call the local tools without asking
for broad access:

```text
Use only the Codex Pro Workspace connector. Do not use web browsing or memory.
First call open_workspace with path /absolute/path/to/the/project, then call
read for README.md. Reply with only the first heading line from README.md and
the names of the MCP tools you used.
```

Expected behavior:

- ChatGPT asks to use the connector tools or calls them directly, depending on
  current product settings.
- The connector receives `open_workspace` and `read`.
- The answer includes the README heading and the tool names.

## What To Tell A First-Time User

When the local agent cannot operate a browser, report only the steps the user
must do manually:

```text
I started the readonly MCP server locally and exposed it through this temporary
HTTPS endpoint:

https://<tunnel-host>/mcp

In ChatGPT web:
1. Open Settings -> Apps & Connectors -> Advanced settings and enable Developer mode.
2. Go to Connectors -> Create.
3. Name it "Codex Pro Workspace".
4. Paste the endpoint above.
5. Choose No authentication only for this temporary smoke test.
6. Open a new chat, select the connector, and send the golden prompt I provide.

Tell me when ChatGPT shows the connector tools or if it reports an error.
```

## Shutdown

After the test:

- Stop the HTTPS tunnel.
- Stop `python3 -m connector.server`.
- Remove or disable any no-auth ChatGPT connector created only for testing.
- Do not leave a no-auth public tunnel to local source code running.

## Troubleshooting

- ChatGPT cannot connect: verify the endpoint is HTTPS and ends in `/mcp`, not
  `/rpc`.
- ChatGPT lists no tools: restart the connector and reconnect the app.
- ChatGPT labels readonly tools as write-capable: ensure the server exposes tool
  `annotations.readOnlyHint` and restart the connector.
- The local server rejects the workspace: set `allowed_roots` to the exact repo
  path or a narrow parent directory.
- A no-browser agent is blocked on ChatGPT UI: the agent should keep the local
  server/tunnel running and ask the user to complete only the web setup steps.
- The user installed only the skill: install or clone the project distribution
  that includes the root-level `connector/` package.
