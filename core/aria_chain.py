"""
ARIA LangChain chain — Gemini 2.5 Flash + structured output.
Uses with_structured_output() so Pydantic validates the response directly.
No manual JSON parsing, no StructuredOutputParser overhead.
"""

import asyncio
import logging
import time
from pathlib import Path

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

from config import (
    GEMINI_API_KEY, GEMINI_MODEL,
    GEMINI_TEMPERATURE, GEMINI_MAX_TOKENS,
    PROMPT_FILE, PROMPT_CACHE_TTL_SECONDS,
)
from core.output_schema import ARIAResponse, fallback_response
from monday.schema_loader import get_all_descriptions, get_group_id

logger = logging.getLogger(__name__)


# ── Prompt cache ──────────────────────────────────────────────

_prompt_cache: str | None = None
_prompt_cache_time: float = 0.0


def _load_prompt(vars: dict) -> str:
    global _prompt_cache, _prompt_cache_time

    now = time.monotonic()
    if _prompt_cache is None or (now - _prompt_cache_time) > PROMPT_CACHE_TTL_SECONDS:
        _prompt_cache = PROMPT_FILE.read_text(encoding="utf-8")
        _prompt_cache_time = now
        logger.info("Prompt loaded from disk (%d chars)", len(_prompt_cache))

    raw = _prompt_cache
    for key, value in vars.items():
        raw = raw.replace(f"{{{{{key}}}}}", str(value) if value else "")
    return raw


# ── Model setup ───────────────────────────────────────────────

def _build_model() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GEMINI_API_KEY,
        temperature=GEMINI_TEMPERATURE,
        max_output_tokens=GEMINI_MAX_TOKENS,
    )


_model = None


def get_model():
    global _model
    if _model is None:
        _model = _build_model()
    return _model


# ── Main invoke ───────────────────────────────────────────────

async def invoke(context_block: str) -> ARIAResponse:
    """
    Invoke ARIA with the full context block.
    context_block includes the GROUP CONTEXT transcript + invoking message.
    Returns a validated ARIAResponse.
    """
    from monday.schema_loader import BOARD_IDS

    # Build prompt variables
    board_descriptions = get_all_descriptions()
    prompt_vars = {
        "SALES_BOARD_ID":   str(BOARD_IDS["sales"]),
        "ARTISTS_BOARD_ID": str(BOARD_IDS["artists"]),
        "STAFF_BOARD_ID":   str(BOARD_IDS["staff"]),
        "SALES_GROUP_ID":   get_group_id("sales") or "",
        "ARTISTS_GROUP_ID": get_group_id("artists") or "",
        "STAFF_GROUP_ID":   get_group_id("staff") or "",
        "SALES_COLUMNS":    board_descriptions.get("sales", ""),
        "ARTISTS_COLUMNS":  board_descriptions.get("artists", ""),
        "STAFF_COLUMNS":    board_descriptions.get("staff", ""),
        "FORMAT_INSTRUCTIONS": "",  # with_structured_output handles this
    }

    system_prompt = _load_prompt(prompt_vars)
    model = get_model()
    structured_model = model.with_structured_output(ARIAResponse)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context_block),
    ]

    last_err = None
    for attempt in range(3):
        try:
            response: ARIAResponse = await structured_model.ainvoke(messages)
            logger.info(
                "ARIA response: intent=%s action=%s awaiting=%s",
                response.intent, response.action_type, response.awaiting_confirmation,
            )
            return response

        except Exception as e:
            last_err = e
            err_str = str(e)
            if "429" in err_str or "ResourceExhausted" in err_str or "quota" in err_str.lower():
                wait = 5 * (attempt + 1)
                logger.warning("Rate limited (429) — retrying in %ds (attempt %d/3)", wait, attempt + 1)
                await asyncio.sleep(wait)
                continue
            # Non-rate-limit error — fail immediately
            logger.error("ARIA chain failed: %s", e, exc_info=True)
            return fallback_response(f"[ERR] {type(e).__name__}: {str(e)[:180]}")

    logger.error("ARIA chain failed after retries: %s", last_err)
    return fallback_response("Rate limit hit — please try again in a moment.")
