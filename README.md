# upbank-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes the
[Up Banking API](https://developer.up.com.au/) to LLM clients, built with
[FastMCP](https://gofastmcp.com) and packaged for Docker.

It provides **19 tools** and **2 resources** covering the complete public surface of
the Up API — accounts, transactions, categories, tags, attachments and webhooks —
with responses reshaped for token efficiency, cursor pagination preserved end to
end, and automatic retry on rate limits.

---

## Contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Running the server](#running-the-server)
- [Connecting an MCP client](#connecting-an-mcp-client)
- [Tool reference](#tool-reference)
- [Resources](#resources)
- [Response conventions](#response-conventions)
- [Error handling and rate limits](#error-handling-and-rate-limits)
- [Project layout](#project-layout)
- [Development](#development)
- [Security](#security)
- [Troubleshooting](#troubleshooting)

---

## Requirements

| | |
| --- | --- |
| **Up account** | A personal access token from <https://api.up.com.au/getting_started>. Tokens look like `up:yeah:…`. |
| **Docker** | Docker Engine 20.10+ with Compose v2 (`docker compose`, not `docker-compose`). |
| **Python** | 3.11+ — only if running outside Docker. |

The Up API is available to Up customers in Australia. A token grants access to the
issuing customer's own data only.

---

## Quick start

```sh
git clone git@github.com:uiux-me/upbank-mcp.git
cd upbank-mcp
cp .env.example .env          # paste your token into UP_API_TOKEN
docker compose up --build
```

The server listens on `http://127.0.0.1:8000/mcp`. Verify it:

```sh
docker compose exec upbank-mcp python -c "
import asyncio, upbank_mcp
from fastmcp import Client
async def main():
    async with Client(upbank_mcp.mcp) as c:
        print((await c.call_tool('ping')).data)
asyncio.run(main())"
```

A healthy response is your customer `id` and a status emoji:

```
{'ok': True, 'id': 'eb59f467-…', 'status_emoji': '⚡️'}
```

---

## Configuration

All configuration is by environment variable. Compose reads `.env` from the project
directory automatically.

| Variable | Default | Description |
| --- | --- | --- |
| `UP_API_TOKEN` | *(required)* | Personal access token. The only variable the server reads for credentials. Compose refuses to start without it; run directly, the server starts and fails on the first tool call. |
| `UP_API_BASE` | `https://api.up.com.au/api/v1` | API base URL. Override only for testing against a mock. |
| `MCP_TRANSPORT` | `stdio` | `stdio` for local MCP clients, `http` for a network-addressable server. Compose sets `http`. |
| `MCP_HOST` | `0.0.0.0` | Bind address for the HTTP transport, inside the container. |
| `MCP_PORT` | `8000` | Listen port for the HTTP transport, inside the container. |
| `MCP_HOST_PORT` | `8000` | **Compose only.** Host port published on `127.0.0.1`. Change this if 8000 is already in use. |

---

## Running the server

### HTTP, via Compose

Best for a long-running server shared by several clients on your machine.

```sh
docker compose up --build          # foreground
docker compose up -d --build       # detached
docker compose logs -f             # follow logs
docker compose down                # stop and remove
```

The port is published on `127.0.0.1` only. See [Security](#security).

### stdio, via Docker

Best for MCP clients that spawn the server as a subprocess. Build the image once:

```sh
docker build -t upbank-mcp:latest .
```

The image defaults to `MCP_TRANSPORT=stdio`, so no transport override is needed.

### Without Docker

```sh
pip install -e .
export UP_API_TOKEN=up:yeah:...
upbank-mcp
```

Set `MCP_TRANSPORT=http` to serve over HTTP instead of stdio.

---

## Connecting an MCP client

### Claude Code

```sh
claude mcp add upbank \
  -e UP_API_TOKEN=up:yeah:... \
  -- docker run -i --rm -e UP_API_TOKEN upbank-mcp:latest
```

### Claude Desktop, or any client using `mcpServers` config

```json
{
  "mcpServers": {
    "upbank": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "UP_API_TOKEN", "upbank-mcp:latest"],
      "env": { "UP_API_TOKEN": "up:yeah:..." }
    }
  }
}
```

`-i` is required — the server communicates over stdin/stdout. `--rm` cleans up the
container when the client disconnects.

### Over HTTP

Point the client at `http://127.0.0.1:8000/mcp` while the Compose stack is running.

---

## Tool reference

Required parameters are **bold**. Every list tool accepts `cursor`; see
[Pagination](#pagination).

### Utility

| Tool | Parameters | Returns |
| --- | --- | --- |
| `ping` | — | `{ok, id, status_emoji}`. Verifies the token and API reachability. |

### Accounts

| Tool | Parameters | Returns |
| --- | --- | --- |
| `list_accounts` | `account_type` (`SAVER` \| `TRANSACTIONAL` \| `HOME_LOAN`), `ownership_type` (`INDIVIDUAL` \| `JOINT`), `page_size` (1–100, default 30), `cursor` | Page of accounts with balances. |
| `get_account` | **`account_id`** | One account. |

### Transactions

| Tool | Parameters | Returns |
| --- | --- | --- |
| `list_transactions` | `account_id`, `status` (`HELD` \| `SETTLED`), `since`, `until`, `category`, `tag`, `page_size` (1–100, default 30), `cursor` | Page of transactions, newest first. Omit `account_id` to search across all accounts. |
| `get_transaction` | **`transaction_id`** | One transaction, including hold, round-up and cashback detail. |

`since` and `until` bound `createdAt` inclusively. `category` accepts a parent id,
which matches all of its children.

### Categories

| Tool | Parameters | Returns |
| --- | --- | --- |
| `list_categories` | `parent` | The category tree, or one parent's children. Not paginated. |
| `get_category` | **`category_id`** | One category with its parent and child ids. |
| `categorize_transaction` | **`transaction_id`**, `category_id` | Sets the category, or clears it when `category_id` is null. |

Categories are fixed by Up and cannot be created. Ids are slugs such as
`restaurants-and-cafes`. Only transactions with `is_categorizable: true` can be
changed, and only leaf categories are accepted — passing a parent such as
`good-life` returns HTTP 403.

### Tags

| Tool | Parameters | Returns |
| --- | --- | --- |
| `list_tags` | `page_size` (1–100, default 50), `cursor` | Page of tags. A tag's id is its label. |
| `add_tags_to_transaction` | **`transaction_id`**, **`tags`** (list) | Adds tags, creating any that do not exist. |
| `remove_tags_from_transaction` | **`transaction_id`**, **`tags`** (list) | Removes tags from the transaction. |

A transaction holds at most 6 tags. Tags with no remaining transactions disappear
from `list_tags`.

### Attachments

| Tool | Parameters | Returns |
| --- | --- | --- |
| `list_attachments` | `page_size` (1–100, default 30), `cursor` | Page of attachments. |
| `get_attachment` | **`attachment_id`** | One attachment. |

`file_url` is a signed URL that expires at `file_url_expires_at`. Fetch it promptly
or re-request the attachment.

### Webhooks

| Tool | Parameters | Returns |
| --- | --- | --- |
| `list_webhooks` | `page_size` (1–100, default 30), `cursor` | Page of webhooks. |
| `get_webhook` | **`webhook_id`** | One webhook. |
| `create_webhook` | **`url`**, `description` (≤64 chars) | The new webhook, including `secret_key`. |
| `delete_webhook` | **`webhook_id`** | `{ok, deleted}`. Permanent. |
| `ping_webhook` | **`webhook_id`** | Sends a test `PING` event. |
| `list_webhook_logs` | **`webhook_id`**, `page_size` (1–100, default 30), `cursor` | Recent delivery attempts with response codes and bodies. |

`secret_key` is returned **only** at creation and never again. Store it to verify
the `X-Up-Authenticity-Signature` header (SHA-256 HMAC) on incoming deliveries.

---

## Resources

| URI | Contents |
| --- | --- |
| `up://accounts` | Every account and its current balance, as a single JSON snapshot. |
| `up://categories` | The full category tree, for resolving valid `category` filter values. |

Both are read on demand and reflect state at read time.

---

## Response conventions

### Shape

Up returns [JSON:API](https://jsonapi.org), which nests every field under
`attributes`/`relationships` and repeats self-links on each resource. This server
flattens each resource into a compact dict and omits optional fields when absent,
which materially reduces token cost without losing information a caller needs.

```jsonc
{
  "id": "45b83097-c97d-40da-9790-254056f03d40",
  "status": "SETTLED",
  "description": "Google One",
  "amount": { "value": "-2.49", "currency": "AUD", "base_units": -249 },
  "created_at": "2026-08-20T06:53:17+10:00",
  "settled_at": "2026-08-20T06:53:17+10:00",
  "account_id": "90c0fffc-bed6-4214-9450-6a76cd39957b",
  "category_id": "games-and-software",
  "parent_category_id": "good-life",
  "tags": [],
  "is_categorizable": true
}
```

Fields such as `foreign_amount`, `hold_info`, `round_up`, `cashback`,
`card_purchase_method`, `note` and `message` appear only when the transaction has
them.

### Money

Every amount is an object:

```jsonc
{ "value": "-2.49", "currency": "AUD", "base_units": -249 }
```

`value` is a decimal string, `base_units` is the integer minor unit (cents for AUD).
**Debits are negative.** Prefer `base_units` for arithmetic to avoid float error.

### Pagination

List tools return:

```jsonc
{ "items": [ ... ], "next_cursor": "https://api.up.com.au/...", "prev_cursor": null }
```

To page, pass a returned cursor back as the `cursor` argument of the **same** tool.
Cursors are Up's own opaque URLs and already encode the filters and page size, so
all other arguments are ignored when `cursor` is set. A null cursor means no further
page in that direction.

Cursors are validated against the configured API host before being followed, so a
cursor cannot redirect the client to another server.

### Dates

`since` and `until` accept either `YYYY-MM-DD` or a full RFC-3339 timestamp. Bare
dates and naive datetimes are anchored to **Australia/Sydney**, matching how Up
presents times in the app; the correct offset is applied for the date in question,
so daylight saving is handled. Unparseable input is rejected before the request is
made, rather than surfacing as an opaque HTTP 400.

---

## Error handling and rate limits

- API errors are raised as `ToolError` with the HTTP status and Up's own error title
  and detail, for example:
  `HTTP 403 — Forbidden: Top-level categories cannot be set directly on transactions.`
- **429 and 5xx** responses are retried up to 3 times with exponential backoff,
  honouring the `Retry-After` header when present.
- Network failures are retried on the same schedule before surfacing.
- 4xx responses other than 429 are not retried — they indicate a bad request.

---

## Project layout

```
src/upbank_mcp/
├── client.py     Async HTTP client: auth, retry/backoff, date normalisation,
│                 cursor host validation
├── shapes.py     JSON:API → flat dict transforms, one per resource type
├── server.py     FastMCP instance, tool and resource definitions, entrypoint
├── __init__.py   Exports `mcp` and `main`
└── __main__.py   Enables `python -m upbank_mcp`
```

The separation is deliberate: `client.py` knows about HTTP and nothing about MCP,
`shapes.py` is pure data transformation, and `server.py` holds the tool contracts.
Each is independently testable.

---

## Development

```sh
pip install -e .
export UP_API_TOKEN=up:yeah:...
upbank-mcp                              # stdio
MCP_TRANSPORT=http upbank-mcp           # http on :8000
```

Drive the server in-process with the FastMCP client:

```python
import asyncio
from fastmcp import Client
import upbank_mcp

async def main():
    async with Client(upbank_mcp.mcp) as client:
        print(await client.list_tools())
        result = await client.call_tool("list_accounts", {"account_type": "TRANSACTIONAL"})
        print(result.data)

asyncio.run(main())
```

Rebuild the image after changes:

```sh
docker compose up -d --build
```

---

## Security

**The token is powerful.** Up personal access tokens cannot move money — the API has
no payment or transfer endpoint — but they can read your complete transaction
history and mutate categories, tags and webhooks. Treat one like a password.

- **The HTTP transport has no authentication of its own.** Anything that can reach
  the port can read your banking data. Compose therefore publishes to
  `127.0.0.1` only. Do not bind it to `0.0.0.0` or expose it through a tunnel or
  reverse proxy without putting authentication in front of it.
- **The token is never baked into an image.** `.env` is listed in `.dockerignore`,
  and the token is supplied at runtime. It does not appear in any image layer, so
  the image is safe to push to a registry.
- **`.env` is gitignored**, and `.env.example` carries a placeholder only.
- **The container runs as a non-root user** (uid 10001).
- **Rotate immediately** at <https://api.up.com.au/getting_started> if a token is
  ever exposed. Tokens do not expire on their own.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `No Up API token configured` | `UP_API_TOKEN` is unset or empty. Check `.env`, and that Compose is run from the project directory. |
| Compose exits with `set UP_API_TOKEN in .env` | Same cause, caught at container start rather than first call. |
| `Bind for 127.0.0.1:8000 failed: port is already allocated` | Another process holds port 8000. Set `MCP_HOST_PORT` in `.env`. |
| `HTTP 401 — Unauthorized` | Token is invalid or revoked. Reissue it. |
| `HTTP 403 — Top-level categories cannot be set…` | `categorize_transaction` was given a parent category. Use a leaf id from `list_categories`. |
| `HTTP 404` on a valid-looking id | Ids are per-customer. Confirm the id came from this token's own data. |
| Repeated `HTTP 429` | Sustained rate limiting. Reduce `page_size` and request frequency; retries are already automatic. |
| Client shows no tools | The client must run the container with `-i`. Without it, stdio is closed immediately. |

---

## Reference

- [Up API documentation](https://developer.up.com.au/)
- [Up API OpenAPI spec and Postman collection](https://github.com/up-banking/api)
- [FastMCP documentation](https://gofastmcp.com)
- [Model Context Protocol specification](https://modelcontextprotocol.io)
