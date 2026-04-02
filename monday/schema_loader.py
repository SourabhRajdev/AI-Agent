"""
Fetches board schemas (column IDs, group IDs, status labels) from Monday.com
at startup and caches them for the lifetime of the process.
"""

import logging
from typing import Any

import aiohttp

from monday.client import execute, HEADERS
from config import MONDAY_API_URL, BOARD_IDS

logger = logging.getLogger(__name__)

# Runtime schema cache — populated at startup
_schema: dict = {
    "sales":   {"columns": [], "group_id": None, "column_map": {}},
    "artists": {"columns": [], "group_id": None, "column_map": {}},
    "staff":   {"columns": [], "group_id": None, "column_map": {}},
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
        columns { id title type }
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

    logger.info(
        "Loaded %s: %d columns, group=%s",
        board_name, len(columns),
        _schema[board_name]["group_id"],
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


# ── Public accessors ─────────────────────────────────────────

def get_column_map(board: str) -> dict[str, str]:
    return _schema.get(board, {}).get("column_map", {})


def get_group_id(board: str) -> str | None:
    return _schema.get(board, {}).get("group_id")


def get_columns_description(board: str) -> str:
    """Returns a readable column list for prompt injection."""
    cols = _schema.get(board, {}).get("columns", [])
    return ", ".join(f"{c['title']} ({c['id']})" for c in cols if c["id"] != "name")


def get_all_descriptions() -> dict[str, str]:
    return {board: get_columns_description(board) for board in BOARD_IDS}
