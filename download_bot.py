from bot_config import TOKEN
import os
import requests
import logging
import time
import urllib.parse
import asyncio
from collections import deque
import yt_dlp
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

MAX_FILE_SIZE = 50 * 1024 * 1024

# --- توابع اصلی ---

def initialize_chat_data(context: ContextTypes.DEFAULT_TYPE):
    if 'queue' not in context.chat_data:
        context.chat_data['queue'] = deque()
    if 'processing' not in context.chat_data:
        context.chat_data['processing'] = False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_html(
        f"سلام {user.mention_html()}! 👋\n\n"
        f"یک لینک مستقیم یا لینکی از سایت‌های پشتیبانی شده (مثل یوتیوب) برای من ارسال کنید.",
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    initialize_chat_data(context)
    url = update.message.text
    context.chat_data['queue'].append(url)
    await update.message.reply_text(f"✅ لینک به صف اضافه شد (موقعیت: {len(context.chat_data['queue'])}).")
    if not context.chat_data.get('processing'):
        asyncio.create_task(process_queue(update.effective_chat.id, context))

async def process_queue(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    initialize_chat_data(context)
    if context.chat_data['processing'] or not context.chat_data['queue']:
        return

    context.chat_data['processing'] = True
    url = context.chat_data['queue'].popleft()
    logger.info(f"شروع پردازش لینک: {url}")
    
    status_message = await context.bot.send_message(chat_id, "⏳ در حال بررسی نوع لینک...")

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    try:
        # --- تشخیص نوع لینک ---
        with requests.head(url, allow_redirects=True, timeout=10, headers=headers) as r:
            r.raise_for_status()
            content_type = r.headers.get('content-type', '').lower()
            
            # اگر لینک مستقیم به یک فایل باشد (نه صفحه وب)
            if 'text/html' not in content_type:
                logger.info("لینک مستقیم شناسایی شد. شروع دانلود با requests...")
                await status_message.delete()
                await download_direct_link(chat_id, context, url, headers)
                return # از ادامه تابع خارج شو چون کار تمام شده

        # اگر لینک مستقیم نبود، با yt-dlp ادامه بده
        logger.info("لینک مستقیم نیست. تلاش برای پردازش با yt-dlp...")
        await status_message.edit_text("⏳ لینک مستقیم نیست، در حال استخراج اطلاعات با yt-dlp...")
        await process_with_yt_dlp(chat_id, context, url, status_message)

    except Exception as e:
        logger.error(f"خطا در پردازش اولیه لینک {url}: {e}")
        await status_message.edit_text(f"❌ در بررسی لینک خطایی رخ داد: {e}")
        context.chat_data['processing'] = False
        asyncio.create_task(process_queue(chat_id, context))

async def download_direct_link(chat_id, context, url, headers):
    """تابع اختصاصی برای دانلود لینک‌های مستقیم"""
    filename = urllib.parse.unquote(url.split('/')[-1].split('?')[0]) or "downloaded_file"
    status_message = await context.bot.send_message(chat_id, f"شروع دانلود فایل مستقیم:\n`{filename}`", parse_mode='Markdown')

    try:
        with requests.get(url, stream=True, headers=headers) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        await status_message.edit_text("📤 در حال آپلود فایل...")
        with open(filename, 'rb') as f:
            await context.bot.send_document(chat_id, document=f)
        await status_message.delete()

    except Exception as e:
        await status_message.edit_text(f"❌ در دانلود فایل مستقیم خطایی رخ داد: {e}")
    finally:
        if os.path.exists(filename):
            os.remove(filename)
        context.chat_data['processing'] = False
        asyncio.create_task(process_queue(chat_id, context))

async def process_with_yt_dlp(chat_id, context, url, status_message):
    """تابع اختصاصی برای پردازش لینک‌ها با yt-dlp"""
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'بدون عنوان')
            thumbnail = info.get('thumbnail')

        context.chat_data['active_url'] = url
        context.chat_data['active_title'] = title

        keyboard = [
            [InlineKeyboardButton("🎬 بهترین ویدیو (MP4)", callback_data='video_best')],
            [InlineKeyboardButton("🎵 بهترین صدا (M4A/Webm)", callback_data='audio_best')],
            [InlineKeyboardButton("🎧 تبدیل به MP3", callback_data='audio_mp3')],
            [InlineKeyboardButton("❌ لغو", callback_data='cancel_info')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        caption = f"**{title}**\n\nلطفاً فرمت مورد نظر خود را برای دانلود انتخاب کنید:"
        if thumbnail:
            await context.bot.delete_message(chat_id, status_message.message_id)
            await context.bot.send_photo(chat_id, photo=thumbnail, caption=caption, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await status_message.edit_text(caption, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"خطا در استخراج اطلاعات yt-dlp: {e}")
        await status_message.edit_text("❌ اطلاعاتی از این لینک یافت نشد. این لینک توسط yt-dlp پشتیبانی نمی‌شود.")
        context.chat_data['processing'] = False
        asyncio.create_task(process_queue(chat_id, context))

# تابع format_selection_callback و بقیه توابع بدون تغییر باقی می‌مانند
# ... (کد کامل شامل format_selection_callback, cancel_active_download, و main در اینجا قرار می‌گیرد)
# ... (برای جلوگیری از تکرار، بقیه کد که تغییری نکرده نمایش داده نمی‌شود)

async def format_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data
    url = context.chat_data.get('active_url')
    title = context.chat_data.get('active_title', 'downloaded_file')
    chat_id = query.message.chat_id
    
    if choice == 'cancel_info':
        await query.edit_message_text("عملیات لغو شد.")
        context.chat_data['processing'] = False
        asyncio.create_task(process_queue(chat_id, context))
        return

    status_message = await query.edit_message_text(f"🚀 در حال آماده‌سازی برای دانلود **{title}**...")
    
    context.chat_data['cancel_download'] = False
    
    last_update_time = 0
    def progress_hook(d):
        nonlocal last_update_time
        if context.chat_data.get('cancel_download'): raise yt_dlp.utils.DownloadError("دانلود توسط کاربر لغو شد.")
        if d['status'] == 'downloading':
            current_time = time.time()
            if current_time - last_update_time > 2:
                total_bytes, downloaded_bytes = d.get('total_bytes_estimate', 0), d.get('downloaded_bytes', 0)
                if total_bytes > 0:
                    percent, speed = downloaded_bytes / total_bytes * 100, d.get('speed', 0) or 0
                    text = f"**در حال دانلود...**\n`{title}`\n\n`{'█' * int(percent/10)}{'░' * (10-int(percent/10))}` {percent:.1f}%\n" \
                           f"`{downloaded_bytes//1024//1024}MB / {total_bytes//1024//1024}MB`\n" \
                           f" سرعت: `{speed//1024} KB/s`"
                    keyboard = [[InlineKeyboardButton("لغو ❌", callback_data='cancel_dl')]]
                    asyncio.create_task(status_message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'))
                    last_update_time = current_time
        elif d['status'] == 'finished': asyncio.create_task(status_message.edit_text(f"✅ دانلود کامل شد. در حال پردازش نهایی..."))

    output_template = f'%(title)s.%(ext)s'
    ydl_opts = {'progress_hooks': [progress_hook],'outtmpl': output_template,'noplaylist': True,'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'}
    if choice == 'audio_best': ydl_opts['format'] = 'bestaudio/best'
    elif choice == 'audio_mp3':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]

    filename = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        if os.path.getsize(filename) > MAX_FILE_SIZE: await status_message.edit_text(f"❌ حجم فایل نهایی ({os.path.getsize(filename)//1024//1024}MB) بیشتر از ۵۰ مگابایت است.")
        else:
            await status_message.edit_text("📤 در حال آپلود فایل...")
            with open(filename, 'rb') as f: await context.bot.send_document(chat_id, document=f)
            await status_message.delete()
    except Exception as e:
        logger.error(f"خطا در دانلود yt-dlp: {e}")
        await status_message.edit_text(f"❌ خطا: {e}")
    finally:
        if filename and os.path.exists(filename): os.remove(filename)
        context.chat_data['processing'] = False
        asyncio.create_task(process_queue(chat_id, context))

async def cancel_active_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['cancel_download'] = True
    await update.callback_query.answer("درخواست لغو ارسال شد...")

def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    application.add_handler(CallbackQueryHandler(format_selection_callback, pattern='^(video_best|audio_best|audio_mp3|cancel_info)$'))
    application.add_handler(CallbackQueryHandler(cancel_active_download, pattern='^cancel_dl$'))
    application.run_polling()

if __name__ == '__main__':
    main()
