"""
Formats Monday.com API results into clean Telegram messages.
Applies limit, sort_by, and filters from the agent's entities.
"""

import logging
import re
from typing import Any

from core.output_schema import ARIAResponse, Entities, Filter
from monday.schema_loader import get_column_map

logger = logging.getLogger(__name__)

MAX_DISPLAY_ITEMS = 15
FIELD_LABELS = {
    "status":          "Status",
    "phone":           "Phone",
    "email":           "Email",
    "source":          "Source",
    "assigned_ae":     "Assigned AE",
    "art_form":        "Art Form",
    "availability":    "Availability",
    "contract_status": "Contract",
    "rating":          "Rating",
    "pricing":         "Pricing (AED)",
    "experience":      "Experience (yrs)",
    "role":            "Role",
    "access_level":    "Access Level",
    "message":         "Notes",
    "follow_up_date":  "Follow Up",
    "last_action":     "Last Action",
}


def format_results(
    raw_results: list[dict],
    response: ARIAResponse,
) -> str:
    """
    Main entry point. Extracts items from Monday.com results,
    applies local filtering/sorting/limiting, and formats for Telegram.
    """
    api_errors = _extract_api_errors(raw_results)
    items = _extract_items(raw_results)

    if not items:
        if api_errors:
            return _format_api_error(api_errors[0])
        return _empty_result_message(response)

    entities = response.entities
    board = entities.board or ""
    items = _apply_filters(items, entities.filters, entities.person_name, board)
    items = _apply_sort(items, entities.sort_by, board)
    items = _apply_limit(items, entities.limit)

    if response.intent == "count":
        return f"*{len(items)}* {_board_label(entities.board)} found."

    if not items:
        return _empty_result_message(response)

    return _format_item_list(items, response)


def _extract_items(results: list[dict]) -> list[dict]:
    """Walk all query results and collect every item."""
    items = []
    for result in results:
        if "error" in result:
            continue
        boards = result.get("data", {}).get("boards", [])
        for board in boards:
            if isinstance(board, dict):
                items_page = board.get("items_page", {})
                items.extend(items_page.get("items", []))
    return items


def _apply_filters(items: list[dict], filters: list[Filter], person_name: str, board: str = "") -> list[dict]:
    """Apply attribute filters and person_name search locally."""
    result = items

    # Person name filter
    if person_name:
        name_lower = person_name.lower()
        result = [
            item for item in result
            if name_lower in item.get("name", "").lower()
            or _search_column_values(item, name_lower)
        ]

    # Attribute filters
    for f in filters:
        result = [item for item in result if _matches_filter(item, f, board)]

    return result


def _matches_filter(item: dict, f: Filter, board: str = "") -> bool:
    """Check if an item matches a single filter."""
    value = _get_field_value(item, f.field, board)
    if value is None:
        return False

    val_str = str(value).lower()
    filter_val = str(f.value).lower()

    if f.operator == "equals":
        # Substring match for string categories/dropdowns (e.g. "dj" inside "music - dj (club & events dj)")
        # If it's pure numeric string, exact match to prevent "50" matching "5000"
        if filter_val.replace('.', '', 1).isdigit():
            return val_str == filter_val
        return filter_val in val_str
    elif f.operator == "not_equals":
        if filter_val.replace('.', '', 1).isdigit():
            return val_str != filter_val
        return filter_val not in val_str
    elif f.operator == "contains":
        return filter_val in val_str
    elif f.operator in ("less_than", "greater_than", "less_equal", "greater_equal"):
        try:
            numeric_val = float(value)
            numeric_filter = float(f.value)
            if f.operator == "less_than":    return numeric_val < numeric_filter
            if f.operator == "greater_than": return numeric_val > numeric_filter
            if f.operator == "less_equal":   return numeric_val <= numeric_filter
            if f.operator == "greater_equal":return numeric_val >= numeric_filter
        except (ValueError, TypeError):
            return False
    return False


def _apply_sort(items: list[dict], sort_by: str | None, board: str = "") -> list[dict]:
    if not sort_by:
        return items

    if sort_by == "created_at_desc":
        return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)
    elif sort_by == "pricing_asc":
        return sorted(items, key=lambda x: _numeric_field(x, "pricing", board))
    elif sort_by == "pricing_desc":
        return sorted(items, key=lambda x: _numeric_field(x, "pricing", board), reverse=True)
    elif sort_by == "experience_desc":
        return sorted(items, key=lambda x: _numeric_field(x, "experience", board), reverse=True)
    elif sort_by == "rating_desc":
        rating_order = {"Top Rated": 3, "Verified": 2, "New": 1}
        return sorted(items, key=lambda x: rating_order.get(_get_field_value(x, "rating", board) or "", 0), reverse=True)
    return items


def _apply_limit(items: list[dict], limit: int | None) -> list[dict]:
    cap = min(limit, MAX_DISPLAY_ITEMS) if limit else MAX_DISPLAY_ITEMS
    return items[:cap]


def _format_item_list(items: list[dict], response: ARIAResponse) -> str:
    lines = [f"*{len(items)} item{'s' if len(items) != 1 else ''} found:*\n"]
    for i, item in enumerate(items, 1):
        lines.append(_format_single_item(i, item))
    return "\n".join(lines)


def _format_single_item(index: int, item: dict) -> str:
    name = item.get("name", "Unknown")
    col_values = item.get("column_values", [])

    parts = [f"*{index}. {name}*"]
    for col in col_values:
        text = (col.get("text") or "").strip()
        if not text:
            continue
        label = _column_label(col.get("id") or "", col.get("title") or "")
        if label:
            parts.append(f"   {label}: {text}")

    return "\n".join(parts)


