# Director-Cut MCP Integration

## Local MCP endpoint

`http://127.0.0.1:9420/mcp`

## Health

`http://127.0.0.1:9420/mcp/health` — returns `{"status":"ok"}` without authentication.

## Auth

Pass your Supabase access token as:

`Authorization: Bearer <supabase_jwt>`

For automation or CI, mint a desktop-signed token from the authenticated app:

`POST /api/settings/mcp/rotate-token` → `{ "token": "...", "expires_at": "..." }`

## Available tools

| Tool | Description |
|------|-------------|
| director.run.create | Start a new pipeline run |
| director.run.cancel | Cancel an active run |
| director.run.status | Get run status row |
| director.run.outputs | Get run outputs map from latest checkpoint |
| director.run.list | List runs |
| director.stage.run_single | Re-run a single stage (power tool) |
| director.stage.* | One tool per pipeline stage (passthrough) |
| director.project.create | Create project |
| director.project.list | List projects |
| director.output.latest_state | Full latest checkpoint JSON |
| director.service.llm_call | Direct LLM call |
| director.service.ffmpeg_probe | Probe media under `data/exports/` |
| director.service.asset_url | Resolve `/media/exports/...` URL |

## Connecting external MCP clients

Add to your MCP client config:

```json
{
  "mcpServers": {
    "director-cut": {
      "url": "http://127.0.0.1:9420/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN" }
    }
  }
}
```

## Connecting director-mcp (hosted package)

In Director's Cut → **MCP** → Remote MCP Connector:

Paste `https://your-director-mcp-deployment.com/mcp` and click **Connect**.

The WKWebView cannot always call external MCP URLs directly (CORS); use a desktop MCP client or the hosted package’s supported transport as documented in that repo.

The Streamable HTTP transport is mounted with `json_response=True` so single JSON-RPC payloads are returned directly (simpler for `curl` and the Tauri proxy). Full SSE streaming remains available in stateless mode if you adjust `app/mcp_server.py`.
