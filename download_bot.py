import os
import time
import asyncio
import httpx
import logging
import urllib.parse
from collections import deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, CallbackQueryHandler
)
from bot_config import TOKEN

# --- تنظیمات اولیه ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

CHUNK_SIZE = 48 * 1024 * 1024  # پارت‌های 48 مگابایتی برای آپلود تلگرام
DOWNLOAD_DIR = "downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# --- توابع کمکی برای زیبایی و محاسبات ---

def human_readable_size(size, decimal_places=2):
    """تبدیل بایت به حجم قابل خواندن (MB, GB)"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"

def get_progress_bar(percent):
    """ساخت نوار پیشرفت بصری"""
    done = int(percent / 10)
    remain = 10 - done
    return "🔹" * done + "🔸" * remain

def get_keyboard(status="downloading"):
    """دکمه‌های کنترلی"""
    if status == "downloading":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⏸ توقف", callback_data="pause"),
             InlineKeyboardButton("❌ لغو", callback_data="cancel")]
        ])
    elif status == "paused":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ ادامه", callback_data="resume"),
             InlineKeyboardButton("❌ لغو", callback_data="cancel")]
        ])
    return None

# --- منطق پارت‌بندی فایل ---

def split_file(file_path):
    file_list = []
    file_size = os.path.getsize(file_path)
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

# --- هسته اصلی دانلود با نمایش سرعت و ETA ---

async def download_task(chat_id, context, url, filename):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    
    downloaded_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    headers = {"Range": f"bytes={downloaded_size}-"}
    
    start_time = time.time()
    last_update_time = time.time()

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416: 
                    total_size = downloaded_size
                elif response.status_code in (200, 206):
                    # اگر فایل جدید است، حجم کل را بگیر، اگر ادامه است، حجم باقیمانده + قبلی
                    total_size = int(response.headers.get("Content-Length", 0)) + downloaded_size
                    mode = "ab" if downloaded_size > 0 else "wb"
                    
                    with open(file_path, mode) as f:
                        # تغییر اصلی اینجاست: استفاده از aiter_bytes به جای iter_bytes
                        async for chunk in response.aiter_bytes(chunk_size=32768):
                            if chat_data.get('status') == 'paused': return "paused"
                            if chat_data.get('status') == 'cancelled': return "cancelled"
                            
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            
                            now = time.time()
                            if now - last_update_time > 2.0:
                                diff = now - start_time
                                # محاسبه سرعت از لحظه شروع این نشست
                                session_downloaded = downloaded_size - (os.path.getsize(file_path) if mode=="ab" else 0)
                                speed = session_downloaded / diff if diff > 0 else 0
                                percent = (downloaded_size / total_size) * 100 if total_size > 0 else 0
                                eta = (total_size - downloaded_size) / speed if speed > 0 else 0
                                
                                await update_ui(chat_id, context, filename, downloaded_size, total_size, percent, speed, eta)
                                last_update_time = now
                else:
                    return f"خطای سرور: {response.status_code}"
        return "completed"
    except Exception as e:
        logger.error(f"Download Error: {e}")
        return str(e)
async def update_ui(chat_id, context, filename, downloaded, total, percent, speed, eta):
    bar = get_progress_bar(percent)
    text = (
        f"🚀 **در حال دانلود با سرعت بالا...**\n\n"
        f"📦 **فایل:** `{filename}`\n"
        f"📊 **پیشرفت:** `{percent:.1f}%`\n"
        f"{bar}\n\n"
        f"⚡ **سرعت:** `{human_readable_size(speed)}/s`\n"
        f"📥 **حجم:** `{human_readable_size(downloaded)} / {human_readable_size(total)}`\n"
        f"⏱ **زمان باقی‌مانده:** `{int(eta)} ثانیه`"
    )
    try:
        await context.bot.edit_message_text(text, chat_id, context.chat_data['msg_id'], 
                                            reply_markup=get_keyboard("downloading"), parse_mode='Markdown')
    except: pass

# --- مدیریت صف و آپلود ---

async def handle_new_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not url.startswith("http"): return
    
    if 'queue' not in context.chat_data: context.chat_data['queue'] = deque()
    context.chat_data['queue'].append(url)
    
    await update.message.reply_text(f"✅ به صف اضافه شد. (موقعیت: {len(context.chat_data['queue'])})")
    
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
    
    filename = urllib.parse.unquote(url.split('/')[-1].split('?')[0]) or "file_download"
    context.chat_data['current_filename'] = filename
    
    msg = await context.bot.send_message(chat_id, "🔍 در حال اتصال به لینک...", parse_mode='Markdown')
    context.chat_data['msg_id'] = msg.message_id

    result = await download_task(chat_id, context, url, filename)
    await process_result(chat_id, context, result)

async def process_result(chat_id, context, result):
    filename = context.chat_data['current_filename']
    file_path = os.path.join(DOWNLOAD_DIR, filename)

    if result == "completed":
        await context.bot.edit_message_text("✅ دانلود ۱۰۰٪ تمام شد. در حال ارسال به تلگرام... 📤", chat_id, context.chat_data['msg_id'])
        
        file_size = os.path.getsize(file_path)
        if file_size > CHUNK_SIZE:
            parts = split_file(file_path)
            for i, p in enumerate(parts):
                await context.bot.send_document(chat_id, document=open(p, 'rb'), caption=f"Part {i+1} of {len(parts)}")
                os.remove(p)
        else:
            await context.bot.send_document(chat_id, document=open(file_path, 'rb'), caption=f"✅ {filename}")
        
        if os.path.exists(file_path): os.remove(file_path)
        await context.bot.delete_message(chat_id, context.chat_data['msg_id'])
        await start_next_download(chat_id, context)

    elif result == "paused":
        await context.bot.edit_message_text(f"⏸ **دانلود متوقف شد.**\nفایل: `{filename}`", chat_id, context.chat_data['msg_id'], 
                                            reply_markup=get_keyboard("paused"), parse_mode='Markdown')
    elif result == "cancelled":
        if os.path.exists(file_path): os.remove(file_path)
        await context.bot.edit_message_text("❌ عملیات لغو شد و فایل حذف گردید.", chat_id, context.chat_data['msg_id'])
        await start_next_download(chat_id, context)
    else:
        await context.bot.send_message(chat_id, f"❌ خطا: {result}")
        await start_next_download(chat_id, context)

# --- مدیریت دکمه‌ها ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id

    if data == "pause":
        context.chat_data['status'] = 'paused'
        await query.answer("متوقف شد.")
    elif data == "resume":
        context.chat_data['status'] = 'downloading'
        await query.answer("ادامه دانلود...")
        asyncio.create_task(process_result(chat_id, context, 
            await download_task(chat_id, context, context.chat_data['current_url'], context.chat_data['current_filename'])))
    elif data == "cancel":
        context.chat_data['status'] = 'cancelled'
        await query.answer("لغو شد.")

# --- دستور شروع ---

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 سلام! من ربات دانلودر پیشرفته هستم.\n\n"
        "✨ ویژگی‌ها:\n"
        "🔹 سرعت بالا\n"
        "🔹 پارت‌بندی خودکار\n"
        "🔹 قابلیت توقف و ادامه\n\n"
        "لینک مستقیم خود را ارسال کنید تا شروع کنیم! 👇"
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_link))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("ربات روشن شد...")
    app.run_polling()

if __name__ == '__main__':
    main()
