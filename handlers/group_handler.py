"""
Core group message handler.

Flow for every group message:
  1. Store message in GroupContext (always — silent for non-mentions)
  2. Detect @mention or reply-to-bot invocation
  3. If pending write for this user → check confirmation/cancellation
  4. Build GROUP CONTEXT block → invoke ARIA chain
  5. If awaiting_confirmation → save pending write, ask user
  6. If read/write → execute Monday.com queries, format, reply
  7. If chitchat/greeting → reply directly with message field
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction

from core import group_context as gc_store
from core import confirmation
from core import context_builder
from core import aria_chain
from core import response_writer
from monday import client as monday_client
from monday import result_formatter
from monday.schema_loader import get_column_map
from monday.write_validator import validate_write
from config import ALLOWED_CHAT_IDS

logger = logging.getLogger(__name__)


async def _send(context, chat_id: int, text: str, reply_to_message_id: int = None) -> None:
    """Send with Markdown, fall back to plain text if parse fails."""
    kwargs = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
    try:
        await context.bot.send_message(**kwargs)
    except Exception:
        kwargs.pop("parse_mode")
        await context.bot.send_message(**kwargs)


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """MessageHandler callback for all group messages."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user:
        return

    chat_id = chat.id
    user_id = user.id

    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        logger.warning(f"Unauthorized chat_id: {chat_id}. Ignoring message.")
        return

    text = message.text or message.caption or ""

    # ── 1. Always store message in group context ───────────────
    group_ctx = gc_store.get_or_create(chat_id, chat.title or str(chat_id))
    group_ctx.add(message)

    # ── 2. Detect invocation ───────────────────────────────────
    bot_username = context.bot.username  # e.g. "ariacrm_bot"

    # In private chats, all messages are an invocation.
    is_private = chat.type == "private"
    invoked = True if is_private else _is_invoked(message, bot_username)

    if not invoked:
        return  # passive — store only, no response

    # ── 3. Pending write check ─────────────────────────────────
    pending = confirmation.get(chat_id, user_id)
    if pending:
        if confirmation.is_confirmation(text):
            confirmation.clear(chat_id, user_id)
            await _execute_pending_write(pending, chat_id, message.message_id, context)
            return
        elif confirmation.is_cancellation(text):
            confirmation.clear(chat_id, user_id)
            await _send(context, chat_id, "Got it — cancelled.", message.message_id)
            return
        # Not a clear yes/no → fall through to re-process normally

    # ── 4. Typing indicator + chain invocation ─────────────────
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    invoker = group_ctx.get_invoker(message)
    direct_request = context_builder.strip_mention(text, bot_username)

    if is_private:
        context_block = context_builder.build_private(text, invoker["username"])
    else:
        context_block = context_builder.build(
            group_context=group_ctx,
            invoking_message_text=text,
            invoker=invoker,
            bot_username=bot_username,
        )

    response = await aria_chain.invoke(context_block)

    # ── 5. Awaiting confirmation ───────────────────────────────
    if response.awaiting_confirmation:
        confirmation.save(chat_id, user_id, response)
        confirm_msg = response.message or _build_confirm_prompt(response)
        await _send(context, chat_id, confirm_msg, message.message_id)
        group_ctx.add_bot_response(confirm_msg)
        return

    # ── 6. No data needed (greeting/chitchat/clarify) ─────────
    if not response.needs_data:
        reply = response.message or "Got it."
        await _send(context, chat_id, reply, message.message_id)
        group_ctx.add_bot_response(reply)
        return

    # ── 7. Execute Monday.com queries ─────────────────────────
    if not response.queries:
        reply = response.message or "Got it."
        await _send(context, chat_id, reply, message.message_id)
        group_ctx.add_bot_response(reply)
        return
    await _execute_and_reply(response, direct_request, chat_id, message.message_id, context, group_ctx)


