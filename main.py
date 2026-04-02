"""
ARIA Telegram Bot — Python entry point.

Startup sequence:
  1. Load Monday.com board schemas (columns, groups)
  2. Register Telegram handlers
  3. Start polling or webhook
"""

import asyncio
import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import TELEGRAM_BOT_TOKEN
from monday.schema_loader import load_all as load_monday_schemas
from handlers.group_handler import handle_group_message
from handlers.command_handler import handle_start, handle_clear, handle_status

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def global_error_handler(update: object, context) -> None:
    """Catch all unhandled exceptions — prevents application from crashing."""
    logger.error("Unhandled exception in update handler", exc_info=context.error)


async def post_init(application: Application) -> None:
    """Called once after the Application is initialized."""
    logger.info("Loading Monday.com board schemas…")
    await load_monday_schemas()
    logger.info("Board schemas loaded. ARIA is ready.")


def build_application() -> Application:
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_error_handler(global_error_handler)

    # Commands
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("clear", handle_clear))
    app.add_handler(CommandHandler("status", handle_status))

    # All text messages in groups and supergroups
    app.add_handler(
        MessageHandler(
            filters.TEXT & (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP),
            handle_group_message,
        )
    )

    # Private chat messages (DMs) — also handled by group_handler logic
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE,
            handle_private_message,
        )
    )

    return app


async def _safe_reply(message, text: str) -> None:
    """Reply with Markdown, fall back to plain text if parse fails."""
    try:
        await message.reply_text(text, parse_mode="Markdown")
    except Exception:
        await message.reply_text(text)


async def handle_private_message(update: Update, context) -> None:
    """For private DMs, use simpler single-turn flow."""
    from core import aria_chain
    from core import context_builder
    from core import confirmation as conf_store
    from core import response_writer
    from monday import client as monday_client
    from monday import result_formatter
    from telegram.constants import ChatAction

    message = update.effective_message
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id
    text = message.text or ""

    # Check pending write
    pending = conf_store.get(chat_id, user_id)
    if pending:
        if conf_store.is_confirmation(text):
            conf_store.clear(chat_id, user_id)
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            queries = monday_client.inject_all_board_ids(pending.queries)
            if pending.entities.person_name:
                from config import BOARD_IDS
                board_id = BOARD_IDS.get(pending.entities.board or "sales", BOARD_IDS["sales"])
                queries = await monday_client.resolve_item_id_placeholder(
                    queries, board_id, pending.entities.person_name
                )
            results = await monday_client.execute_queries(queries)
            reply_text = result_formatter.format_results(results, pending)
            await _safe_reply(message, reply_text)
            return
        elif conf_store.is_cancellation(text):
            conf_store.clear(chat_id, user_id)
            await message.reply_text("Cancelled.")
            return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    username = user.username or user.first_name or str(user_id)
    context_block = context_builder.build_private(text, username)
    response = await aria_chain.invoke(context_block)

    if response.awaiting_confirmation:
        conf_store.save(chat_id, user_id, response)
        await _safe_reply(message, response.message or "Confirm this action? Reply *yes* or *no*.")
        return

    if not response.needs_data or not response.queries:
        await _safe_reply(message, response.message or "Got it.")
        return

    queries = monday_client.inject_all_board_ids(response.queries)
    if response.action_type == "write" and response.entities.person_name:
        from config import BOARD_IDS
        board_id = BOARD_IDS.get(response.entities.board or "sales", BOARD_IDS["sales"])
        queries = await monday_client.resolve_item_id_placeholder(
            queries, board_id, response.entities.person_name
        )
    results = await monday_client.execute_queries(queries)
    raw_text = result_formatter.format_results(results, response)
    reply_text = await response_writer.rewrite(raw_text, text, response.intent)
    await _safe_reply(message, reply_text)


def main() -> None:
    webhook_url = os.environ.get("WEBHOOK_URL")
    port = int(os.environ.get("PORT", 8443))

    app = build_application()

    if webhook_url:
        logger.info("Starting in webhook mode on port %d", port)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=f"{webhook_url}/{TELEGRAM_BOT_TOKEN}",
        )
    else:
        logger.info("Starting in polling mode")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
