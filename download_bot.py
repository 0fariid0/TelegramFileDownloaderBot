import os
import time
import asyncio
import httpx
import logging
import urllib.parse
from datetime import datetime
from collections import deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, CallbackQueryHandler
)

# وارد کردن توکن از فایل تنظیمات شما
try:
    from bot_config import TOKEN
except ImportError:
    TOKEN = "YOUR_BOT_TOKEN_HERE"

# --- تنظیمات ---
ADMIN_ID = 12345678  # 👈 آیدی تلگرام خودت
LOG_FILE = "bot_log.txt"
HISTORY_FILE = "download_history.txt"
DOWNLOAD_DIR = "downloads"
CHUNK_SIZE = 48 * 1024 * 1024  # 48MB
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v')

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- کمکی ---
def human_readable_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: break
        size /= 1024
    return f"{size:.2f} {unit}"

def get_progress_bar(percent):
    done = int(percent / 10)
    return "🔹" * done + "🔸" * (10 - done)

def save_to_history(filename, url):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"📅 {now} | 📁 {filename} | 🔗 {url}\n")

def get_download_keyboard(status="downloading"):
    if status == "downloading":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("⏸ توقف", callback_data="pause"),
            InlineKeyboardButton("❌ لغو", callback_data="cancel")
        ]])
    else:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("▶️ ادامه", callback_data="resume"),
            InlineKeyboardButton("❌ لغو", callback_data="cancel")
        ]])

def split_file(file_path):
    parts = []
    part_num = 1
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk: break
            part_name = f"{file_path}.part{part_num}"
            with open(part_name, 'wb') as p: p.write(chunk)
            parts.append(part_name)
            part_num += 1
    return parts

# --- دانلود ---
async def download_task(chat_id, context, url, filename):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    downloaded_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    headers = {"User-Agent": "Mozilla/5.0", "Range": f"bytes={downloaded_size}-"}
    
    start_time = time.time()
    last_update = time.time()
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416:  # قبلاً دانلود شده
                    total_size = downloaded_size
                elif response.status_code in (200, 206):
                    total_size = int(response.headers.get("Content-Length", 0)) + downloaded_size
                    mode = "ab" if downloaded_size > 0 else "wb"
                    with open(file_path, mode) as f:
                        async for chunk in response.aiter_bytes(chunk_size=32768):
                            if chat_data.get('status') == 'paused': return "paused"
                            if chat_data.get('status') == 'cancelled': return "cancelled"
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            now = time.time()
                            if now - last_update > 2:
                                elapsed = now - start_time
                                speed = (downloaded_size - (0 if mode=="wb" else downloaded_size)) / elapsed if elapsed > 0 else 0
                                percent = (downloaded_size / total_size * 100) if total_size else 0
                                eta = (total_size - downloaded_size) / speed if speed > 0 else 0
                                bar = get_progress_bar(percent)
                                text = (
                                    f"🚀 **دانلود...**\n\n"
                                    f"📦 `{filename}`\n"
                                    f"📊 {percent:.1f}% {bar}\n"
                                    f"⚡ `{human_readable_size(speed)}/s`\n"
                                    f"📥 `{human_readable_size(downloaded_size)}/{human_readable_size(total_size)}`\n"
                                    f"⏱ {int(eta)} ثانیه باقی مانده"
                                )
                                try:
                                    await context.bot.edit_message_text(text, chat_id, chat_data['msg_id'], 
                                                                        reply_markup=get_download_keyboard(), parse_mode='Markdown')
                                except: pass
                                last_update = now
                else:
                    return f"خطا: {response.status_code}"
        return "completed"
    except Exception as e:
        return str(e)

