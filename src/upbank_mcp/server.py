"""FastMCP server exposing the Up Banking API (https://developer.up.com.au/)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from . import shapes
from .client import MAX_PAGE_SIZE, UpClient, UpError, to_rfc3339

client = UpClient()


@asynccontextmanager
async def lifespan(_server: FastMCP):
    try:
        yield
    finally:
        await client.aclose()


mcp = FastMCP(
    name="upbank",
    instructions=(
        "Read and annotate the signed-in customer's Up bank data: accounts and "
        "balances, transactions, categories, tags, attachments and webhooks.\n\n"
        "All amounts are objects with `value` (a decimal string), `currency` and "
        "`base_units` (cents). Debits are negative.\n\n"
        "List tools return `{items, next_cursor, prev_cursor}`. To page, pass a "
        "returned cursor back as the `cursor` argument of the same tool; all other "
        "arguments are ignored when a cursor is given, since the cursor already "
        "encodes them.\n\n"
        "Category ids are slugs such as `restaurants-and-cafes`; tag ids are their "
        "human-readable labels. Use list_categories / list_tags to discover valid "
        "values before filtering or writing."
    ),
    lifespan=lifespan,
)


# --- shared argument types -------------------------------------------------

PageSize = Annotated[
    int,
    Field(
        default=30,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Number of records per page (1-{MAX_PAGE_SIZE}).",
    ),
]

Cursor = Annotated[
    str | None,
    Field(
        default=None,
        description=(
            "A `next_cursor` or `prev_cursor` from a previous call to this tool. "
            "When set, all other arguments are ignored."
        ),
    ),
]


async def _list(path: str, shaper, cursor: str | None, params: dict[str, Any]) -> dict[str, Any]:
    """Run a paginated GET, following `cursor` verbatim when one is supplied."""
    try:
        document = await client.request("GET", cursor or path, params=None if cursor else params)
    except UpError as exc:
        raise ToolError(str(exc)) from exc
    return shapes.page(document or {}, shaper)


async def _one(path: str, shaper) -> dict[str, Any]:
    try:
        document = await client.get(path)
    except UpError as exc:
        raise ToolError(str(exc)) from exc
    return shaper(document.get("data", {}))


# --- utility ---------------------------------------------------------------


@mcp.tool
async def ping() -> dict[str, Any]:
    """Check that the configured Up API token is valid and the API is reachable."""
    try:
        document = await client.get("/util/ping")
    except UpError as exc:
        raise ToolError(str(exc)) from exc
    meta = document.get("meta", {})
    return {"ok": True, "id": meta.get("id"), "status_emoji": meta.get("statusEmoji")}


# --- accounts --------------------------------------------------------------


@mcp.tool
async def list_accounts(
    account_type: Annotated[
        Literal["SAVER", "TRANSACTIONAL", "HOME_LOAN"] | None,
        Field(default=None, description="Return only accounts of this type."),
    ] = None,
    ownership_type: Annotated[
        Literal["INDIVIDUAL", "JOINT"] | None,
        Field(default=None, description="Return only individual or joint accounts."),
    ] = None,
    page_size: PageSize = 30,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """List the customer's Up accounts with their current balances."""
    return await _list(
        "/accounts",
        shapes.account,
        cursor,
        {
            "page[size]": page_size,
            "filter[accountType]": account_type,
            "filter[ownershipType]": ownership_type,
        },
    )


@mcp.tool
async def get_account(
    account_id: Annotated[str, Field(description="The account's unique id.")],
) -> dict[str, Any]:
    """Retrieve a single account by id."""
    return await _one(f"/accounts/{account_id}", shapes.account)


# --- transactions ----------------------------------------------------------


