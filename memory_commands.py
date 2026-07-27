"""
/memories and /forget — a direct window into what's stored, for when the
automatic extraction misses something or a user wants control over it.
"""
from telegram import Update
from telegram.ext import ContextTypes

import database as db


async def memories_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rows = await db.list_memories(user.id)
    if not rows:
        await update.message.reply_text("i've got nothing on you yet. tell me stuff.")
        return
    lines = [f"{r['memory_id']}. {r['content']}" for r in rows]
    await update.message.reply_text(
        "here's what i've got:\n" + "\n".join(lines) + "\n\nforget one with /forget <number>"
    )


async def forget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("usage: /forget <number> — check /memories for the number")
        return
    memory_id = int(context.args[0])
    ok = await db.delete_memory(user.id, memory_id)
    await update.message.reply_text("gone." if ok else "couldn't find that one, check /memories")


async def remember_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("usage: /remember <something about you>")
        return
    await db.add_memory(user.id, text)
    await update.message.reply_text("got it, filed away.")
