"""
ARIA output schema — Pydantic v2.
LangChain's with_structured_output() validates and parses the model's JSON
directly into this model. No manual parsing needed.
"""

import json
import logging
from typing import Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class Filter(BaseModel):
    field: str
    operator: Literal[
        "equals", "not_equals", "contains",
        "less_than", "greater_than", "less_equal", "greater_equal",
    ]
    value: Union[str, int, float]

    @field_validator("operator", mode="before")
    @classmethod
    def normalize_operator(cls, v):
        """Normalize operator variants the model might return."""
        mapping = {
            "contains_text": "contains",
            "not_contains": "not_equals",
            "is": "equals",
            "is_not": "not_equals",
            "gte": "greater_equal",
            "lte": "less_equal",
            "gt": "greater_than",
            "lt": "less_than",
        }
        return mapping.get(str(v).lower() if v else "", v)

    @field_validator("value", mode="before")
    @classmethod
    def coerce_value(cls, v):
        if v is None:
            return ""
        if isinstance(v, (dict, list)):
            return str(v)
        return v


class Entities(BaseModel):
    person_name: str = Field(default="", description="Person name only. Empty string if none. Never in filters.")
    board: Optional[Literal["sales", "artists", "staff", "all"]] = Field(
        default=None,
        description="Target board. Default 'sales' when ambiguous. null only for greetings.",
    )
    limit: Optional[int] = Field(default=None, description="Numeric constraint from message. null if none.")
    sort_by: Optional[Literal[
        "created_at_desc", "pricing_asc", "pricing_desc",
        "experience_desc", "rating_desc",
    ]] = Field(default=None)
    filters: list[Filter] = Field(default_factory=list)
    values_to_set: dict = Field(default_factory=dict)

    @field_validator("limit", mode="before")
    @classmethod
    def coerce_limit(cls, v):
        if v is None or v in ("null", "none", ""):
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    @field_validator("values_to_set", mode="before")
    @classmethod
    def coerce_values_to_set(cls, v):
        if v is None:
            return {}
        if isinstance(v, str):
            try:
                result = json.loads(v) if v.strip() else {}
                return result if isinstance(result, dict) else {}
            except (json.JSONDecodeError, ValueError):
                return {}
        return v if isinstance(v, dict) else {}

    @field_validator("filters", mode="before")
    @classmethod
    def coerce_filters(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            try:
                result = json.loads(v) if v.strip() else []
                return result if isinstance(result, list) else []
            except (json.JSONDecodeError, ValueError):
                return []
        return v if isinstance(v, list) else []


class ARIAResponse(BaseModel):
    reasoning: str = Field(description="20+ words. Board detected, intent classified, entities extracted.")
    intent: Literal[
        "list_filtered", "list_all", "count",
        "search_by_name", "create_item", "update_item", "delete_item",
        "cross_board_search", "follow_up",
        "greeting", "chitchat", "clarify",
    ]
    entities: Entities
    action_type: Literal["read", "write", "none"]
    queries: list[str] = Field(default_factory=list)
    needs_data: bool
    message: str = Field(default="")
    awaiting_confirmation: bool = Field(default=False)

    @field_validator("needs_data", "awaiting_confirmation", mode="before")
    @classmethod
    def coerce_bool(cls, v):
        if isinstance(v, str):
            return v.lower().strip() in ("true", "1", "yes")
        return bool(v) if v is not None else False

    @field_validator("entities")
    @classmethod
    def person_name_not_in_filters(cls, v: Entities) -> Entities:
        v.filters = [
            f for f in v.filters
            if f.field not in ("person_name", "name") and "name" not in f.field.lower()
        ]
        return v

    @field_validator("queries")
    @classmethod
    def queries_must_be_strings(cls, v: list) -> list[str]:
        """Coerce any non-string query entries and log if read/write has no queries."""
        return [str(q) for q in v if q]


# ── Fallback response for chain failures ──────────────────────

def fallback_response(raw_text: str = "") -> ARIAResponse:
    return ARIAResponse(
        reasoning="Parse failure — returning safe fallback.",
        intent="chitchat",
        entities=Entities(),
        action_type="none",
        queries=[],
        needs_data=False,
        message=raw_text[:300] if raw_text else "Something went wrong. Try again.",
        awaiting_confirmation=False,
    )
