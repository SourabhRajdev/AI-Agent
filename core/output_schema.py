"""
ARIA output schema — Pydantic v2.
LangChain's with_structured_output() validates and parses the model's JSON
directly into this model. No manual parsing needed.
"""

import json
from typing import Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator


class Filter(BaseModel):
    field: str
    operator: Literal[
        "equals", "not_equals", "contains",
        "less_than", "greater_than", "less_equal", "greater_equal",
    ]
    value: Union[str, int, float]


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

    @field_validator("values_to_set", mode="before")
    @classmethod
    def coerce_values_to_set(cls, v):
        if isinstance(v, str):
            try:
                result = json.loads(v) if v.strip() else {}
                return result if isinstance(result, dict) else {}
            except (json.JSONDecodeError, ValueError):
                return {}
        return v or {}

    @field_validator("filters", mode="before")
    @classmethod
    def coerce_filters(cls, v):
        if isinstance(v, str):
            try:
                result = json.loads(v) if v.strip() else []
                return result if isinstance(result, list) else []
            except (json.JSONDecodeError, ValueError):
                return []
        return v or []


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

    @field_validator("entities")
    @classmethod
    def person_name_not_in_filters(cls, v: Entities) -> Entities:
        for f in v.filters:
            if f.field in ("person_name", "name") or "name" in f.field.lower():
                raise ValueError("person_name must not appear in filters[]")
        return v

    @field_validator("queries")
    @classmethod
    def read_write_needs_queries(cls, v: list[str], info) -> list[str]:
        data = info.data
        action = data.get("action_type")
        awaiting = data.get("awaiting_confirmation", False)
        if action in ("read", "write") and not v and not awaiting:
            raise ValueError(f"action_type '{action}' requires non-empty queries[]")
        return v


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
