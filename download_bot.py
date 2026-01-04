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

# --- تنظیمات ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHUNK_SIZE = 48 * 1024 * 1024  # 48MB برای هر پارت
DOWNLOAD_DIR = "downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# --- توابع کمکی ---

def get_keyboard(status="downloading"):
    """ایجاد دکمه‌های کنترلی براساس وضعیت."""
    if status == "downloading":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("توقف موقت ⏸", callback_data="pause"),
             InlineKeyboardButton("لغو کامل ❌", callback_data="cancel")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("ادامه دانلود ▶️", callback_data="resume"),
             InlineKeyboardButton("لغو کامل ❌", callback_data="cancel")]
        ])

def split_file(file_path, chunk_size):
    file_list = []
    part_num = 1
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk: break
            part_name = f"{file_path}.part{part_num}"
            with open(part_name, 'wb') as p: p.write(chunk)
            file_list.append(part_name)
            part_num += 1
    return file_list

# --- هسته اصلی دانلود ---

async def download_task(chat_id, context, url, filename):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    
    # تعیین نقطه شروع (برای Resume)
    downloaded_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    headers = {"User-Agent": "Mozilla/5.0", "Range": f"bytes={downloaded_size}-"}

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416: # محدوده نادرست (احتمالا دانلود تمام شده)
                    total_size = downloaded_size
                elif response.status_code in (200, 206):
                    total_size = int(response.headers.get("Content-Length", 0)) + downloaded_size
                    
                    mode = "ab" if downloaded_size > 0 else "wb"
                    with open(file_path, mode) as f:
                        last_update = 0
                        async for chunk in response.iter_bytes(chunk_size=16384):
                            if chat_data.get('status') == 'paused':
                                return "paused"
                            if chat_data.get('status') == 'cancelled':
                                return "cancelled"
                            
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            
                            # بروزرسانی نوار پیشرفت هر 3 ثانیه (برای جلوگیری از Flood تلگرام)
                            if time.time() - last_update > 3:
                                await update_progress(chat_id, context, filename, downloaded_size, total_size)
                                last_update = time.time()
                else:
                    return f"خطای سرور: {response.status_code}"

        return "completed"

    except Exception as e:
        logger.error(f"Download error: {e}")
        return str(e)

async def update_progress(chat_id, context, filename, downloaded, total):
    percent = (downloaded / total * 100) if total > 0 else 0
    bar = "█" * int(percent / 10) + "░" * (10 - int(percent / 10))
    text = f"📥 **در حال دانلود...**\n`{filename}`\n\n`{bar}` {percent:.1f}%\n📦 {downloaded//1048576} / {total//1048576} MB"
    
    try:
        await context.bot.edit_message_text(
            text, chat_id, context.chat_data['msg_id'], 
            reply_markup=get_keyboard("downloading"), parse_mode='Markdown'
        )
    except: pass

# --- هندلرهای دستورات ---

async def handle_new_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not url.startswith("http"): return
    
    if 'queue' not in context.chat_data: context.chat_data['queue'] = deque()
    context.chat_data['queue'].append(url)
    
    await update.message.reply_text(f"✅ لینک به صف اضافه شد. تعداد در صف: {len(context.chat_data['queue'])}")
    
    if not context.chat_data.get('is_working'):
        await start_next_download(update.effective_chat.id, context)

async def start_next_download(chat_id, context):
    if not context.chat_data['queue']:
        context.chat_data['is_working'] = False
        return

    context.chat_data['is_working'] = True
    url = context.chat_data['queue'].popleft()
    context.chat_data['status'] = 'downloading'
    context.chat_data['current_url'] = url
    
    # حدس نام فایل
    filename = urllib.parse.unquote(url.split('/')[-1].split('?')[0]) or "file"
    context.chat_data['current_filename'] = filename
    
    msg = await context.bot.send_message(chat_id, f"⏳ شروع دانلود: {filename}", reply_markup=get_keyboard())
    context.chat_data['msg_id'] = msg.message_id

    result = await download_task(chat_id, context, url, filename)
    await process_result(chat_id, context, result)

async def process_result(chat_id, context, result):
    filename = context.chat_data['current_filename']
    file_path = os.path.join(DOWNLOAD_DIR, filename)

    if result == "completed":
        await context.bot.edit_message_text("✅ دانلود تمام شد. در حال بررسی برای آپلود...", chat_id, context.chat_data['msg_id'])
        
        if os.path.getsize(file_path) > CHUNK_SIZE:
            parts = split_file(file_path, CHUNK_SIZE)
            for i, p in enumerate(parts):
                await context.bot.send_document(chat_id, document=open(p, 'rb'), caption=f"Part {i+1}")
                os.remove(p)
        else:
            await context.bot.send_document(chat_id, document=open(file_path, 'rb'))
        
        if os.path.exists(file_path): os.remove(file_path)
        await start_next_download(chat_id, context)

    elif result == "paused":
        await context.bot.edit_message_text(f"⏸ دانلود متوقف شد: `{filename}`", chat_id, context.chat_data['msg_id'], 
                                            reply_markup=get_keyboard("paused"), parse_mode='Markdown')
    
    elif result == "cancelled":
        if os.path.exists(file_path): os.remove(file_path)
        await context.bot.edit_message_text("❌ عملیات لغو شد.", chat_id, context.chat_data['msg_id'])
        await start_next_download(chat_id, context)

# --- کال‌بک دکمه‌ها ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id

    if data == "pause":
        context.chat_data['status'] = 'paused'
        await query.answer("توقف موقت...")
    
    elif data == "resume":
        context.chat_data['status'] = 'downloading'
        await query.answer("ادامه دانلود...")
        # اجرای مجدد تسک دانلود
        asyncio.create_task(process_result(chat_id, context, 
            await download_task(chat_id, context, context.chat_data['current_url'], context.chat_data['current_filename'])))
    
    elif data == "cancel":
        context.chat_data['status'] = 'cancelled'
        await query.answer("لغو دانلود...")

# --- اجرای ربات ---

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_link))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == '__main__':
    main()
