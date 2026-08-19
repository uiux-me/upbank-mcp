"""Flatteners that turn Up's JSON:API documents into compact dicts.

The wire format nests every field under `attributes`/`relationships` and repeats
self-links on each resource. Passing that through verbatim burns a lot of tokens
for no benefit, so each resource is reshaped into a flat dict that keeps the ids
and the fields a caller actually reasons about.
"""

from __future__ import annotations

from typing import Any


def money(obj: Any) -> dict[str, Any] | None:
    if not obj:
        return None
    return {
        "value": obj.get("value"),
        "currency": obj.get("currencyCode"),
        "base_units": obj.get("valueInBaseUnits"),
    }


def _related_id(relationships: dict[str, Any], name: str) -> str | None:
    data = (relationships.get(name) or {}).get("data")
    return data.get("id") if data else None


def _related_ids(relationships: dict[str, Any], name: str) -> list[str]:
    data = (relationships.get(name) or {}).get("data") or []
    return [item["id"] for item in data]


def account(resource: dict[str, Any]) -> dict[str, Any]:
    attrs = resource.get("attributes", {})
    return {
        "id": resource.get("id"),
        "name": attrs.get("displayName"),
        "account_type": attrs.get("accountType"),
        "ownership_type": attrs.get("ownershipType"),
        "balance": money(attrs.get("balance")),
        "created_at": attrs.get("createdAt"),
    }


def transaction(resource: dict[str, Any]) -> dict[str, Any]:
    attrs = resource.get("attributes", {})
    rels = resource.get("relationships", {})

    out: dict[str, Any] = {
        "id": resource.get("id"),
        "status": attrs.get("status"),
        "description": attrs.get("description"),
        "amount": money(attrs.get("amount")),
        "created_at": attrs.get("createdAt"),
        "settled_at": attrs.get("settledAt"),
        "account_id": _related_id(rels, "account"),
        "category_id": _related_id(rels, "category"),
        "parent_category_id": _related_id(rels, "parentCategory"),
        "tags": _related_ids(rels, "tags"),
        "is_categorizable": attrs.get("isCategorizable"),
    }

    # Optional detail — only included when present, to keep responses small.
    if attrs.get("message"):
        out["message"] = attrs["message"]
    if attrs.get("rawText"):
        out["raw_text"] = attrs["rawText"]
    if note := attrs.get("note"):
        out["note"] = note.get("text")
    if foreign := attrs.get("foreignAmount"):
        out["foreign_amount"] = money(foreign)
    if hold := attrs.get("holdInfo"):
        out["hold_info"] = {
            "amount": money(hold.get("amount")),
            "foreign_amount": money(hold.get("foreignAmount")),
        }
    if round_up := attrs.get("roundUp"):
        out["round_up"] = {
            "amount": money(round_up.get("amount")),
            "boost_portion": money(round_up.get("boostPortion")),
        }
    if cashback := attrs.get("cashback"):
        out["cashback"] = {
            "description": cashback.get("description"),
            "amount": money(cashback.get("amount")),
        }
    if method := attrs.get("cardPurchaseMethod"):
        out["card_purchase_method"] = {
            "method": method.get("method"),
            "card_number_suffix": method.get("cardNumberSuffix"),
        }
    if customer := attrs.get("performingCustomer"):
        out["performing_customer"] = customer.get("displayName")
    if attrs.get("transferAccount") is not None:
        out["transfer_account_id"] = _related_id(rels, "transferAccount")
    if attachment_id := _related_id(rels, "attachment"):
        out["attachment_id"] = attachment_id
    if attrs.get("deepLinkURL"):
        out["deep_link_url"] = attrs["deepLinkURL"]

    return out


def category(resource: dict[str, Any]) -> dict[str, Any]:
    attrs = resource.get("attributes", {})
    rels = resource.get("relationships", {})
    return {
        "id": resource.get("id"),
        "name": attrs.get("name"),
        "parent_id": _related_id(rels, "parent"),
        "child_ids": _related_ids(rels, "children"),
    }


def tag(resource: dict[str, Any]) -> dict[str, Any]:
    return {"id": resource.get("id")}


def attachment(resource: dict[str, Any]) -> dict[str, Any]:
    attrs = resource.get("attributes", {})
    rels = resource.get("relationships", {})
    return {
        "id": resource.get("id"),
        "created_at": attrs.get("createdAt"),
        "file_content_type": attrs.get("fileContentType"),
        "file_extension": attrs.get("fileExtension"),
        # Signed and short-lived; Up documents these as expiring URLs.
        "file_url": (attrs.get("fileURL") or {}).get("value")
        if isinstance(attrs.get("fileURL"), dict)
        else attrs.get("fileURL"),
        "file_url_expires_at": attrs.get("fileURLExpiresAt"),
        "transaction_id": _related_id(rels, "transaction"),
    }


def webhook(resource: dict[str, Any]) -> dict[str, Any]:
    attrs = resource.get("attributes", {})
    return {
        "id": resource.get("id"),
        "url": attrs.get("url"),
        "description": attrs.get("description"),
        "created_at": attrs.get("createdAt"),
        # Only returned on creation, and never again.
        "secret_key": attrs.get("secretKey"),
    }


def webhook_log(resource: dict[str, Any]) -> dict[str, Any]:
    attrs = resource.get("attributes", {})
    response = attrs.get("response") or {}
    return {
        "id": resource.get("id"),
        "delivery_status": attrs.get("deliveryStatus"),
        "created_at": attrs.get("createdAt"),
        "response_status_code": response.get("statusCode"),
        "response_body": response.get("body"),
    }


def page(document: dict[str, Any], shaper) -> dict[str, Any]:
    """Shape a list document into `{items, next_cursor, prev_cursor}`.

    Cursors are the opaque pagination URLs Up returns; pass one back as the
    `cursor` argument of the same tool to fetch that page.
    """
    links = document.get("links") or {}
    return {
        "items": [shaper(r) for r in document.get("data", [])],
        "next_cursor": links.get("next"),
        "prev_cursor": links.get("prev"),
    }
