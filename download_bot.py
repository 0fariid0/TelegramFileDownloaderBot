from bot_config import TOKEN
import os
import requests
import logging
import time
import urllib.parse
import asyncio
import math # برای محاسبات تعداد پارت‌ها
from collections import deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler
)

# --- راه‌اندازی اولیه ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# محدودیت آپلود تلگرام 50 مگابایت است
CHUNK_SIZE = 48 * 1024 * 1024  # هر پارت را 48 مگابایت در نظر می‌گیریم تا حاشیه امنیت داشته باشد
MAX_TOTAL_DOWNLOAD = 2000 * 1024 * 1024 # محدودیت کلی دانلود (مثلاً 2 گیگابایت)

# --- توابع کمکی جدید ---

def split_file(file_path, chunk_size):
    """فایل را به قطعات کوچک‌تر تقسیم می‌کند."""
    file_list = []
    file_size = os.path.getsize(file_path)
    part_num = 1
    
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            
            part_name = f"{file_path}.part{part_num}"
            with open(part_name, 'wb') as p:
                p.write(chunk)
            
            file_list.append(part_name)
            part_num += 1
            
    return file_list

# --- توابع اصلی ربات ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        f"سلام {user.mention_html()}! 👋\n\n"
        f"لینک مستقیم خود را بفرستید. فایل‌های بزرگتر از ۵۰ مگابایت به صورت خودکار پارت‌بندی می‌شوند.",
    )

def initialize_chat_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    if 'download_queue' not in context.chat_data:
        context.chat_data['download_queue'] = deque()
    if 'is_downloading' not in context.chat_data:
        context.chat_data['is_downloading'] = False

async def handle_new_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    initialize_chat_data(context)
    url = update.message.text

    if not url.startswith(('http://', 'https://')):
        await update.message.reply_text("❌ این یک لینک معتبر به نظر نمی‌رسد.")
        return

    context.chat_data['download_queue'].append(url)
    queue_position = len(context.chat_data['download_queue'])
    await update.message.reply_text(
        f"✅ لینک به صف اضافه شد (موقعیت: {queue_position})."
    )

    if not context.chat_data.get('is_downloading', False):
        asyncio.create_task(process_queue(update.effective_chat.id, context))

async def process_queue(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    initialize_chat_data(context)
    if context.chat_data['is_downloading'] or not context.chat_data['download_queue']:
        return

    context.chat_data['is_downloading'] = True
    url = context.chat_data['download_queue'].popleft()
    status_message = await context.bot.send_message(chat_id, f"⏳ در حال بررسی لینک...", parse_mode='Markdown')
    
    filename = "downloaded_file"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        # بررسی حجم فایل قبل از شروع
        with requests.head(url, allow_redirects=True, timeout=10, headers=headers) as r:
            r.raise_for_status()
            content_length = r.headers.get('content-length')
            if content_length and int(content_length) > MAX_TOTAL_DOWNLOAD:
                raise ValueError("حجم فایل از حد مجاز ربات (2 گیگابایت) بیشتر است.")

            # استخراج نام فایل
            if "content-disposition" in r.headers:
                cd = r.headers.get('content-disposition')
                if 'filename=' in cd: filename = urllib.parse.unquote(cd.split('filename=')[-1].strip(' "'))
            if filename == "downloaded_file":
                filename = urllib.parse.unquote(url.split('/')[-1].split('?')[0]) or "downloaded_file"

        # شروع دانلود
        with requests.get(url, stream=True, timeout=60, headers=headers) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            downloaded_size = 0
            last_update_time = 0
            
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if context.chat_data.get('cancel_download', False):
                        raise asyncio.CancelledError("لغو شد.")
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    if time.time() - last_update_time > 3:
                        await update_progress(status_message, downloaded_size, total_size, filename)
                        last_update_time = time.time()

        # بررسی برای پارت‌بندی
        final_size = os.path.getsize(filename)
        if final_size > CHUNK_SIZE:
            await status_message.edit_text(f"📦 حجم فایل ({final_size // (1024*1024)}MB) بیشتر از حد مجاز است. در حال پارت‌بندی...")
            parts = split_file(filename, CHUNK_SIZE)
            
            for i, part in enumerate(parts):
                await status_message.edit_text(f"📤 در حال آپلود پارت {i+1} از {len(parts)}...")
                with open(part, 'rb') as f:
                    await context.bot.send_document(chat_id=chat_id, document=f, caption=f"Part {i+1}")
                os.remove(part) # حذف هر پارت بلافاصله بعد از آپلود
        else:
            await status_message.edit_text("✅ در حال آپلود فایل...")
            with open(filename, 'rb') as f:
                await context.bot.send_document(chat_id=chat_id, document=f)

        await status_message.delete()

    except Exception as e:
        logger.error(f"Error: {e}")
        await context.bot.send_message(chat_id, f"❌ خطا: {e}")
    finally:
        if os.path.exists(filename):
            os.remove(filename)
        context.chat_data['is_downloading'] = False
        asyncio.create_task(process_queue(chat_id, context))

async def update_progress(message, downloaded, total, filename):
    if total <= 0: return
    percent = (downloaded / total) * 100
    bar = "█" * int(percent / 10) + "░" * (10 - int(percent / 10))
    text = f"**در حال دانلود...**\n`{filename}`\n\n`{bar}` {percent:.1f}%\n{downloaded // 1048576} / {total // 1048576} MB"
    try:
        await message.edit_text(text, parse_mode='Markdown')
    except: pass

async def cancel_download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.chat_data['cancel_download'] = True
    await update.callback_query.answer("درخواست لغو ثبت شد.")

def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_link))
    application.add_handler(CallbackQueryHandler(cancel_download_callback, pattern='^cancel_download$'))
    application.run_polling()

if __name__ == '__main__':
    main()
