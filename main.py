"""
Emina — entry point. Deliberately small: one chat handler, three memory
commands, done. Add more handlers here only once they're actually built.
"""
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

import database as db
from config import TELEGRAM_BOT_TOKEN, BOT_NAME, logger
from chat import handle_message
from memory_commands import memories_cmd, forget_cmd, remember_cmd


async def start_cmd(update: Update, context):
    await update.message.reply_text(
        f"hey, {BOT_NAME} here. talk to me in dm anytime, or in a group just say my name, "
        f"reply to me, or @ me. i remember things — /memories to see what, /forget to clear one."
    )


async def _post_init(app: Application):
    await db.init_db()
    logger.info("%s is up.", BOT_NAME)


def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(_post_init).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("memories", memories_cmd))
    app.add_handler(CommandHandler("forget", forget_cmd))
    app.add_handler(CommandHandler("remember", remember_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
