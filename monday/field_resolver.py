"""
Semantic field resolver.

Maps any field representation to a live Monday.com column ID.

Resolution order (stops at first match):
  1. Column ID pass-through   — "text_mm1rrhc0" (already a valid column ID)
  2. Semantic key lookup      — "assigned_ae" → column_id via column_map
  3. Underscore ↔ space norm  — "assigned ae" → "assigned_ae"
  4. SEMANTIC_TO_TITLE reverse — "Assigned AE" / "AE" / "Account Executive"
  5. Live title match         — any actual Monday.com column title
  6. Fuzzy match              — difflib across all titles and semantic keys

No hardcoded field mappings.  All knowledge comes from SEMANTIC_TO_TITLE
(schema_loader) and the live board schema populated at startup.
"""

import difflib
import logging

from monday.schema_loader import SEMANTIC_TO_TITLE, get_column_map, get_column_types
from monday import schema_loader as _schema_module

logger = logging.getLogger(__name__)


# ── Reverse map (built once at import) ───────────────────────────────────────
# Derived entirely from SEMANTIC_TO_TITLE — no hardcoded mappings.
# Maps every lowercase title variant → canonical semantic key.

def _build_reverse_map() -> dict[str, str]:
    reverse: dict[str, str] = {}
    for semantic, titles in SEMANTIC_TO_TITLE.items():
        reverse[semantic] = semantic
        reverse[semantic.replace("_", " ")] = semantic
        for title in titles:
            t = title.lower()
            reverse[t] = semantic
            reverse[t.replace(" ", "_")] = semantic
            reverse[t.replace("_", " ")] = semantic
    return reverse


_REVERSE_MAP: dict[str, str] = _build_reverse_map()


# ── Primary public API ────────────────────────────────────────────────────────

def resolve_to_column_id(field: str, board: str) -> str | None:
    """
    Resolve any field representation to a Monday.com column ID.

    Accepts:
    - Raw column IDs     ("text_mm1rrhc0")
    - Semantic keys      ("assigned_ae")
    - Human titles       ("Assigned AE", "Assigned Manager", "AE")
    - Fuzzy variants     ("assignd manager", "price", "rate")

    Returns the column ID string (e.g. "text_mm1rrhc0") or None.
    """
    if not field:
        return None

    col_types = get_column_types(board)
    column_map = get_column_map(board)

    # 1. Already a valid column ID for this board
    if field in col_types:
        return field
    f = field.strip()
    if f != field and f in col_types:
        return f

    f_lower = f.lower()
    f_underscored = f_lower.replace(" ", "_")
    f_spaced = f_lower.replace("_", " ")

    # 2. Semantic key in column_map
    for variant in (f_lower, f_underscored, f_spaced):
        col_id = column_map.get(variant)
        if col_id:
            return col_id

    # 3. SEMANTIC_TO_TITLE reverse lookup → semantic key → column_id
    for variant in (f_lower, f_underscored, f_spaced):
        semantic = _REVERSE_MAP.get(variant)
        if semantic:
            col_id = column_map.get(semantic)
            if col_id:
                logger.debug("Field resolved via title map: '%s' → '%s' → %s", field, semantic, col_id)
                return col_id

    # 4. Direct live column title scan (covers titles not in SEMANTIC_TO_TITLE)
    col_id = _scan_live_titles(f_lower, f_underscored, f_spaced, board)
    if col_id:
        logger.debug("Field resolved via live schema title: '%s' → %s", field, col_id)
        return col_id

    # 5. Fuzzy match across all known titles + semantic keys
    col_id = _fuzzy_resolve(f_lower, board)
    if col_id:
        logger.info("Field fuzzy-resolved: '%s' → %s (board=%s)", field, col_id, board)
        return col_id

    return None


def resolve_field(field: str, board: str) -> str | None:
    """
    Resolve to canonical semantic key (used for logging / non-critical paths).
    For validation, prefer resolve_to_column_id which is authoritative.
    """
    if not field:
        return None

    column_map = get_column_map(board)
    if not column_map:
        return None

    f = field.strip().lower()
    f_underscored = f.replace(" ", "_")
    f_spaced = f.replace("_", " ")

    for variant in (f, f_underscored, f_spaced):
        if variant in column_map:
            return variant

    for variant in (f, f_underscored, f_spaced):
        semantic = _REVERSE_MAP.get(variant)
        if semantic and semantic in column_map:
            return semantic

    return None


def resolve_values_to_set(values_to_set: dict, board: str) -> tuple[dict, list[str]]:
    """
    Normalize all keys in values_to_set to canonical semantic keys.
    Used for logging / downstream processing only.
    """
    resolved: dict = {}
    unresolved: list[str] = []

    for field, value in values_to_set.items():
        semantic = resolve_field(field, board)
        if semantic:
            if semantic != field:
                logger.info("values_to_set resolved: '%s' → '%s' (board=%s)", field, semantic, board)
            resolved[semantic] = value
        else:
            logger.warning("Unresolvable field: '%s' (board=%s)", field, board)
            unresolved.append(field)

    return resolved, unresolved


# ── Internal helpers ──────────────────────────────────────────────────────────

def _scan_live_titles(f: str, f_underscored: str, f_spaced: str, board: str) -> str | None:
    """
    Scan every column in the live board schema for a title match.
    Returns the column ID if found, None otherwise.
    Covers columns whose titles don't appear in SEMANTIC_TO_TITLE
    (e.g. "Assigned Manager" on artists board).
    """
    columns = _schema_module._schema.get(board, {}).get("columns", [])

    for col in columns:
        t = col.get("title", "").lower()
        t_underscored = t.replace(" ", "_")
        t_spaced = t.replace("_", " ")

        if f in (t, t_underscored, t_spaced):
            return col.get("id")
        if f_underscored in (t, t_underscored):
            return col.get("id")
        if f_spaced in (t, t_spaced):
            return col.get("id")

    return None


def _fuzzy_resolve(query: str, board: str) -> str | None:
    """
    Fuzzy match the query against all column titles + semantic key variants.
    Returns column ID of best match, or None.
    """
    columns = _schema_module._schema.get(board, {}).get("columns", [])
    col_types = get_column_types(board)
    column_map = get_column_map(board)

    # Build candidate map: normalized_string → column_id
    candidates: dict[str, str] = {}

    # From live schema titles
    for col in columns:
        t = col.get("title", "").lower()
        col_id = col.get("id", "")
        if col_id:
            candidates[t] = col_id
            candidates[t.replace(" ", "_")] = col_id
            candidates[t.replace("_", " ")] = col_id

    # From semantic keys + their SEMANTIC_TO_TITLE variants
    for semantic, col_id in column_map.items():
        candidates[semantic] = col_id
        candidates[semantic.replace("_", " ")] = col_id
        for title_variant, sem in _REVERSE_MAP.items():
            if sem == semantic:
                candidates[title_variant] = col_id

    all_keys = list(candidates.keys())
    matches = difflib.get_close_matches(query, all_keys, n=1, cutoff=0.60)
    if matches:
        return candidates[matches[0]]
    return None