@mcp.tool
async def list_transactions(
    account_id: Annotated[
        str | None,
        Field(
            default=None,
            description="Restrict to one account. Omit to search across all accounts.",
        ),
    ] = None,
    status: Annotated[
        Literal["HELD", "SETTLED"] | None,
        Field(default=None, description="Only transactions with this status."),
    ] = None,
    since: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Inclusive lower bound on createdAt. Accepts YYYY-MM-DD or a full "
                "RFC-3339 timestamp; bare dates are read as Australia/Sydney time."
            ),
        ),
    ] = None,
    until: Annotated[
        str | None,
        Field(default=None, description="Inclusive upper bound on createdAt, same formats as since."),
    ] = None,
    category: Annotated[
        str | None,
        Field(
            default=None,
            description="Category id slug, e.g. `restaurants-and-cafes`. Parent ids match their children.",
        ),
    ] = None,
    tag: Annotated[
        str | None,
        Field(default=None, description="Tag id (its label), e.g. `Holiday`."),
    ] = None,
    page_size: PageSize = 30,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """List transactions, newest first, optionally filtered by account, status,
    date range, category or tag."""
    path = f"/accounts/{account_id}/transactions" if account_id else "/transactions"
    try:
        params = {
            "page[size]": page_size,
            "filter[status]": status,
            "filter[since]": to_rfc3339(since),
            "filter[until]": to_rfc3339(until),
            "filter[category]": category,
            "filter[tag]": tag,
        }
    except UpError as exc:
        raise ToolError(str(exc)) from exc
    return await _list(path, shapes.transaction, cursor, params)


@mcp.tool
async def get_transaction(
    transaction_id: Annotated[str, Field(description="The transaction's unique id.")],
) -> dict[str, Any]:
    """Retrieve a single transaction by id, including hold, round-up and cashback detail."""
    return await _one(f"/transactions/{transaction_id}", shapes.transaction)


# --- categories ------------------------------------------------------------


@mcp.tool
async def list_categories(
    parent: Annotated[
        str | None,
        Field(
            default=None,
            description="Return only children of this parent category id, e.g. `good-life`.",
        ),
    ] = None,
) -> dict[str, Any]:
    """List the category tree. Categories are fixed by Up, not user-defined."""
    try:
        document = await client.get("/categories", **{"filter[parent]": parent})
    except UpError as exc:
        raise ToolError(str(exc)) from exc
    return {"items": [shapes.category(r) for r in document.get("data", [])]}


@mcp.tool
async def get_category(
    category_id: Annotated[str, Field(description="The category id slug, e.g. `booze`.")],
) -> dict[str, Any]:
    """Retrieve a single category by id."""
    return await _one(f"/categories/{category_id}", shapes.category)


@mcp.tool
async def categorize_transaction(
    transaction_id: Annotated[str, Field(description="The transaction to categorise.")],
    category_id: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "A child category id such as `restaurants-and-cafes`. Pass null to "
                "clear the category. Parent categories are not accepted."
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    """Set or clear a transaction's category.

    Only transactions with `is_categorizable: true` can be changed.
    """
    body = {"data": {"type": "categories", "id": category_id} if category_id else None}
    try:
        await client.request(
            "PATCH", f"/transactions/{transaction_id}/relationships/category", json=body
        )
    except UpError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "transaction_id": transaction_id, "category_id": category_id}


# --- tags ------------------------------------------------------------------


@mcp.tool
async def list_tags(page_size: PageSize = 50, cursor: Cursor = None) -> dict[str, Any]:
    """List all tags the customer has created. A tag's id is its label."""
    return await _list("/tags", shapes.tag, cursor, {"page[size]": page_size})


@mcp.tool
async def add_tags_to_transaction(
    transaction_id: Annotated[str, Field(description="The transaction to tag.")],
    tags: Annotated[
        list[str],
        Field(
            description=(
                "Tag labels to add. Tags that do not exist yet are created. "
                "A transaction may hold at most 6 tags."
            ),
            min_length=1,
        ),
    ],
) -> dict[str, Any]:
    """Add one or more tags to a transaction."""
    body = {"data": [{"type": "tags", "id": t} for t in tags]}
    try:
        await client.request(
            "POST", f"/transactions/{transaction_id}/relationships/tags", json=body
        )
    except UpError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "transaction_id": transaction_id, "added": tags}


@mcp.tool
async def remove_tags_from_transaction(
    transaction_id: Annotated[str, Field(description="The transaction to untag.")],
    tags: Annotated[list[str], Field(description="Tag labels to remove.", min_length=1)],
) -> dict[str, Any]:
    """Remove one or more tags from a transaction."""
    body = {"data": [{"type": "tags", "id": t} for t in tags]}
    try:
        await client.request(
            "DELETE", f"/transactions/{transaction_id}/relationships/tags", json=body
        )
    except UpError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "transaction_id": transaction_id, "removed": tags}


