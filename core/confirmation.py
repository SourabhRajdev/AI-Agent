"""
Pending write confirmation state.

Scoped per (chat_id, user_id) — two users can have simultaneous pending
writes in the same group without collision.
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional

from core.output_schema import ARIAResponse
from config import CONFIRMATION_TTL_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class PendingWrite:
    response: ARIAResponse
    created_at: float

    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > CONFIRMATION_TTL_SECONDS


# (chat_id, user_id) → PendingWrite
_pending: dict[tuple[int, int], PendingWrite] = {}


# ── Write ─────────────────────────────────────────────────────

def save(chat_id: int, user_id: int, response: ARIAResponse) -> None:
    _pending[(chat_id, user_id)] = PendingWrite(
        response=response,
        created_at=time.monotonic(),
    )
    logger.info("Pending write saved for user %d in chat %d: %s", user_id, chat_id, response.intent)


def get(chat_id: int, user_id: int) -> Optional[ARIAResponse]:
    """Returns the pending write if it exists and hasn't expired."""
    key = (chat_id, user_id)
    pending = _pending.get(key)
    if not pending:
        return None
    if pending.is_expired():
        logger.info("Pending write expired for user %d in chat %d", user_id, chat_id)
        del _pending[key]
        return None
    return pending.response


def clear(chat_id: int, user_id: int) -> None:
    _pending.pop((chat_id, user_id), None)


# ── Intent detection ──────────────────────────────────────────

_CONFIRMATIONS = frozenset([
    "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "do it",
    "confirm", "proceed", "go ahead", "correct", "right",
    "absolutely", "sounds good", "done", "go", "execute",
])

# Strong single-word affirmatives that may lead a longer sentence
_LEADING_YES = frozenset(["yes", "yeah", "yep", "yup", "ok", "okay", "sure", "absolutely"])

_CANCELLATIONS = frozenset([
    "no", "nope", "nah", "cancel", "nevermind", "never mind",
    "stop", "don't", "dont", "skip", "forget it", "abort", "drop it",
])

_CANCEL_WORDS = frozenset(["no", "cancel", "stop", "don't", "dont", "nevermind", "abort"])


def _clean(text: str) -> str:
    """Strip @mentions and normalise for intent matching."""
    import re
    return re.sub(r"@\w+", "", text).strip().lower().rstrip("!?.")


def is_confirmation(text: str) -> bool:
    normalized = _clean(text)
    # Exact match ("yes", "ok", "go ahead", etc.)
    if normalized in _CONFIRMATIONS:
        return True
    # Leading affirmative: "yes do it", "yes go ahead", "yes whatever you need"
    # Reject if any cancellation word also appears to avoid "yes but cancel"
    words = normalized.split()
    if words and words[0] in _LEADING_YES:
        return not bool(set(words[1:]) & _CANCEL_WORDS)
    return False


def is_cancellation(text: str) -> bool:
    return _clean(text) in _CANCELLATIONS
