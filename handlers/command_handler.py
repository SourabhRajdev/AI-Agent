"""
Telegram command handlers: /start, /clear, /status
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from core import group_context as gc_store
from core import confirmation

logger = logging.getLogger(__name__)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message."""
    chat = update.effective_chat
    is_group = chat.type in ("group", "supergroup")

    if is_group:
        text = (
            "*ARIA is online.*\n\n"
            "Mention me with @{username} to search, update, or add records "
            "across your Sales, Artists, and Staff boards on Monday.com.\n\n"
            "_Example: @{username} show me available DJs under 3000 AED_"
        ).format(username=context.bot.username)
    else:
        text = (
            "Hey! I'm ARIA — your AI chief of staff for Denicx Entertainment.\n\n"
            "I can search, update, and manage your Monday.com boards. "
            "Just tell me what you need."
        )

    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the group context buffer and any pending write for this user."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type in ("group", "supergroup"):
        gc = gc_store.get(chat.id)
        if gc:
            gc.clear()
        confirmation.clear(chat.id, user.id)
        await update.effective_message.reply_text("Context cleared.")
    else:
        await update.effective_message.reply_text("Nothing to clear here.")


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show buffer stats for the current group."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("Status is only available in group chats.")
        return

    gc = gc_store.get(chat.id)
    msg_count = len(gc.messages) if gc else 0
    pending = confirmation.get(chat.id, user.id)

    lines = [
        f"*ARIA Status — {chat.title}*",
        f"Messages buffered: {msg_count}",
        f"Pending write: {'yes (awaiting confirmation)' if pending else 'none'}",
    ]
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")
