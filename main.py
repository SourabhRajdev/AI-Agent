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

    # All text messages (Groups, Supergroups, Private)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_group_message,
        )
    )

    return app


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
