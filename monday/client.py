"""
Async Monday.com GraphQL client.
Handles execution, ITEM_ID_PLACEHOLDER resolution, and rate limiting.
"""

import asyncio
import logging
import re
from typing import Any

import aiohttp

from config import MONDAY_API_KEY, MONDAY_API_URL, BOARD_IDS

logger = logging.getLogger(__name__)

HEADERS = {
    "Authorization": MONDAY_API_KEY,
    "Content-Type": "application/json",
    "API-Version": "2025-04",
}

MAX_RETRIES = 3
RETRY_DELAY = 2.0


# ── Core executor ─────────────────────────────────────────────

async def execute(query: str, session: aiohttp.ClientSession) -> dict:
    """Execute a single GraphQL query/mutation. Returns parsed JSON."""
    payload = {"query": query}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.post(
                MONDAY_API_URL,
                json=payload,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (429, 423):
                    retry_after = float(resp.headers.get("Retry-After", RETRY_DELAY * attempt))
                    reason = "Rate limited" if resp.status == 429 else "Board locked"
                    logger.warning("%s — waiting %.1fs (attempt %d)", reason, retry_after, attempt)
                    await asyncio.sleep(retry_after)
                    continue

                data = await resp.json()

                if "errors" in data:
                    logger.error("Monday.com GraphQL error: %s", data["errors"])
                    return {"error": data["errors"]}

                return data

        except aiohttp.ClientError as e:
            logger.warning("Monday.com request failed (attempt %d): %s", attempt, e)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY * attempt)

    return {"error": "Monday.com request failed after all retries"}


async def execute_queries(queries: list[str]) -> list[dict]:
    """Execute multiple queries sequentially, sharing one session."""
    results = []
    async with aiohttp.ClientSession() as session:
        for query in queries:
            result = await execute(query, session)
            results.append(result)
    return results


# ── ITEM_ID_PLACEHOLDER resolver ─────────────────────────────

async def resolve_item_id_placeholder(
    queries: list[str],
    board_id: int,
    person_name: str,
) -> list[str]:
    """
    For 2-step write operations (search → mutate):
    Executes the search query, extracts the item ID, and substitutes
    ITEM_ID_PLACEHOLDER in the mutation query.
    """
    if not any("ITEM_ID_PLACEHOLDER" in q for q in queries):
        return queries

    search_queries = [q for q in queries if "ITEM_ID_PLACEHOLDER" not in q]
    mutation_queries = [q for q in queries if "ITEM_ID_PLACEHOLDER" in q]

    if not search_queries:
        logger.error("ITEM_ID_PLACEHOLDER present but no search query found")
        return queries

    # Execute search
    results = await execute_queries(search_queries)
    item_id = _extract_first_item_id(results)

    if not item_id:
        logger.warning("Could not resolve item ID for '%s' on board %d", person_name, board_id)
        return queries  # return as-is; caller handles empty result

    logger.info("Resolved ITEM_ID_PLACEHOLDER → %s for '%s'", item_id, person_name)

    resolved = [q.replace("ITEM_ID_PLACEHOLDER", str(item_id)) for q in mutation_queries]
    return resolved


def _extract_first_item_id(results: list[dict]) -> str | None:
    """Walk the result tree to find the first item ID."""
    for result in results:
        boards = result.get("data", {}).get("boards", [])
        for board in boards:
            if isinstance(board, dict):
                items = board.get("items_page", {}).get("items", [])
                if items:
                    return items[0]["id"]
    return None


# ── Write verification ────────────────────────────────────────

_WRITE_MUTATION_KEYS = (
    "create_item",
    "change_multiple_column_values",
    "change_column_value",
    "change_simple_column_value",
)


def _extract_written_item_id(results: list[dict]) -> str | None:
    """Walk mutation results to find the written item's ID."""
    for result in results:
        data = result.get("data", {})
        for key in _WRITE_MUTATION_KEYS:
            item = data.get(key)
            if item and isinstance(item, dict):
                item_id = item.get("id")
                if item_id:
                    return str(item_id)
    return None


