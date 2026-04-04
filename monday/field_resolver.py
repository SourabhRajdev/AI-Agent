"""
Semantic field resolver.

Maps any human-readable field name variant to its canonical semantic key,
then to the live Monday.com column ID.

Resolution order (stops at first match):
  1. Direct match in column_map              "assigned_ae"
  2. Underscore ↔ space normalization        "assigned ae" → "assigned_ae"
  3. SEMANTIC_TO_TITLE reverse lookup        "Assigned AE" / "AE" / "Account Executive"
  4. Live board column title match           actual Monday.com column titles
  5. Fuzzy match across all candidates       difflib, cutoff=0.60

No hardcoded field mappings.  All knowledge comes from SEMANTIC_TO_TITLE
(schema_loader) and the live board schema populated at startup.
"""

import difflib
import logging

from monday.schema_loader import SEMANTIC_TO_TITLE, get_column_map
from monday import schema_loader as _schema_module

logger = logging.getLogger(__name__)


# ── Reverse map (built once at import) ───────────────────────────────────────
# Derived entirely from SEMANTIC_TO_TITLE.
# Maps every lowercase title variant → canonical semantic key.
# e.g. "assigned ae" → "assigned_ae", "ae" → "assigned_ae", etc.

def _build_reverse_map() -> dict[str, str]:
    reverse: dict[str, str] = {}
    for semantic, titles in SEMANTIC_TO_TITLE.items():
        # Include the canonical key itself
        reverse[semantic] = semantic
        reverse[semantic.replace("_", " ")] = semantic
        for title in titles:
            t = title.lower()
            reverse[t] = semantic
            reverse[t.replace(" ", "_")] = semantic
            reverse[t.replace("_", " ")] = semantic
    return reverse


_REVERSE_MAP: dict[str, str] = _build_reverse_map()


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_field(field: str, board: str) -> str | None:
    """
    Resolve any field name to its canonical semantic key for the given board.

    The canonical key is what column_map uses as its key (e.g. "assigned_ae").
    Returns None only if no match is found after all five resolution stages.
    """
    if not field:
        return None

    column_map = get_column_map(board)
    if not column_map:
        return None

    f = field.strip().lower()
    f_underscored = f.replace(" ", "_")
    f_spaced = f.replace("_", " ")

    # 1. Direct match in column_map
    for variant in (f, f_underscored, f_spaced):
        if variant in column_map:
            return variant

    # 2. Reverse map lookup via SEMANTIC_TO_TITLE
    for variant in (f, f_underscored, f_spaced):
        semantic = _REVERSE_MAP.get(variant)
        if semantic and semantic in column_map:
            if semantic != field:
                logger.debug("Field resolved via title map: '%s' → '%s'", field, semantic)
            return semantic

    # 3. Live board column title match
    semantic = _match_live_columns(f, f_underscored, f_spaced, board)
    if semantic and semantic in column_map:
        logger.debug("Field resolved via live schema: '%s' → '%s'", field, semantic)
        return semantic

    # 4. Fuzzy match (last resort)
    semantic = _fuzzy_resolve(f, column_map)
    if semantic:
        logger.info("Field fuzzy-resolved: '%s' → '%s' (board=%s)", field, semantic, board)
        return semantic

    return None


def resolve_column_id(field: str, board: str) -> str | None:
    """
    Resolve any field name all the way to the actual Monday.com column ID.
    Returns None if unresolvable.
    """
    semantic = resolve_field(field, board)
    if not semantic:
        return None
    return get_column_map(board).get(semantic)


def resolve_values_to_set(values_to_set: dict, board: str) -> tuple[dict, list[str]]:
    """
    Normalize all keys in values_to_set to canonical semantic keys.

    Returns:
        resolved   — {canonical_semantic_key: value} for all resolvable fields
        unresolved — original field names that could not be resolved
    """
    resolved: dict = {}
    unresolved: list[str] = []

    for field, value in values_to_set.items():
        semantic = resolve_field(field, board)
        if semantic:
            if semantic != field:
                logger.info(
                    "values_to_set field resolved: '%s' → '%s' (board=%s)",
                    field, semantic, board,
                )
            resolved[semantic] = value
        else:
            logger.warning("Unresolvable field: '%s' (board=%s)", field, board)
            unresolved.append(field)

    return resolved, unresolved


# ── Internal helpers ──────────────────────────────────────────────────────────

def _match_live_columns(f: str, f_underscored: str, f_spaced: str, board: str) -> str | None:
    """Match against actual Monday.com column titles from the live board schema."""
    columns = _schema_module._schema.get(board, {}).get("columns", [])

    for col in columns:
        raw_title = col.get("title", "")
        title = raw_title.lower()
        title_underscored = title.replace(" ", "_")
        title_spaced = title.replace("_", " ")

        if f in (title, title_underscored, title_spaced) or f_underscored == title_underscored:
            # Matched a live column — find its semantic key
            for semantic, candidates in SEMANTIC_TO_TITLE.items():
                for candidate in candidates:
                    if candidate.lower() == title:
                        return semantic
            # No semantic mapping — normalize the title as fallback key
            return title_underscored

    return None


def _fuzzy_resolve(query: str, column_map: dict) -> str | None:
    """Fuzzy match against all semantic keys and known title variants for this board."""
    # Build candidate set: only include variants whose semantic key exists in this board's column_map
    candidates: dict[str, str] = {}  # normalized_variant → semantic_key

    for semantic in column_map:
        candidates[semantic] = semantic
        candidates[semantic.replace("_", " ")] = semantic

    for title_variant, semantic in _REVERSE_MAP.items():
        if semantic in column_map:
            candidates[title_variant] = semantic

    matches = difflib.get_close_matches(query, list(candidates.keys()), n=1, cutoff=0.60)
    if matches:
        return candidates[matches[0]]
    return None