def _column_label(col_id: str, col_title: str) -> str:
    """Return a clean display label for a column."""
    title_lower = col_title.lower()
    for _, label in FIELD_LABELS.items():
        if label.lower() in title_lower or title_lower in label.lower():
            return label
    if col_title and col_title.lower() not in ("name",):
        return col_title
    return ""


def _get_field_value(item: dict, field: str, board: str = "") -> Any:
    """
    Find a column value for `field` in an item's column_values.

    Lookup priority:
      1. Exact column ID from schema_loader column_map (semantic → real ID)
      2. Exact match on col_id or col_title (case-insensitive)
      3. field with underscores→spaces matches col_title ("art_form" → "art form")
      4. Substring match on col_id or col_title

    Returns only col["text"] — never col["value"] which is a raw JSON blob.
    """
    field_lower = field.lower()
    field_spaced = field_lower.replace("_", " ")   # "art_form" → "art form"

    # Step 1: resolve exact column ID from schema column_map
    exact_col_id: str | None = None
    if board:
        column_map = get_column_map(board)
        exact_col_id = column_map.get(field_lower)

    col_values = item.get("column_values", [])

    logger.debug("_get_field_value: field=%s board=%s exact_col_id=%s", field, board, exact_col_id)

    # Step 2: if we have an exact ID, use it directly
    if exact_col_id:
        for col in col_values:
            if (col.get("id") or "").lower() == exact_col_id.lower():
                text = (col.get("text") or "").strip()
                return text if text else None

    # Step 3: fallback — exact then spaced then substring match
    for col in col_values:
        col_id    = (col.get("id")    or "").lower()
        col_title = (col.get("title") or "").lower()

        matched = (
            col_id    == field_lower
            or col_title == field_lower
            or col_title == field_spaced
            or field_lower in col_id
            or field_lower in col_title
            or field_spaced in col_id
        )
        if matched:
            text = (col.get("text") or "").strip()
            return text if text else None

    return None


def _numeric_field(item: dict, field: str, board: str = "") -> float:
    val = _get_field_value(item, field, board)
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0


def _search_column_values(item: dict, query: str) -> bool:
    for col in item.get("column_values", []):
        if query in (col.get("text") or "").lower():
            return True
    return False


def _board_label(board: str | None) -> str:
    return {"sales": "leads/clients", "artists": "artists", "staff": "staff members"}.get(board or "", "records")


def format_write_verification(verify_results: list[dict], intent: str) -> str:
    """
    Format a read-back result as a write confirmation.
    Shows what actually got saved in Monday.com — not what the LLM assumed.
    """
    items = _extract_direct_items(verify_results)
    if not items:
        return "Done."

    item = items[0]
    name = item.get("name", "Item")
    col_values = item.get("column_values", [])

    parts = []
    for col in col_values:
        text = (col.get("text") or "").strip()
        if not text:
            continue
        label = _column_label(col.get("id") or "", col.get("title") or "")
        if label:
            parts.append(f"   {label}: {text}")

    action = {
        "create_item": "Created",
        "update_item": "Updated",
        "delete_item": "Deleted",
    }.get(intent, "Done")

    lines = [f"*{action}: {name}*"]
    lines.extend(parts[:6])
    return "\n".join(lines)


def _extract_direct_items(results: list[dict]) -> list[dict]:
    """Extract items from an items(ids:[...]) direct query result."""
    items = []
    for result in results:
        if "error" in result:
            continue
        items.extend(result.get("data", {}).get("items", []))
    return items


def _extract_api_errors(results: list[dict]) -> list[str]:
    """Collect Monday.com API error messages from all results."""
    messages = []
    for result in results:
        err = result.get("error")
        if not err:
            continue
        if isinstance(err, list):
            for e in err:
                msg = e.get("message", "") if isinstance(e, dict) else str(e)
                if msg:
                    messages.append(msg)
        else:
            messages.append(str(err))
    return messages


def _format_api_error(message: str) -> str:
    """Convert a raw Monday.com error into a user-facing string."""
    msg_lower = message.lower()
    if "columnvalueexception" in msg_lower or "column value" in msg_lower:
        return (
            "Couldn't save — Monday.com rejected the value format. "
            "Check the field value and try again."
        )
    if "invalidcolumnidexception" in msg_lower or ("column" in msg_lower and "not found" in msg_lower):
        return "Couldn't save — one of the fields doesn't exist on this board."
    if "invalidboardidexception" in msg_lower:
        return "Couldn't find that board. The board ID may be wrong."
    if "itemslimitationexception" in msg_lower:
        return "Board is full — Monday.com boards have a 10,000 item limit."
    if "unauthorized" in msg_lower or "permission" in msg_lower:
        return "Access denied — the API key doesn't have permission for this action."
    # Surface the raw message but trimmed
    return f"Monday.com error: {message[:200]}"


def _empty_result_message(response: ARIAResponse) -> str:
    """
    Returns a natural-language empty result message.
    Never exposes raw filter field names or values — those are internal and
    would corrupt the group context buffer if stored.
    """
    entities = response.entities
    board = _board_label(entities.board)
    person = entities.person_name

    if person:
        return f"No '{person}' found in {board}. Check the spelling or try a different board?"
    if entities.filters or entities.sort_by:
        return f"No {board} match your current search criteria. Want to broaden the search?"
    return f"No {board} found. The board might be empty."
