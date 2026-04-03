"""
Pre-execution validation for Monday.com write operations.

Three layers:
  1. Schema validation  — does this field exist on the board?
  2. Type enforcement   — is the value the right type for this column?
  3. Value enforcement  — is this label a valid option for status/dropdown?

Returns human-readable error strings so ARIA can tell the user exactly
what's wrong before touching Monday.com.
"""

import difflib
import logging

from monday.schema_loader import get_column_map, get_column_types, get_valid_labels

logger = logging.getLogger(__name__)

# Column types that require numeric values
_NUMERIC_TYPES = {"numbers", "rating", "hour"}

# Column types that have a fixed set of valid labels
_LABEL_TYPES = {"status", "dropdown", "color"}


def validate_write(values_to_set: dict, board: str) -> list[str]:
    """
    Validate values_to_set against the board's live schema.

    Args:
        values_to_set: {semantic_field: value} dict from entities.values_to_set
        board:         "sales" | "artists" | "staff"

    Returns:
        List of human-readable error strings.  Empty list = valid.
        Returns [] (skip validation) if schema is not loaded yet.
    """
    if not values_to_set or not board or board == "all":
        return []

    column_map = get_column_map(board)
    if not column_map:
        # Schema not loaded — don't block the write, let Monday handle it
        logger.warning("validate_write: column_map empty for board=%s — skipping", board)
        return []

    column_types = get_column_types(board)
    valid_labels = get_valid_labels(board)
    errors: list[str] = []

    for field, value in values_to_set.items():
        field_lower = field.lower()

        # ── 1. Schema validation ───────────────────────────────
        col_id = column_map.get(field_lower)
        if col_id is None:
            known = ", ".join(sorted(column_map.keys()))
            errors.append(
                f"'{field}' doesn't exist on the {board} board. "
                f"Valid fields: {known}."
            )
            continue

        col_type = column_types.get(col_id, "unknown")

        if isinstance(value, dict) and "label" in value:
            str_val = str(value["label"]).strip()
        else:
            str_val = str(value).strip()

        # ── 2. Type enforcement ────────────────────────────────
        if col_type in _NUMERIC_TYPES:
            try:
                float(str_val)
            except (ValueError, TypeError):
                errors.append(
                    f"'{field}' expects a number, got '{value}'. "
                    f"Example: 2500"
                )
            continue

        # ── 3. Value enforcement (status / dropdown) ───────────
        if col_type in _LABEL_TYPES:
            labels = valid_labels.get(col_id)
            if labels:
                if str_val.lower() not in labels:
                    hint = _closest_label(str_val.lower(), labels)
                    suggestion = f" — did you mean *{hint}*?" if hint else ""
                    readable_labels = ", ".join(sorted(labels)[:8])
                    errors.append(
                        f"'{value}' is not a valid option for '{field}'{suggestion} "
                        f"Valid options: {readable_labels}."
                    )

    if errors:
        logger.warning("Write validation failed for board=%s: %s", board, errors)

    return errors


def _closest_label(query: str, labels: set[str]) -> str | None:
    """Find the closest valid label to the query string."""
    # 1. Substring match (query is contained in a label or vice versa)
    for label in sorted(labels):
        if query in label or label in query:
            return label

    # 2. Fuzzy match via difflib
    matches = difflib.get_close_matches(query, labels, n=1, cutoff=0.55)
    return matches[0] if matches else None
