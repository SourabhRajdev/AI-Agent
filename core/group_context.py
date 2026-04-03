"""
GroupContext — per-group circular message buffer.

Stores the last N messages from every user in the group.
Used to build the conversation transcript injected into ARIA's context.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import logging

from config import GROUP_CONTEXT_MAX_MESSAGES

logger = logging.getLogger(__name__)


@dataclass
class StoredMessage:
    message_id: int
    user_id: int
    username: str          # @handle if available, else first_name
    display_name: str      # first_name (+ last_name if present)
    text: str
    timestamp: datetime
    reply_to_id: Optional[int] = None


class GroupContext:
    """
    Maintains a rolling window of messages for one group chat.
    Thread-safe via asyncio — all access is from the PTB event loop.
    """

    def __init__(self, chat_id: int, chat_title: str, max_messages: int = 50):
        self.chat_id = chat_id
        self.chat_title = chat_title
        self.messages: deque[StoredMessage] = deque(maxlen=max_messages)

    # ── Ingestion ────────────────────────────────────────────

    def add(self, telegram_message) -> None:
        """Store a Telegram Message object."""
        user = telegram_message.from_user
        if not user:
            return

        username = user.username or user.first_name
        display_name = user.first_name
        if user.last_name:
            display_name += f" {user.last_name}"

        text = telegram_message.text or telegram_message.caption or ""
        if not text.strip():
            return  # skip stickers, photos without caption, etc.

        stored = StoredMessage(
            message_id=telegram_message.message_id,
            user_id=user.id,
            username=username,
            display_name=display_name,
            text=text,
            timestamp=telegram_message.date,
            reply_to_id=telegram_message.reply_to_message.message_id
            if telegram_message.reply_to_message
            else None,
        )
        self.messages.append(stored)
        logger.debug("Stored [%s]: %s", username, text[:60])

    # ── Retrieval ─────────────────────────────────────────────

    def get_transcript(self, limit: int = 20) -> str:
        """
        Returns the last `limit` messages as a readable transcript.

        Format:
            [ansh]: we need DJs for this weekend
            [yash]: yeah affordable ones
            [sourabh]: under 4000 aed
        """
        recent = list(self.messages)[-limit:]
        if not recent:
            return "(no prior conversation)"

        lines = []
        for msg in recent:
            lines.append(f"[{msg.username}]: {msg.text}")
        return "\n".join(lines)

    def add_bot_response(self, text: str) -> None:
        """Store ARIA's own reply so it appears in future transcripts."""
        stored = StoredMessage(
            message_id=-1,
            user_id=0,
            username="ARIA",
            display_name="ARIA",
            text=text[:500],  # cap length to keep context sane
            timestamp=datetime.now(),
        )
        self.messages.append(stored)

    def get_invoker(self, telegram_message) -> dict:
        """
        Returns structured info about the user who invoked ARIA.
        """
        user = telegram_message.from_user
        return {
            "user_id": user.id,
            "username": user.username or user.first_name,
            "display_name": user.first_name + (f" {user.last_name}" if user.last_name else ""),
        }

    def get_last_bot_message_id(self, bot_id: int) -> Optional[int]:
        """Find the most recent message sent by the bot (for reply detection)."""
        for msg in reversed(self.messages):
            if msg.user_id == bot_id:
                return msg.message_id
        return None

    def clear(self) -> None:
        self.messages.clear()

    # ── Stats ─────────────────────────────────────────────────

    def size(self) -> int:
        return len(self.messages)

    def active_users(self) -> list[str]:
        seen = {}
        for msg in self.messages:
            seen[msg.user_id] = msg.username
        return list(seen.values())


# ── Registry ──────────────────────────────────────────────────
# One GroupContext per chat, stored here and in PTB bot_data

_contexts: dict[int, GroupContext] = {}


def get_or_create(chat_id: int, chat_title: str = "") -> GroupContext:
    if chat_id not in _contexts:
        _contexts[chat_id] = GroupContext(chat_id, chat_title, max_messages=GROUP_CONTEXT_MAX_MESSAGES)
        logger.info("Created GroupContext for chat %d (%s)", chat_id, chat_title)
    return _contexts[chat_id]


def get(chat_id: int) -> Optional[GroupContext]:
    return _contexts.get(chat_id)