async def verify_write_result(mutation_results: list[dict]) -> list[dict]:
    """
    After a write mutation, read back the item to confirm what actually got saved.
    Returns the items(ids:[...]) query result, or [] if no item ID was found.
    """
    item_id = _extract_written_item_id(mutation_results)
    if not item_id:
        return []

    query = """
    query {
      items(ids: [%s]) {
        id
        name
        column_values { id title text }
      }
    }
    """ % item_id

    logger.info("Verifying write — reading back item %s", item_id)
    async with aiohttp.ClientSession() as session:
        result = await execute(query, session)
    return [result]


# ── Board ID resolver ─────────────────────────────────────────

def get_board_id(board_name: str) -> int:
    return BOARD_IDS.get(board_name, BOARD_IDS["sales"])


def inject_board_ids(query: str, board: str) -> str:
    """Replace {{BOARD_ID}} style placeholders with real IDs."""
    replacements = {
        "{{SALES_BOARD_ID}}":   str(BOARD_IDS["sales"]),
        "{{ARTISTS_BOARD_ID}}": str(BOARD_IDS["artists"]),
        "{{STAFF_BOARD_ID}}":   str(BOARD_IDS["staff"]),
    }
    for placeholder, value in replacements.items():
        query = query.replace(placeholder, value)
    return query


def inject_all_board_ids(queries: list[str]) -> list[str]:
    return [_ensure_items_page_limit(inject_board_ids(q, "")) for q in queries]


def _ensure_items_page_limit(query: str, default_limit: int = 200) -> str:
    """
    If a query contains items_page without a limit argument, inject limit: N.
    Prevents Monday.com's default of 25 items silently truncating results.
    """
    if re.search(r'items_page\s*\(', query):
        return query
    return re.sub(r'\bitems_page\b', f'items_page(limit: {default_limit})', query)


# Operators that map cleanly to Monday.com's numeric comparators.
# String/dropdown operators (equals, contains) are intentionally excluded —
# they stay client-side to handle fuzzy label matching ("DJ" → "Music - DJ").
_NUMERIC_OP_MAP = {
    "less_than":    "lower_than",
    "less_equal":   "lower_than",
    "greater_than": "greater_than",
    "greater_equal": "greater_than",
}


def inject_server_filters(queries: list[str], filters: list, column_map: dict) -> list[str]:
    """
    Inject numeric filters as server-side query_params into items_page.

    Only numeric operators are pushed to the API — Monday.com handles these
    reliably on its end.  String/dropdown filters (equals, contains) remain
    client-side so fuzzy label normalization still applies.

    Skips queries that already contain query_params (idempotent).
    """
    rules = []
    for f in filters:
        monday_op = _NUMERIC_OP_MAP.get(f.operator)
        if not monday_op:
            continue
        col_id = column_map.get(f.field.lower())
        if not col_id:
            logger.debug("inject_server_filters: no column_id for field=%s", f.field)
            continue
        rules.append(
            f'{{ column_id: "{col_id}", compare_value: ["{f.value}"], operator: {monday_op} }}'
        )

    if not rules:
        return queries

    rules_str = ", ".join(rules)
    query_params = f'query_params: {{ rules: [{rules_str}], operator: and }}'

    result = []
    for q in queries:
        if "query_params" in q:
            result.append(q)
            continue
        # Replace items_page(limit: N) → items_page(limit: N, query_params: {...})
        modified = re.sub(
            r'items_page\(limit:\s*(\d+)\)',
            lambda m: f'items_page(limit: {m.group(1)}, {query_params})',
            q,
        )
        result.append(modified)
        if modified != q:
            logger.debug("inject_server_filters: injected %d rule(s) into query", len(rules))

    return result
