"""
Second Gemini pass — rewrites structured Monday.com results as natural language.

ARIA speaks like a sharp human chief of staff in a group chat.
Not a data dump. Not a table. A person replying.
"""

import logging
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are ARIA — AI Chief of Staff at Denicx Entertainment Dubai.
You just pulled data from the company CRM in response to a team member's request.
Rewrite the data as a natural, direct reply — the way a sharp ops person would message in a group chat.

RULES:
- Lead with the key number or insight. No preamble.
- Use *bold* for names and key values (Telegram Markdown only — no headers, no ---).
- For lists: number entries, keep each tight — name + 2-3 most relevant fields only.
- For a single result: go deeper, share more fields.
- For empty results: never say "nothing found". Suggest an alternative.
- For counts: state the number with context ("14 leads, 6 qualified").
- Add useful commentary when obvious: "only 2 under budget", "all available this weekend".
- If more than 10 items: show top 10, add "— want more?" at the end.
- For confirmed writes: one line confirming what was done.
- BANNED phrases: "Based on your request", "Here are the results", "I found", "As requested",
  "I have successfully", "The operation was successful", "Certainly", "Of course".
- Max 300 words. Be tight.
"""

_model = None


def _get_model() -> ChatGoogleGenerativeAI:
    global _model
    if _model is None:
        _model = ChatGoogleGenerativeAI(
            model=GEMINI_MODEL,
            google_api_key=GEMINI_API_KEY,
            temperature=0.4,
            max_output_tokens=600,
        )
    return _model


# Intents where rewriting adds no value — return as-is
_SKIP_INTENTS = frozenset(["greeting", "chitchat", "clarify"])


async def rewrite(raw_data: str, original_request: str, intent: str) -> str:
    """
    Takes the Python-formatted data string and rewrites it as natural language.
    Falls back to raw_data silently if anything fails.

    Args:
        raw_data:         Output from result_formatter (structured Markdown table)
        original_request: The cleaned user request (@mention stripped)
        intent:           ARIA's classified intent (for skip logic)

    Returns:
        Natural language reply string.
    """
    if intent in _SKIP_INTENTS:
        return raw_data

    human_message = f"REQUEST: {original_request}\n\nDATA:\n{raw_data}"

    try:
        model = _get_model()
        result = await model.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=human_message),
        ])
        text = result.content.strip()
        return text if text else raw_data
    except Exception as e:
        logger.error("Response rewriter failed: %s", e)
        return raw_data  # always fall back — never break the reply
