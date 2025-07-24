from bot_config import TOKEN
import os
import requests
import logging
import time
import urllib.parse
import asyncio
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

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# --- توابع اصلی ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دستور /start را مدیریت می‌کند."""
    user = update.effective_user
    await update.message.reply_html(
        f"سلام {user.mention_html()}! 👋\n\n"
        f"می‌توانید یک یا چند لینک مستقیم برای دانلود ارسال کنید. من آنها را در صف قرار داده و یکی پس از دیگری دانلود خواهم کرد.",
    )

def initialize_chat_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    """اطلاعات اولیه مورد نیاز برای هر چت را مقداردهی می‌کند."""
    if 'download_queue' not in context.chat_data:
        context.chat_data['download_queue'] = deque()
    if 'is_downloading' not in context.chat_data:
        context.chat_data['is_downloading'] = False

async def handle_new_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """لینک جدید را به صف اضافه کرده و در صورت بیکار بودن، پردازش را شروع می‌کند."""
    initialize_chat_data(context)
    url = update.message.text

    if not url.startswith(('http://', 'https://')):
        await update.message.reply_text("❌ این یک لینک معتبر به نظر نمی‌رسد.")
        return

    context.chat_data['download_queue'].append(url)
    queue_position = len(context.chat_data['download_queue'])
    await update.message.reply_text(
        f"✅ لینک به صف اضافه شد (موقعیت شما در صف: {queue_position})."
    )

    if not context.chat_data.get('is_downloading', False):
        asyncio.create_task(process_queue(update.effective_chat.id, context))

async def process_queue(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """اولین آیتم در صف را پردازش (دانلود) می‌کند."""
    initialize_chat_data(context)

    if context.chat_data['is_downloading']:
        return

    if not context.chat_data['download_queue']:
        logger.info("صف دانلود خالی است. پردازش متوقف شد.")
        return

    context.chat_data['is_downloading'] = True
    url = context.chat_data['download_queue'].popleft()
    
    logger.info(f"شروع پردازش لینک از صف: {url}")

    status_message = await context.bot.send_message(chat_id, f"⏳ در حال پردازش لینک:\n`{url}`", parse_mode='Markdown')
    
    filename = "downloaded_file"
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        
        with requests.head(url, allow_redirects=True, timeout=10, headers=headers) as r:
            r.raise_for_status()
            content_length = r.headers.get('content-length')
            if content_length and int(content_length) > MAX_FILE_SIZE:
                raise ValueError(f"حجم فایل بیشتر از {MAX_FILE_SIZE // 1024 // 1024} مگابایت است.")

            if "content-disposition" in r.headers:
                cd = r.headers.get('content-disposition')
                if 'filename=' in cd: filename = urllib.parse.unquote(cd.split('filename=')[-1].strip(' "'))
            if filename == "downloaded_file":
                filename = urllib.parse.unquote(url.split('/')[-1].split('?')[0]) or "downloaded_file"

        context.chat_data['cancel_download'] = False
        keyboard = [[InlineKeyboardButton("لغو عملیات ❌", callback_data='cancel_download')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await status_message.edit_text(f"شروع دانلود فایل:\n`{filename}`", reply_markup=reply_markup, parse_mode='Markdown')


        with requests.get(url, stream=True, timeout=60, headers=headers) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            downloaded_size = 0
            last_update_time = 0
            
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if context.chat_data.get('cancel_download', False):
                        raise asyncio.CancelledError("دانلود توسط کاربر لغو شد.")
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    current_time = time.time()
                    if total_size > 0 and current_time - last_update_time > 2:
                        await update_progress(status_message, downloaded_size, total_size, filename)
                        last_update_time = current_time

        await status_message.edit_text("✅ دانلود کامل شد. در حال آپلود فایل...")
        with open(filename, 'rb') as f:
            await context.bot.send_document(chat_id=chat_id, document=f)
        await status_message.delete()

    except asyncio.CancelledError as e:
        await status_message.edit_text(f"❌ {e}")
    except Exception as e:
        logger.error(f"خطا در پردازش {url}: {e}")
        await status_message.edit_text(f"متاسفانه در دانلود این فایل مشکلی پیش آمد:\n`{e}`")
    finally:
        if os.path.exists(filename):
            os.remove(filename)
        context.chat_data['is_downloading'] = False
        # بلافاصله پردازش آیتم بعدی در صف را شروع کن
        asyncio.create_task(process_queue(chat_id, context))

async def update_progress(message, downloaded, total, filename):
    """پیام نوار پیشرفت را به‌روزرسانی می‌کند."""
    percent = (downloaded / total) * 100
    progress_bar = "█" * int(percent / 10) + "░" * (10 - int(percent / 10))
    text = (
        f"**در حال دانلود...**\n"
        f"`{filename}`\n\n"
        f"`{progress_bar}` {percent:.1f}%\n"
        f"`{downloaded // 1024 // 1024}MB / {total // 1024 // 1024}MB`"
    )
    keyboard = [[InlineKeyboardButton("لغو عملیات ❌", callback_data='cancel_download')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception:
        pass # از خطای Message is not modified جلوگیری می‌کند

async def cancel_download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """عملیات دانلود فعلی را لغو می‌کند."""
    context.chat_data['cancel_download'] = True
    query = update.callback_query
    await query.answer("در حال ارسال درخواست لغو...")

def main() -> None:
    """ربات را راه‌اندازی و اجرا می‌کند."""
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_link))
    application.add_handler(CallbackQueryHandler(cancel_download_callback, pattern='^cancel_download$'))

    application.run_polling()

if __name__ == '__main__':
    main()
