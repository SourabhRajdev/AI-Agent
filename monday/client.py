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
    "API-Version": "2024-01",
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
                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", RETRY_DELAY * attempt))
                    logger.warning("Rate limited — waiting %.1fs (attempt %d)", retry_after, attempt)
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
    return [inject_board_ids(q, "") for q in queries]