# --- attachments -----------------------------------------------------------


@mcp.tool
async def list_attachments(page_size: PageSize = 30, cursor: Cursor = None) -> dict[str, Any]:
    """List transaction attachments. File URLs are signed and expire shortly."""
    return await _list("/attachments", shapes.attachment, cursor, {"page[size]": page_size})


@mcp.tool
async def get_attachment(
    attachment_id: Annotated[str, Field(description="The attachment's unique id.")],
) -> dict[str, Any]:
    """Retrieve a single attachment by id."""
    return await _one(f"/attachments/{attachment_id}", shapes.attachment)


# --- webhooks --------------------------------------------------------------


@mcp.tool
async def list_webhooks(page_size: PageSize = 30, cursor: Cursor = None) -> dict[str, Any]:
    """List configured webhooks."""
    return await _list("/webhooks", shapes.webhook, cursor, {"page[size]": page_size})


@mcp.tool
async def get_webhook(
    webhook_id: Annotated[str, Field(description="The webhook's unique id.")],
) -> dict[str, Any]:
    """Retrieve a single webhook by id."""
    return await _one(f"/webhooks/{webhook_id}", shapes.webhook)


@mcp.tool
async def create_webhook(
    url: Annotated[str, Field(description="Publicly reachable HTTPS URL to deliver events to.")],
    description: Annotated[
        str | None,
        Field(default=None, description="Optional label, max 64 characters.", max_length=64),
    ] = None,
) -> dict[str, Any]:
    """Register a webhook for transaction events.

    The response contains `secret_key`, which is returned only once — store it now
    to verify the X-Up-Authenticity-Signature header on incoming deliveries.
    """
    attributes: dict[str, Any] = {"url": url}
    if description:
        attributes["description"] = description
    try:
        document = await client.request("POST", "/webhooks", json={"data": {"attributes": attributes}})
    except UpError as exc:
        raise ToolError(str(exc)) from exc
    return shapes.webhook((document or {}).get("data", {}))


@mcp.tool
async def delete_webhook(
    webhook_id: Annotated[str, Field(description="The webhook to delete.")],
) -> dict[str, Any]:
    """Permanently delete a webhook. This cannot be undone."""
    try:
        await client.request("DELETE", f"/webhooks/{webhook_id}")
    except UpError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "deleted": webhook_id}


@mcp.tool
async def ping_webhook(
    webhook_id: Annotated[str, Field(description="The webhook to send a PING event to.")],
) -> dict[str, Any]:
    """Send a test PING event to a webhook to verify it is reachable."""
    try:
        document = await client.request("POST", f"/webhooks/{webhook_id}/ping")
    except UpError as exc:
        raise ToolError(str(exc)) from exc
    data = (document or {}).get("data", {})
    return {"ok": True, "event_type": data.get("attributes", {}).get("eventType")}


@mcp.tool
async def list_webhook_logs(
    webhook_id: Annotated[str, Field(description="The webhook whose deliveries to inspect.")],
    page_size: PageSize = 30,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """List recent delivery attempts for a webhook, to debug failing deliveries."""
    return await _list(
        f"/webhooks/{webhook_id}/logs", shapes.webhook_log, cursor, {"page[size]": page_size}
    )


# --- resources -------------------------------------------------------------


@mcp.resource("up://accounts", mime_type="application/json")
async def accounts_resource() -> dict[str, Any]:
    """A snapshot of every Up account and its current balance."""
    document = await client.get("/accounts", **{"page[size]": MAX_PAGE_SIZE})
    return {"accounts": [shapes.account(r) for r in document.get("data", [])]}


@mcp.resource("up://categories", mime_type="application/json")
async def categories_resource() -> dict[str, Any]:
    """The full Up category tree, for resolving category filter values."""
    document = await client.get("/categories")
    return {"categories": [shapes.category(r) for r in document.get("data", [])]}


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "stdio":
        mcp.run()
    else:
        mcp.run(
            transport=transport,
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )


if __name__ == "__main__":
    main()
