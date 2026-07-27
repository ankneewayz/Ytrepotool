"""
The core of Emina: decides whether to reply in a group, builds context from
memory + recent history, calls the AI, and stores anything worth remembering.
No feature flags, no plugin system — this is the one thing that has to work.
"""
import asyncio
import re

from telegram import Update
from telegram.constants import ChatAction, ChatType
from telegram.ext import ContextTypes

import database as db
from persona import build_system_prompt
from ai_client import generate_reply, extract_facts
from config import BOT_NAME, logger

NAME_RE = re.compile(rf"\b{re.escape(BOT_NAME)}\b", re.IGNORECASE)


def _should_respond(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or message.text is None:
        return False

    # Always respond in DMs.
    if chat.type == ChatType.PRIVATE:
        return True

    # In groups: only on name mention, @-mention, or a reply to Emina's own message.
    if NAME_RE.search(message.text):
        return True

    bot_username = context.bot.username
    if bot_username and f"@{bot_username}".lower() in message.text.lower():
        return True

    if message.reply_to_message and message.reply_to_message.from_user and \
            message.reply_to_message.from_user.id == context.bot.id:
        return True

    return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _should_respond(update, context):
        return

    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    await db.upsert_user(user.id, user.username, user.first_name)

    # Strip a leading/embedded name mention so it's not weirdly echoed back.
    clean_text = NAME_RE.sub("", message.text).strip(" ,:!") or message.text

    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)

    memories_rows = await db.list_memories(user.id)
    memory_strings = [row["content"] for row in memories_rows]
    system_prompt = build_system_prompt(memory_strings)

    history_rows = await db.recent_messages(chat.id, limit=12)
    history = [{"role": r["role"], "content": r["content"]} for r in history_rows]

    reply = await generate_reply(system_prompt, history, f"{user.first_name}: {clean_text}")

    await message.reply_text(reply)

    await db.add_message(chat.id, user.id, "user", f"{user.first_name}: {clean_text}")
    await db.add_message(chat.id, user.id, "assistant", reply)

    # Fire-and-forget: pull out durable facts without blocking the reply.
    asyncio.create_task(_remember_from_message(user.id, clean_text))


async def _remember_from_message(user_id: int, text: str):
    try:
        facts = await extract_facts(text)
        for fact in facts:
            if not await db.memory_exists_like(user_id, fact):
                await db.add_memory(user_id, fact)
    except Exception as e:  # noqa: BLE001 - never let background extraction crash anything
        logger.warning("Memory extraction pipeline error: %s", e)
