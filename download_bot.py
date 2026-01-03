from bot_config import TOKEN
import os, asyncio, aiohttp, logging, time, urllib.parse, math, subprocess
from collections import deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)

# ---------------- CONFIG ----------------
MAX_FILE_SIZE = 45 * 1024 * 1024
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- BASIC ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 لینک دانلود بفرست\n"
        "✔ پشتیبانی از فایل بزرگ\n"
        "✔ ادامه دانلود\n"
        "✔ لغو عملیات"
    )

def init_chat(context):
    context.chat_data.setdefault("queue", deque())
    context.chat_data.setdefault("downloading", False)
    context.chat_data.setdefault("cancel", False)

# ---------------- FILE UTILS ----------------
def split_file(path):
    parts = []
    with open(path, "rb") as f:
        i = 1
        while chunk := f.read(MAX_FILE_SIZE):
            p = f"{path}.part{i:02d}"
            with open(p, "wb") as pf:
                pf.write(chunk)
            parts.append(p)
            i += 1
    return parts

def compress_video(inp, out):
    subprocess.run([
        "ffmpeg", "-y", "-i", inp,
        "-vcodec", "libx264", "-preset", "fast",
        "-b:v", "800k", "-acodec", "aac", out
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ---------------- DOWNLOAD ----------------
async def download_with_resume(url, path, msg, context):
    downloaded = os.path.getsize(path) if os.path.exists(path) else 0
    headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0)) + downloaded
            with open(path, "ab") as f:
                async for chunk in r.content.iter_chunked(1024 * 64):
                    if context.chat_data["cancel"]:
                        raise asyncio.CancelledError
                    f.write(chunk)
                    downloaded += len(chunk)
                    percent = downloaded * 100 / total
                    if int(percent) % 5 == 0:
                        try:
                            await msg.edit_text(f"⬇️ دانلود {percent:.1f}%")
                        except:
                            pass

# ---------------- PROCESS QUEUE ----------------
async def process_queue(chat_id, context):
    if context.chat_data["downloading"]:
        return

    context.chat_data["downloading"] = True

    while context.chat_data["queue"]:
        url = context.chat_data["queue"].popleft()
        context.chat_data["cancel"] = False

        msg = await context.bot.send_message(chat_id, "⏳ شروع دانلود...")
        filename = os.path.join(DOWNLOAD_DIR, f"{chat_id}_{int(time.time())}")

        try:
            await download_with_resume(url, filename, msg, context)

            if url.lower().endswith(".mp4"):
                await msg.edit_text("🎞 فشرده‌سازی ویدیو...")
                compressed = filename + "_compressed.mp4"
                await asyncio.get_event_loop().run_in_executor(
                    None, compress_video, filename, compressed
                )
                os.remove(filename)
                filename = compressed

            await msg.edit_text("⬆️ در حال آپلود...")

            size = os.path.getsize(filename)
            if size <= MAX_FILE_SIZE:
                await context.bot.send_document(chat_id, open(filename, "rb"))
            else:
                parts = split_file(filename)
                for i, p in enumerate(parts, 1):
                    await context.bot.send_document(
                        chat_id,
                        document=open(p, "rb"),
                        caption=f"📦 پارت {i}/{len(parts)}"
                    )
                    os.remove(p)

            await msg.delete()

        except asyncio.CancelledError:
            await msg.edit_text("❌ دانلود لغو شد")
        except Exception as e:
            await msg.edit_text(f"❌ خطا: {e}")
        finally:
            if os.path.exists(filename):
                os.remove(filename)

    context.chat_data["downloading"] = False

# ---------------- HANDLERS ----------------
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_chat(context)
    url = update.message.text.strip()

    if not url.startswith("http"):
        return await update.message.reply_text("❌ لینک نامعتبر")

    context.chat_data["queue"].append(url)
    await update.message.reply_text("✅ به صف اضافه شد")

    asyncio.create_task(process_queue(update.effective_chat.id, context))

async def cancel_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["cancel"] = True
    await update.callback_query.answer("لغو شد")

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(cancel_download, pattern="cancel"))

    app.run_polling()

if __name__ == "__main__":
    main()
