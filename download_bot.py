from bot_config import TOKEN
import os, asyncio, aiohttp, logging, time, urllib.parse
from collections import deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)

MAX_FILE_SIZE = 45 * 1024 * 1024
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لینک دانلود بفرست")

def init_chat(context):
    context.chat_data.setdefault("queue", deque())
    context.chat_data.setdefault("downloading", False)
    context.chat_data.setdefault("cancel", False)

def get_filename(url):
    name = urllib.parse.unquote(url.split("/")[-1].split("?")[0])
    return name or f"file_{int(time.time())}"

async def download(url, path, msg, context):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            done = 0
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(1024 * 64):
                    if context.chat_data["cancel"]:
                        raise asyncio.CancelledError
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        p = done * 100 / total
                        if int(p) % 10 == 0:
                            try:
                                await msg.edit_text(f"⬇️ {p:.0f}%")
                            except:
                                pass

async def process_queue(chat_id, context):
    if context.chat_data["downloading"]:
        return

    context.chat_data["downloading"] = True

    while context.chat_data["queue"]:
        url = context.chat_data["queue"].popleft()
        context.chat_data["cancel"] = False

        filename = get_filename(url)
        filepath = os.path.join(DOWNLOAD_DIR, filename)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("لغو ❌", callback_data="cancel")]
        ])

        msg = await context.bot.send_message(
            chat_id, "⏳ شروع دانلود...", reply_markup=keyboard
        )

        try:
            await download(url, filepath, msg, context)

            size = os.path.getsize(filepath)
            if size <= MAX_FILE_SIZE:
                await context.bot.send_document(chat_id, open(filepath, "rb"))
            else:
                await context.bot.send_message(
                    chat_id, "❌ فایل بزرگ‌تر از حد مجاز است"
                )

            await msg.delete()

        except asyncio.CancelledError:
            await msg.edit_text("❌ لغو شد")

        finally:
            if os.path.exists(filepath):
                os.remove(filepath)

    context.chat_data["downloading"] = False

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_chat(context)
    url = update.message.text.strip()

    if not url.startswith("http"):
        return await update.message.reply_text("لینک نامعتبر")

    context.chat_data["queue"].append(url)
    await update.message.reply_text("➕ اضافه شد به صف")

    asyncio.create_task(process_queue(update.effective_chat.id, context))

async def cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["cancel"] = True
    await update.callback_query.answer("لغو شد")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern="cancel"))
    app.run_polling()

if __name__ == "__main__":
    main()
