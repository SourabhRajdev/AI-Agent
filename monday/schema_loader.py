"""
Fetches board schemas (column IDs, group IDs, status labels) from Monday.com
at startup and caches them for the lifetime of the process.
"""

import json
import logging

import aiohttp

from monday.client import execute, HEADERS
from config import MONDAY_API_URL, BOARD_IDS

logger = logging.getLogger(__name__)

# Runtime schema cache — populated at startup
_schema: dict = {
    "sales":   {"columns": [], "group_id": None, "column_map": {}, "column_types": {}, "valid_labels": {}},
    "artists": {"columns": [], "group_id": None, "column_map": {}, "column_types": {}, "valid_labels": {}},
    "staff":   {"columns": [], "group_id": None, "column_map": {}, "column_types": {}, "valid_labels": {}},
}

SEMANTIC_TO_TITLE: dict[str, list[str]] = {
    "status":          ["Status", "Pipeline Status", "Stage"],
    "phone":           ["Phone", "Phone Number"],
    "email":           ["Email", "Email Address"],
    "whatsapp":        ["WhatsApp", "WA Number"],
    "source":          ["Source", "Lead Source"],
    "assigned_ae":     ["Assigned AE", "AE", "Account Executive", "Assigned To"],
    "message":         ["Message", "Notes", "Note", "Description"],
    "last_action":     ["Last Action", "Last Activity"],
    "follow_up_date":  ["Follow Up Date", "Follow-up Date", "Next Follow Up"],
    "art_form":        ["Art Form", "Category", "Performer Type"],
    "specialisation":  ["Specialisation", "Specialization", "Sub-category"],
    "availability":    ["Availability", "Available"],
    "contract_status": ["Contract Status", "Contract"],
    "rating":          ["Rating", "Tier"],
    "pricing":         ["Pricing", "Price", "Rate", "Fee (AED)", "Rate (AED)"],
    "experience":      ["Experience", "Years Experience", "Years"],
    "role":            ["Role", "Position", "Job Title"],
    "access_level":    ["Access Level", "Access", "Permission"],
    "assigned_pipeline": ["Assigned Pipeline", "Pipeline"],
    "tasks":           ["Tasks", "Task", "Current Tasks"],
}


async def load_all() -> None:
    """Called once at startup. Fetches columns and group IDs for all boards."""
    async with aiohttp.ClientSession() as session:
        for board_name, board_id in BOARD_IDS.items():
            await _load_board(board_name, board_id, session)
    logger.info("Board schemas loaded: %s", {k: len(v["columns"]) for k, v in _schema.items()})


async def _load_board(board_name: str, board_id: int, session: aiohttp.ClientSession) -> None:
    query = """
    query {
      boards(ids: [%d]) {
        columns { id title type settings_str }
        groups { id title }
      }
    }
    """ % board_id

    result = await execute(query, session)
    boards = result.get("data", {}).get("boards", [])
    if not boards:
        logger.error("Failed to load schema for %s (board %d)", board_name, board_id)
        return

    board = boards[0]
    columns = board.get("columns", [])
    groups = board.get("groups", [])

    _schema[board_name]["columns"] = columns
    _schema[board_name]["group_id"] = groups[0]["id"] if groups else None
    _schema[board_name]["column_map"] = _build_column_map(columns)
    _schema[board_name]["column_types"] = {col["id"]: col["type"] for col in columns}
    _schema[board_name]["valid_labels"] = _build_valid_labels(columns)

    logger.info(
        "Loaded %s: %d columns, group=%s, %d label sets",
        board_name, len(columns),
        _schema[board_name]["group_id"],
        sum(1 for v in _schema[board_name]["valid_labels"].values() if v),
    )


def _build_column_map(columns: list[dict]) -> dict[str, str]:
    """Map semantic field name → real column ID."""
    title_to_id = {col["title"].lower(): col["id"] for col in columns}
    column_map = {}

    for semantic, candidates in SEMANTIC_TO_TITLE.items():
        for candidate in candidates:
            if candidate.lower() in title_to_id:
                column_map[semantic] = title_to_id[candidate.lower()]
                break

    return column_map


def _build_valid_labels(columns: list[dict]) -> dict[str, set[str]]:
    """
    Parse settings_str for status and dropdown columns.
    Returns {col_id: set_of_valid_label_strings_lowercased}.
    """
    valid: dict[str, set[str]] = {}
    for col in columns:
        col_type = col.get("type", "")
        if col_type not in ("status", "dropdown", "color"):
            continue
        labels = _parse_labels(col_type, col.get("settings_str") or "")
        if labels:
            valid[col["id"]] = labels
            logger.debug("Loaded %d labels for %s (%s)", len(labels), col["title"], col["id"])
    return valid


def _parse_labels(col_type: str, settings_str: str) -> set[str]:
    """Extract valid label strings from a column's settings_str JSON."""
    if not settings_str:
        return set()
    try:
        settings = json.loads(settings_str)
    except (json.JSONDecodeError, ValueError):
        return set()

    raw = settings.get("labels", {})

    if col_type == "status":
        # {"0": "Working on it", "1": "Done", ...}
        if isinstance(raw, dict):
            return {v.lower() for v in raw.values() if v}

    elif col_type in ("dropdown", "color"):
        # [{"id": 1, "name": "Music - DJ"}, ...]
        if isinstance(raw, list):
            return {item["name"].lower() for item in raw if "name" in item}

    return set()


# ── Public accessors ─────────────────────────────────────────

def get_column_map(board: str) -> dict[str, str]:
    return _schema.get(board, {}).get("column_map", {})


def get_column_types(board: str) -> dict[str, str]:
    """Returns {col_id: col_type} for the board."""
    return _schema.get(board, {}).get("column_types", {})


def get_valid_labels(board: str) -> dict[str, set[str]]:
    """Returns {col_id: set_of_valid_label_strings} for status/dropdown columns."""
    return _schema.get(board, {}).get("valid_labels", {})


def get_group_id(board: str) -> str | None:
    return _schema.get(board, {}).get("group_id")


def get_columns_description(board: str) -> str:
    """Returns a readable column list for prompt injection."""
    cols = _schema.get(board, {}).get("columns", [])
    return ", ".join(f"{c['title']} ({c['id']})" for c in cols if c["id"] != "name")


def get_all_descriptions() -> dict[str, str]:
    return {board: get_columns_description(board) for board in BOARD_IDS}