async def _execute_and_reply(
    response,
    original_request: str,
    chat_id: int,
    reply_to_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    group_ctx=None,
) -> None:
    """Run Monday queries, rewrite results as natural language, send."""

    # ── Pre-execution write validation ─────────────────────────
    if response.action_type == "write" and response.entities.values_to_set:
        val_errors = validate_write(response.entities.values_to_set, response.entities.board or "")
        if val_errors:
            error_msg = "Can't do that:\n" + "\n".join(f"• {e}" for e in val_errors)
            await _send(context, chat_id, error_msg, reply_to_id)
            if group_ctx:
                group_ctx.add_bot_response(error_msg)
            return

    queries = monday_client.inject_all_board_ids(response.queries)

    # Push numeric filters (pricing, experience, etc.) to Monday server-side
    if response.action_type == "read" and response.entities.filters:
        column_map = get_column_map(response.entities.board or "")
        queries = monday_client.inject_server_filters(queries, response.entities.filters, column_map)

    # 2-step write resolution for ITEM_ID_PLACEHOLDER
    if response.action_type == "write" and response.entities.person_name:
        board_id = _board_id_from_response(response)
        queries = await monday_client.resolve_item_id_placeholder(
            queries, board_id, response.entities.person_name
        )

    results = await monday_client.execute_queries(queries)

    # ── VERIFY ─────────────────────────────────────────────────
    if response.action_type == "write":
        verify_results = await monday_client.verify_write_result(results)
        raw_text = (
            result_formatter.format_write_verification(verify_results, response.intent)
            if verify_results
            else result_formatter.format_results(results, response)
        )
    else:
        raw_text = result_formatter.format_results(results, response)

    reply = await response_writer.rewrite(raw_text, original_request, response.intent)
    await _send(context, chat_id, reply, reply_to_id)
    if group_ctx:
        group_ctx.add_bot_response(reply)


async def _execute_pending_write(
    pending_response,
    chat_id: int,
    reply_to_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Execute a previously confirmed write operation."""
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    queries = monday_client.inject_all_board_ids(pending_response.queries)

    if pending_response.entities.person_name:
        board_id = _board_id_from_response(pending_response)
        queries = await monday_client.resolve_item_id_placeholder(
            queries, board_id, pending_response.entities.person_name
        )

    results = await monday_client.execute_queries(queries)

    # ── VERIFY ─────────────────────────────────────────────────
    verify_results = await monday_client.verify_write_result(results)
    text = (
        result_formatter.format_write_verification(verify_results, pending_response.intent)
        if verify_results
        else result_formatter.format_results(results, pending_response)
    )

    await _send(context, chat_id, text, reply_to_id)
    ctx = gc_store.get(chat_id)
    if ctx:
        ctx.add_bot_response(text)


# ── Helpers ───────────────────────────────────────────────────

def _is_invoked(message, bot_username: str) -> bool:
    """
    Returns True if the bot was @mentioned or the message is a reply to the bot.
    """
    # Check message entities for @mention
    entities = message.entities or message.caption_entities or []
    for entity in entities:
        if entity.type.name == "MENTION":
            mention_text = message.text[entity.offset : entity.offset + entity.length]
            if mention_text.lstrip("@").lower() == bot_username.lower():
                return True

    # Check if it's a reply to the bot's own message
    if message.reply_to_message:
        replied_user = message.reply_to_message.from_user
        if replied_user and replied_user.username and replied_user.username.lower() == bot_username.lower():
            return True

    return False


def _build_confirm_prompt(response) -> str:
    """Build a fallback confirmation message if model didn't provide one."""
    intent = response.intent
    entities = response.entities
    name = entities.person_name or "this record"
    if intent == "create_item":
        return f"Create a new entry for *{name}*? Reply *yes* to confirm or *no* to cancel."
    elif intent == "update_item":
        return f"Update *{name}*? Reply *yes* to confirm or *no* to cancel."
    elif intent == "delete_item":
        return f"Delete *{name}*? Reply *yes* to confirm or *no* to cancel."
    return "Confirm this action? Reply *yes* or *no*."


def _board_id_from_response(response) -> int:
    from config import BOARD_IDS
    board = response.entities.board or "sales"
    return BOARD_IDS.get(board, BOARD_IDS["sales"])
