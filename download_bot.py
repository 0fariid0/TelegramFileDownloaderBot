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

# --- تنظیمات اختصاصی ---
try:
    from bot_config import TOKEN
except ImportError:
    TOKEN = "YOUR_BOT_TOKEN_HERE"

ADMIN_ID = 450281442  # آیدی عددی خود را اینجا وارد کنید
LOG_FILE = "bot_log.txt"
HISTORY_FILE = "download_history.txt"
DOWNLOAD_DIR = "downloads"
CHUNK_SIZE = 45 * 1024 * 1024  # کمی کمتر از 50 مگ برای امنیت بیشتر
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v')

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- توابع کمکی ---

def human_readable_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0: break
        size /= 1024.0
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
            InlineKeyboardButton("⏸ توقف", callback_data="dl_pause"),
            InlineKeyboardButton("❌ لغو", callback_data="dl_cancel")
        ]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ ادامه", callback_data="dl_resume"),
        InlineKeyboardButton("❌ لغو", callback_data="dl_cancel")
    ]])

def split_file(file_path):
    file_list = []
    part_num = 1
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk: break
            part_name = f"{file_path}.part{part_num}"
            with open(part_name, 'wb') as p: p.write(chunk)
            file_list.append(part_name)
            part_num += 1
    return file_list

# --- هسته دانلودر ---

async def download_task(chat_id, context, url, filename):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    
    downloaded_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    headers = {"Range": f"bytes={downloaded_size}-"}
    
    start_time = time.time()
    last_ui_update = time.time()

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416:
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
                            if now - last_ui_update > 3.0: # افزایش به 3 ثانیه برای جلوگیری از Flood
                                elapsed = now - start_time
                                speed = (downloaded_size - (os.path.getsize(file_path) if mode=="ab" else 0)) / elapsed if elapsed > 0 else 0
                                percent = (downloaded_size / total_size * 100) if total_size > 0 else 0
                                eta = (total_size - downloaded_size) / speed if speed > 0 else 0
                                
                                bar = get_progress_bar(percent)
                                text = (
                                    f"🚀 **در حال دانلود...**\n\n"
                                    f"📦 **فایل:** `{filename}`\n"
                                    f"📊 **پیشرفت:** {percent:.1f}%\n"
                                    f"{bar}\n\n"
                                    f"⚡ **سرعت:** {human_readable_size(speed)}/s\n"
                                    f"📥 **حجم:** {human_readable_size(downloaded_size)} / {human_readable_size(total_size)}\n"
                                    f"⏱ **زمان باقیمانده:** {int(eta)} ثانیه"
                                )
                                try:
                                    await context.bot.edit_message_text(text, chat_id, chat_data['msg_id'], 
                                                                        reply_markup=get_download_keyboard(), parse_mode='Markdown')
                                except: pass
                                last_ui_update = now
                else: return f"خطای سرور: {response.status_code}"
        return "completed"
    except Exception as e: return str(e)

# --- مدیریت ارسال و آپلود ---

async def process_result(chat_id, context, result):
    chat_data = context.chat_data
    if 'current_filename' not in chat_data: return
    
    filename = chat_data['current_filename']
    url = chat_data['current_url']
    file_path = os.path.join(DOWNLOAD_DIR, filename)

    if result == "completed":
        await context.bot.edit_message_text("✅ دانلود تکمیل شد. در حال ارسال... 📤", chat_id, chat_data['msg_id'])
        save_to_history(filename, url)
        
        try:
            if os.path.getsize(file_path) > CHUNK_SIZE:
                parts = split_file(file_path)
                for i, p in enumerate(parts):
                    with open(p, 'rb') as f:
                        await context.bot.send_document(chat_id, document=f, caption=f"Part {i+1} | {filename}")
                    os.remove(p)
            else:
                is_video = filename.lower().endswith(VIDEO_EXTENSIONS)
                with open(file_path, 'rb') as f:
                    if is_video:
                        await context.bot.send_video(chat_id, video=f, caption=f"✅ {filename}", supports_streaming=True)
                    else:
                        await context.bot.send_document(chat_id, document=f, caption=f"✅ {filename}")
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ خطا در ارسال: {e}")
        
        if os.path.exists(file_path): os.remove(file_path)
        try: await context.bot.delete_message(chat_id, chat_data['msg_id'])
        except: pass
        
        chat_data['is_working'] = False
        await start_next_download(chat_id, context)

    elif result == "paused":
        await context.bot.edit_message_text(f"⏸ **دانلود متوقف شد.**\n`{filename}`", chat_id, chat_data['msg_id'], 
                                            reply_markup=get_download_keyboard("paused"), parse_mode='Markdown')
    elif result == "cancelled":
        if os.path.exists(file_path): os.remove(file_path)
        await context.bot.edit_message_text("❌ دانلود لغو و فایل حذف شد.", chat_id, chat_data['msg_id'])
        chat_data['is_working'] = False
        await start_next_download(chat_id, context)

# --- هندلرهای دستورات و پیام‌ها ---

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **به ربات دانلودر خوش آمدید!**\n\n🔗 لینک مستقیم را بفرستید تا دانلود و آپلود شود.",
        parse_mode='Markdown'
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    keyboard = [
        [InlineKeyboardButton("📊 آخرین دانلودها", callback_data="adm_history"),
         InlineKeyboardButton("📜 وضعیت سیستم", callback_data="adm_logs")],
        [InlineKeyboardButton("🧹 پاکسازی تاریخچه", callback_data="adm_clear"),
         InlineKeyboardButton("🔄 رفرش", callback_data="adm_main")]
    ]
    text = "🛠 **پنل مدیریت**"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.startswith("http"):
        if 'queue' not in context.chat_data: context.chat_data['queue'] = deque()
        context.chat_data['queue'].append(text)
        await update.message.reply_text(f"✅ در صف قرار گرفت. (موقعیت: {len(context.chat_data['queue'])})")
        if not context.chat_data.get('is_working'):
            await start_next_download(update.effective_chat.id, context)

async def start_next_download(chat_id, context):
    if not context.chat_data.get('queue'): 
        context.chat_data['is_working'] = False
        return
    
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

async def global_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id

    # مدیریت دکمه‌های دانلود
    if data.startswith("dl_"):
        if data == "dl_pause":
            context.chat_data['status'] = 'paused'
            await query.answer("⏸ توقف")
        elif data == "dl_resume":
            context.chat_data['status'] = 'downloading'
            await query.answer("▶️ ادامه")
            # اجرای دانلود در بک‌گراند بدون بلاک کردن هندلر
            asyncio.create_task(resume_download_wrapper(update.effective_chat.id, context))
        elif data == "dl_cancel":
            context.chat_data['status'] = 'cancelled'
            await query.answer("❌ لغو")

    # مدیریت دکمه‌های ادمین
    elif data.startswith("adm_") and user_id == ADMIN_ID:
        if data == "adm_main":
            await admin_panel(update, context)
        elif data == "adm_history":
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    history = "".join(f.readlines()[-5:])
                    await query.edit_message_text(f"📈 **تاریخچه:**\n\n{history or 'خالی'}", 
                                               reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_main")]]))
        elif data == "adm_clear":
            if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
            await query.answer("🧹 تاریخچه پاکسازی شد")
            await admin_panel(update, context)

async def resume_download_wrapper(chat_id, context):
    res = await download_task(chat_id, context, context.chat_data['current_url'], context.chat_data['current_filename'])
    await process_result(chat_id, context, res)

# --- اجرا ---

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(global_callback_handler))
    
    print("🚀 Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
