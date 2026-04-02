"""
Builds the GROUP CONTEXT block injected into ARIA's human message.

Format injected into model:
    [GROUP CONTEXT — Last 20 messages, Denicx Ops]
    [ansh]: we need DJs for this weekend
    [yash]: yeah affordable ones
    [sourabh]: under 4000 aed
    [ansh]: @aria find them

    INVOKING USER: ansh
    DIRECT REQUEST: find them
"""

import re
from core.group_context import GroupContext
from config import GROUP_CONTEXT_WINDOW


def build(
    group_context: GroupContext,
    invoking_message_text: str,
    invoker: dict,
    bot_username: str,
) -> str:
    """
    Build the full context block for ARIA.

    Args:
        group_context:         The GroupContext for this chat
        invoking_message_text: The raw text of the @mention message
        invoker:               {user_id, username, display_name}
        bot_username:          The bot's Telegram username (without @)

    Returns:
        A formatted string ready to be sent as the HumanMessage content.
    """
    transcript = group_context.get_transcript(limit=GROUP_CONTEXT_WINDOW)
    direct_request = _strip_mention(invoking_message_text, bot_username)

    return (
        f"[GROUP CONTEXT — Last {GROUP_CONTEXT_WINDOW} messages, {group_context.chat_title}]\n"
        f"{transcript}\n\n"
        f"INVOKING USER: {invoker['username']}\n"
        f"DIRECT REQUEST: {direct_request}"
    )


def build_private(message_text: str, username: str) -> str:
    """
    For private (1:1) chats — no group context, just the message.
    """
    return (
        f"USER: {username}\n"
        f"MESSAGE: {message_text}"
    )


def _strip_mention(text: str, bot_username: str) -> str:
    """
    Remove the @botusername from the message to get the actual request.
    '@aria find available DJs' → 'find available DJs'
    """
    cleaned = re.sub(rf"@{re.escape(bot_username)}\s*", "", text, flags=re.IGNORECASE).strip()
    return cleaned or text  # fallback to original if nothing left after strip
