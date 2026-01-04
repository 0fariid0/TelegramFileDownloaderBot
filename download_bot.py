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
    TOKEN = "YOUR_BOT_TOKEN_HERE" # اگر فایل ندارید اینجا جایگذاری کنید

# --- تنظیمات اختصاصی ---
ADMIN_ID = 12345678  # 👈 آیدی عددی تلگرام خودتان را اینجا وارد کنید
LOG_FILE = "bot_log.txt"
HISTORY_FILE = "download_history.txt"
DOWNLOAD_DIR = "downloads"
CHUNK_SIZE = 48 * 1024 * 1024  # پارت‌های 48 مگابایتی برای تلگرام
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v')

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# تنظیمات لاگ‌گذاری
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
            InlineKeyboardButton("⏸ توقف", callback_data="pause"),
            InlineKeyboardButton("❌ لغو", callback_data="cancel")
        ]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ ادامه", callback_data="resume"),
        InlineKeyboardButton("❌ لغو", callback_data="cancel")
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
    headers = {"User-Agent": "Mozilla/5.0", "Range": f"bytes={downloaded_size}-"}
    
    start_time = time.time()
    last_ui_update = time.time()

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416: # دانلود قبلاً تمام شده
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
                            if now - last_ui_update > 2.0: # هر 2 ثانیه آپدیت رابط کاربری
                                elapsed = now - start_time
                                session_downloaded = downloaded_size - (os.path.getsize(file_path) if mode=="ab" else 0)
                                speed = session_downloaded / elapsed if elapsed > 0 else 0
                                percent = (downloaded_size / total_size * 100) if total_size > 0 else 0
                                eta = (total_size - downloaded_size) / speed if speed > 0 else 0
                                
                                bar = get_progress_bar(percent)
                                text = (
                                    f"🚀 **در حال دانلود با سرعت بالا...**\n\n"
                                    f"📦 **فایل:** `{filename}`\n"
                                    f"📊 **پیشرفت:** `{percent:.1f}%`\n"
                                    f"{bar}\n\n"
                                    f"⚡ **سرعت:** `{human_readable_size(speed)}/s`\n"
                                    f"📥 **حجم:** `{human_readable_size(downloaded_size)} / {human_readable_size(total_size)}`\n"
                                    f"⏱ **زمان باقیمانده:** `{int(eta)} ثانیه`"
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
    filename = chat_data['current_filename']
    url = chat_data['current_url']
    file_path = os.path.join(DOWNLOAD_DIR, filename)

    if result == "completed":
        await context.bot.edit_message_text("✅ دانلود تکمیل شد. در حال ارسال... 📤", chat_id, chat_data['msg_id'])
        save_to_history(filename, url)
        
        try:
            is_video = filename.lower().endswith(VIDEO_EXTENSIONS)
            if os.path.getsize(file_path) > CHUNK_SIZE:
                await context.bot.send_message(chat_id, "📦 فایل بزرگ است، به صورت پارت‌بندی ارسال می‌شود...")
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
        await context.bot.edit_message_text(f"⏸ **دانلود متوقف شد.**\n`{filename}`", chat_id, chat_data['msg_id'], 
                                            reply_markup=get_download_keyboard("paused"), parse_mode='Markdown')
    elif result == "cancelled":
        if os.path.exists(file_path): os.remove(file_path)
        await context.bot.edit_message_text("❌ دانلود لغو و فایل حذف شد.", chat_id, chat_data['msg_id'])
        chat_data['is_working'] = False
        await start_next_download(chat_id, context)

# --- هندلرهای دستورات و پیام‌ها ---

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "👋 **به ربات دانلودر خوش آمدید!**\n\n"
        "🔗 کافیست لینک مستقیم فایل خود را ارسال کنید.\n"
        "🎥 ویدیوها به صورت استریم آپلود می‌شوند.\n"
        "🛠 پنل مدیریت: /admin"
    )
    await update.message.reply_text(welcome, parse_mode='Markdown')

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی اصلی مدیریت با دکمه‌های شیشه‌ای"""
    if update.effective_user.id != ADMIN_ID:
        return

    keyboard = [
        [InlineKeyboardButton("📊 آخرین دانلودها", callback_data="admin_history"),
         InlineKeyboardButton("📜 وضعیت سیستم (Logs)", callback_data="admin_logs")],
        [InlineKeyboardButton("🧹 پاکسازی فایل‌ها", callback_data="admin_clear"),
         InlineKeyboardButton("🔄 رفرش پنل", callback_data="admin_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "🛠 **پنل مدیریت پیشرفته**\n\nیکی از گزینه‌های زیر را برای نظارت بر عملکرد ربات انتخاب کنید:"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش کلیک روی دکمه‌های مدیریت"""
    query = update.callback_query
    data = query.data
    if update.effective_user.id != ADMIN_ID: return

    if data == "admin_main":
        await admin_panel(update, context)

    elif data == "admin_logs":
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r") as f:
                raw_logs = f.readlines()[-8:] # گرفتن 8 لاگ آخر
                formatted_logs = ""
                for log in raw_logs:
                    if "sendMessage" in log: formatted_logs += "✉️ `پیام ارسال شد`\n"
                    elif "getUpdates" in log: continue # حذف لاگ‌های تکراری پولینگ
                    elif "Application started" in log: formatted_logs += "🟢 `ربات استارت شد`\n"
                    elif "ERROR" in log: formatted_logs += "🔴 `خطا در سیستم`\n"
                
                if not formatted_logs: formatted_logs = "✅ سیستم در وضعیت پایدار است و فعالیت خاصی ثبت نشده."
                
                text = f"📜 **وضعیت لحظه‌ای سیستم:**\n\n{formatted_logs}\n\n🕒 آخرین بروزرسانی: `{datetime.now().strftime('%H:%M:%S')}`"
        else:
            text = "❌ فایل لاگ یافت نشد."
            
        keyboard = [[InlineKeyboardButton("🔄 بروزرسانی", callback_data="admin_logs")],
                    [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == "admin_history":
        text = "📈 **تاریخچه آخرین دانلودها:**\n\n"
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()[-5:]
                text += "".join(lines) if lines else "هنوز دانلودی انجام نشده."
        else:
            text += "تاریخچه خالی است."
        
        keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == "admin_clear":
        # منطق پاکسازی...
        await query.answer("فایل‌های اضافی پاکسازی شدند ✨")
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # بخش پنل مدیریت
    if user_id == ADMIN_ID:
        if text == "📊 آمار دانلودها":
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = "".join(f.readlines()[-10:])
                    await update.message.reply_text(f"📈 **آخرین دانلودها:**\n\n{data or 'خالی'}")
            return
        elif text == "📜 مشاهده لاگ‌ها":
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r") as f:
                    data = "".join(f.readlines()[-15:])
                    await update.message.reply_text(f"📄 **آخرین وضعیت سیستم:**\n\n`{data}`", parse_mode='Markdown')
            return
        elif text == "🧹 پاکسازی تاریخچه":
            if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
            await update.message.reply_text("✅ تاریخچه پاک شد.")
            return
        elif text == "🏠 بازگشت":
            await update.message.reply_text("منوی اصلی", reply_markup=ReplyKeyboardMarkup([["/start"]], resize_keyboard=True))
            return

    # بخش دریافت لینک
    if text.startswith("http"):
        if 'queue' not in context.chat_data: context.chat_data['queue'] = deque()
        context.chat_data['queue'].append(text)
        await update.message.reply_text(f"✅ لینک در صف قرار گرفت. (موقعیت: {len(context.chat_data['queue'])})")
        if not context.chat_data.get('is_working'):
            await start_next_download(update.effective_chat.id, context)

async def start_next_download(chat_id, context):
    if not context.chat_data.get('queue'): return
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

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "pause":
        context.chat_data['status'] = 'paused'
        await query.answer("⏸ توقف موقت")
    elif data == "resume":
        context.chat_data['status'] = 'downloading'
        await query.answer("▶️ ادامه دانلود")
        asyncio.create_task(process_result(update.effective_chat.id, context, 
            await download_task(update.effective_chat.id, context, context.chat_data['current_url'], context.chat_data['current_filename'])))
    elif data == "cancel":
        context.chat_data['status'] = 'cancelled'
        await query.answer("❌ لغو شد")

# --- اجرای اصلی ---

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("🚀 ربات با موفقیت فعال شد...")
    app.run_polling()

if __name__ == '__main__':
    main()