# --- پردازش نتیجه ---
async def process_result(chat_id, context, result):
    chat_data = context.chat_data
    filename = chat_data.get('current_filename')
    url = chat_data.get('current_url')
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    
    if result == "completed":
        await context.bot.edit_message_text("✅ دانلود تکمیل شد. در حال ارسال...", chat_id, chat_data['msg_id'])
        save_to_history(filename, url)
        try:
            is_video = filename.lower().endswith(VIDEO_EXTENSIONS)
            if os.path.getsize(file_path) > CHUNK_SIZE:
                await context.bot.send_message(chat_id, "📦 فایل بزرگ، به صورت پارت ارسال می‌شود...")
                parts = split_file(file_path)
                for i, p in enumerate(parts):
                    with open(p, 'rb') as f:
                        await context.bot.send_document(chat_id, document=f, caption=f"Part {i+1} | `{filename}`")
                    os.remove(p)
            else:
                with open(file_path, 'rb') as f:
                    if is_video:
                        await context.bot.send_video(chat_id, video=f, caption=f"✅ `{filename}`", supports_streaming=True)
                    else:
                        await context.bot.send_document(chat_id, document=f, caption=f"✅ `{filename}`")
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ خطا در ارسال: {e}")
        if os.path.exists(file_path): os.remove(file_path)
        await context.bot.delete_message(chat_id, chat_data['msg_id'])
        chat_data['is_working'] = False
        await start_next_download(chat_id, context)
    
    elif result == "paused":
        await context.bot.edit_message_text(f"⏸ دانلود متوقف شد.\n`{filename}`", chat_id, chat_data['msg_id'],
                                            reply_markup=get_download_keyboard("paused"), parse_mode='Markdown')
    elif result == "cancelled":
        if os.path.exists(file_path): os.remove(file_path)
        await context.bot.edit_message_text("❌ دانلود لغو شد و فایل حذف شد.", chat_id, chat_data['msg_id'])
        chat_data['is_working'] = False
        await start_next_download(chat_id, context)
    else:
        await context.bot.send_message(chat_id, f"❌ خطا: {result}")
        chat_data['is_working'] = False
        await start_next_download(chat_id, context)

# --- شروع دانلود بعدی ---
async def start_next_download(chat_id, context):
    if not context.chat_data.get('queue'): 
        context.chat_data['is_working'] = False
        return
    if context.chat_data.get('is_working'): return
    context.chat_data['is_working'] = True
    url = context.chat_data['queue'].popleft()
    context.chat_data['current_url'] = url
    context.chat_data['status'] = 'downloading'
    
    filename = urllib.parse.unquote(url.split('/')[-1].split('?')[0]) or f"file_{int(time.time())}"
    context.chat_data['current_filename'] = filename
    
    msg = await context.bot.send_message(chat_id, "🔍 در حال اتصال به سرور...")
    context.chat_data['msg_id'] = msg.message_id
    
    result = await download_task(chat_id, context, url, filename)
    await process_result(chat_id, context, result)

# --- هندلرها ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 به ربات دانلودر خوش آمدید!\n"
        "🔗 لینک مستقیم فایل خود را ارسال کنید.\n"
        "🎥 ویدیوها به صورت استریم ارسال می‌شوند.\n"
        "🛠 پنل مدیریت: /admin"
    )
    await update.message.reply_text(text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    if text.startswith("http"):
        if 'queue' not in context.chat_data: context.chat_data['queue'] = deque()
        context.chat_data['queue'].append(text)
        await update.message.reply_text(f"✅ لینک اضافه شد. موقعیت در صف: {len(context.chat_data['queue'])}")
        if not context.chat_data.get('is_working'):
            await start_next_download(update.effective_chat.id, context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "pause":
        context.chat_data['status'] = 'paused'
        await query.answer("⏸ توقف")
    elif data == "resume":
        context.chat_data['status'] = 'downloading'
        await query.answer("▶️ ادامه")
        asyncio.create_task(process_result(update.effective_chat.id, context, 
                                           await download_task(update.effective_chat.id, context, context.chat_data['current_url'], context.chat_data['current_filename'])))
    elif data == "cancel":
        context.chat_data['status'] = 'cancelled'
        await query.answer("❌ لغو شد")

# --- اجرای اصلی ---
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("🚀 ربات فعال شد!")
    app.run_polling()

if __name__ == "__main__":
    main()
