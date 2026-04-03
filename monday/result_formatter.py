"""
Formats Monday.com API results into clean Telegram messages.
Applies limit, sort_by, and filters from the agent's entities.
"""

import logging
import re
from typing import Any

from core.output_schema import ARIAResponse, Entities, Filter

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
    items = _extract_items(raw_results)

    if not items:
        return _empty_result_message(response)

    entities = response.entities
    items = _apply_filters(items, entities.filters, entities.person_name)
    items = _apply_sort(items, entities.sort_by)
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


def _apply_filters(items: list[dict], filters: list[Filter], person_name: str) -> list[dict]:
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
        result = [item for item in result if _matches_filter(item, f)]

    return result


def _matches_filter(item: dict, f: Filter) -> bool:
    """Check if an item matches a single filter."""
    value = _get_field_value(item, f.field)
    if value is None:
        return False

    val_str = str(value).lower()
    filter_val = str(f.value).lower()

    if f.operator == "equals":
        # Normalize Monday.com dropdown labels that may have parenthetical subtypes
        # e.g. "Music - DJ (Club & Events DJ)" should match filter "Music - DJ"
        val_normalized = re.split(r'\s*\(', val_str)[0].strip()
        return val_normalized == filter_val or val_str == filter_val
    elif f.operator == "not_equals":
        val_normalized = re.split(r'\s*\(', val_str)[0].strip()
        return val_normalized != filter_val and val_str != filter_val
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


def _apply_sort(items: list[dict], sort_by: str | None) -> list[dict]:
    if not sort_by:
        return items

    if sort_by == "created_at_desc":
        return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)
    elif sort_by == "pricing_asc":
        return sorted(items, key=lambda x: _numeric_field(x, "pricing"))
    elif sort_by == "pricing_desc":
        return sorted(items, key=lambda x: _numeric_field(x, "pricing"), reverse=True)
    elif sort_by == "experience_desc":
        return sorted(items, key=lambda x: _numeric_field(x, "experience"), reverse=True)
    elif sort_by == "rating_desc":
        rating_order = {"Top Rated": 3, "Verified": 2, "New": 1}
        return sorted(items, key=lambda x: rating_order.get(_get_field_value(x, "rating") or "", 0), reverse=True)
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


def _get_field_value(item: dict, field: str) -> Any:
    """
    Find a column value by matching field name against column IDs and titles.

    Matching rules (in priority order):
      1. Exact match on col_id or col_title
      2. field_lower is substring of col_id or col_title
      3. field with underscores→spaces matches col_title ("art_form" → "art form")

    Returns only col["text"] — never col["value"] which is a raw JSON blob
    for status/dropdown columns and would corrupt filtering.
    """
    field_lower = field.lower()
    field_spaced = field_lower.replace("_", " ")   # "art_form" → "art form"

    for col in item.get("column_values", []):
        col_id    = (col.get("id")    or "").lower()
        col_title = (col.get("title") or "").lower()

        matched = (
            field_lower in col_id
            or field_lower in col_title
            or field_spaced == col_title          # "art form" == "art form"  ✓
            or field_spaced in col_id
        )
        if matched:
            text = (col.get("text") or "").strip()
            return text if text else None

    return None


def _numeric_field(item: dict, field: str) -> float:
    val = _get_field_value(item, field)
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
